"""Свободный текст (и голос) → агент → действие. Регистрируется последним в bot/app.py,
чтобы команды и FSM перехватывали свои события раньше."""
import json
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import OwnerOnly
from src.config import settings as app_settings
from src.core.auth import check_user_permission
from src.core.agent import route_intent
from src.core.chat_service import load_chat
from src.core.commitment_extractor import extract_and_save_commitments
from src.core.contact_resolver import resolve
from src.core.news import build_news_digest
from src.core.summarizer import catchup, draft_reply, summarize_chat
from src.core import conversation_context as ctx_store
from src.core.text_sanitizer import sanitize_html
from src.core.timeutil import fmt_local, is_valid_tz, now_in_tz, tz_short
from src.core.transcription import transcription_service
from src.bot.filters import get_team_for_event
from src.db.repo import (
    add_commitment,
    add_news_topic,
    create_meeting,
    create_pending_action,
    delete_news_topic,
    get_api_key,
    get_contact,
    get_or_create_user,
    get_team_by_chat,
    get_team_member,
    list_news_topics,
    list_open_commitments,
    list_trashed_commitments,
    restore_commitment,
    trash_commitment,
    update_commitment_status,
    upsert_api_key,
    upsert_contact,
)
from src.db.session import get_session
from src.bot.lexicon import L
from src.bot.states import TaskCreationStates
from src.llm.router import get_provider_chain
from src.userbot.manager import UserbotManager


logger = logging.getLogger(__name__)

# Pending задачи ожидающие выбора исполнителя
# ключ: chat_id, значение: dict с данными задачи
_pending_assignee_selection: dict[int, dict] = {}


def _build_yougile_user_keyboard(
    users: list[dict],
    chat_id: int,
    page: int = 0,
    page_size: int = 8,
    prefix: str = "yg_assign",
) -> InlineKeyboardMarkup:
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()

    start = page * page_size
    page_users = users[start:start + page_size]

    for u in page_users:
        name = (u.get("name") or u.get("email", u.get("id", "?")))[:20]
        uid = u.get("id", "")
        cb = f"{prefix}:{chat_id}:{uid}"
        if len(cb) > 64:
            max_uid = 64 - len(f"{prefix}:{chat_id}:")
            cb = f"{prefix}:{chat_id}:{uid[:max_uid]}"
        kb.button(text=name, callback_data=cb)

    kb.adjust(1)

    page_prefix = prefix.replace("assign", "page")
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=f"{page_prefix}:{chat_id}:{page - 1}"
            )
        )
    if start + page_size < len(users):
        nav_buttons.append(
            InlineKeyboardButton(
                text="▶️ Далее",
                callback_data=f"{page_prefix}:{chat_id}:{page + 1}"
            )
        )
    if nav_buttons:
        kb.row(*nav_buttons)

    kb.row(InlineKeyboardButton(
        text="❌ Без исполнителя",
        callback_data=f"{prefix}:{chat_id}:none"
    ))

    return kb.as_markup()
router = Router(name="free_text")
router.message.filter(OwnerOnly())


CHAT_LOAD_LIMIT = 50


# Поля UserSettings, которые агент может менять через set_setting (имя → тип значения)
SETTING_FIELDS: dict[str, str] = {
    "auto_reply_enabled":         "bool",
    "auto_reply_mode":            "choice:static,smart",
    "auto_reply_text":            "str",
    "auto_reply_cooldown_min":    "int",
    "digest_enabled":             "bool",
    "digest_time":                "hm",
    "news_enabled":               "bool",
    "news_digest_time":           "hm",
    "news_window_hours":          "int",
    "reminders_enabled":          "bool",
    "reminder_lead_hours":        "int",
    "reminder_overdue_enabled":   "bool",
    "ignore_archived":            "bool",
    "use_heavy_model":            "bool",
    "llm_provider":               "choice:openai,gemini,gigachat,groq",
    "transcription_mode":         "choice:local,api,hybrid",
    "timezone":                   "tz",
    "mtslink_token":              "token",
}


import re
_HM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _coerce_setting_value(spec: str, raw):
    if spec == "bool":
        if isinstance(raw, bool):
            return raw, None
        if isinstance(raw, str) and raw.lower() in {"true", "yes", "on", "вкл", "1"}:
            return True, None
        if isinstance(raw, str) and raw.lower() in {"false", "no", "off", "выкл", "0"}:
            return False, None
        return None, "ожидаю true/false"
    if spec == "int":
        try:
            return int(raw), None
        except (TypeError, ValueError):
            return None, "ожидаю целое число"
    if spec == "str":
        if not isinstance(raw, str) or not raw.strip():
            return None, "ожидаю строку"
        return raw.strip(), None
    if spec == "hm":
        if isinstance(raw, str) and _HM_RE.match(raw.strip()):
            return raw.strip(), None
        return None, "ожидаю время в формате HH:MM"
    if spec == "tz":
        if isinstance(raw, str) and is_valid_tz(raw.strip()):
            return raw.strip(), None
        return None, "не нашёл такой IANA timezone"
    if spec == "token":
        if isinstance(raw, str) and raw.strip():
            return raw.strip(), None
        return None, "ожидаю строку с токеном"
    if spec.startswith("choice:"):
        opts = set(spec[len("choice:"):].split(","))
        if isinstance(raw, str) and raw.strip() in opts:
            return raw.strip(), None
        return None, f"допустимые значения: {', '.join(sorted(opts))}"
    return None, "неизвестный тип"


def _confirm_keyboard(action_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Отправить", callback_data=f"send:confirm:{action_id}"),
        InlineKeyboardButton(text="✏ Изменить", callback_data=f"send:edit:{action_id}"),
    )
    kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data=f"send:cancel:{action_id}"))
    return kb.as_markup()


def _candidates_keyboard_send(candidates):
    kb = InlineKeyboardBuilder()
    for c in candidates:
        kb.row(InlineKeyboardButton(
            text=f"{c.label()} · {c.score}",
            callback_data=f"send:pick:{c.peer_id}",
        ))
    kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="send:cancel:0"))
    return kb.as_markup()


def _candidates_keyboard_chat(action: str, candidates):
    # action ∈ {summary, tasks, draft, catchup} — re-use chat:* callback'ов из chat_cmd
    kb = InlineKeyboardBuilder()
    for c in candidates:
        kb.row(InlineKeyboardButton(
            text=f"{c.label()} · {c.score}",
            callback_data=f"chat:{action}:{c.peer_id}",
        ))
    kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="chat:cancel:0"))
    return kb.as_markup()


async def _exec_kanban_intent(intent: dict, message: Message) -> None:
    """Исполнитель канбан-интентов (create_task, show_boards, move_task, smalltalk).
    Читает поля как из плоского JSON-формата (основной AGENT_SYSTEM),
    так и из вложенного parameters (формат KanbanIntentResponse)."""
    kind = intent.get("intent")
    from src.bot.handlers.yougile import YouGileClient

    async with get_session() as session:
        team = await get_team_for_event(session, message)

    if not team or not team.kanban_token:
        await message.answer("⚠️ Доска для задач не выбрана. Пожалуйста, выберите нужную доску в настройках команды, чтобы я мог создавать карточки.")
        return

    board_id = team.active_board_id or team.kanban_board_id
    if not board_id or len(board_id) < 10:
        await message.answer(
            "⚠️ Доска не настроена корректно. "
            "Выбери доску через /kanban_board."
        )
        return

    client = YouGileClient(team.kanban_token, board_id)

    def _get(key: str, default: str = "") -> str:
        val = intent.get(key) or intent.get("parameters", {}).get(key) or default
        return str(val).strip() if val else default

    if kind == "create_task":
        title = _get("title")
        if not title:
            await message.answer("❓ Не понял название задачи. Уточни.")
            return
        description = _get("description")
        column_query = _get("column")
        deadline_raw = _get("deadline") or None
        assignee_name = _get("assignee") or None
        try:
            columns = await client.get_columns()
        except Exception as e:
            await message.answer(f"❌ Ошибка при получении колонок: {e}")
            return
        if column_query:
            col = next((c for c in columns if column_query.lower() in c.get("title", "").lower()), None)
            if not col:
                names = ", ".join(c.get("title", "?") for c in columns)
                await message.answer(f"❓ Колонка «{column_query}» не найдена. Доступны: {names}")
                return
            column_id = col["id"]
        else:
            column_id = columns[0]["id"] if columns else None
            if not column_id:
                await message.answer("❌ На доске нет колонок.")
                return
        assignee_ids = None
        if assignee_name:
            # Шаг 1: проверить локальные алиасы (быстро, без запроса к YouGile)
            async with get_session() as session:
                from src.db.repo import resolve_alias
                uid = await resolve_alias(session, team.id, assignee_name)

            if not uid:
                # Шаг 2: поиск в YouGile API
                uid = await client.resolve_user_by_name(assignee_name)

            if uid:
                assignee_ids = [uid]
            else:
                # Шаг 3: показать список для выбора + запомнить
                try:
                    users = await client.get_users()
                except Exception as e:
                    logger.warning("get_users failed: %s", e)
                    users = []
                if users:
                    _pending_assignee_selection[message.chat.id] = {
                        "title": title,
                        "description": description,
                        "column_id": column_id,
                        "deadline": deadline_raw,
                        "team_id": team.id,
                        "kanban_token": team.kanban_token,
                        "board_id": board_id,
                        "users": users,
                        "original_name": assignee_name,
                    }
                    kb = _build_yougile_user_keyboard(users, message.chat.id)
                    await message.answer(
                        f"❓ Исполнитель «{assignee_name}» не найден в YouGile.\n"
                        f"Выбери кто это из списка — я запомню привязку:",
                        reply_markup=kb,
                    )
                    return
                else:
                    await message.answer(
                        f"⚠️ Исполнитель «{assignee_name}» не найден. "
                        f"Задача создастся без исполнителя."
                    )
        try:
            await client.create_card(title, description, column_id,
                                     assignee_ids=assignee_ids,
                                     deadline=deadline_raw)
        except Exception as e:
            await message.answer(f"❌ Ошибка при создании задачи: {e}")
            return
        board_name = team.active_board_name or "по умолчанию"
        tail = ""
        if assignee_ids:
            tail += f"\n👤 Исполнитель: {assignee_name}"
        elif assignee_name:
            tail += f"\n⚠️ Исполнитель «{assignee_name}» не найден."
        if deadline_raw:
            tail += f"\n📅 Дедлайн: {deadline_raw}"
        await message.answer(f"✅ Задача «{title}» создана!\n📋 Доска: {board_name}{tail}")

    elif kind == "show_boards":
        try:
            columns = await client.get_columns()
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")
            return
        parts = ["📊 <b>Канбан-доска</b>\n"]
        for col in columns:
            try:
                cards = await client.get_cards_in_column(col["id"], limit=10)
            except Exception:
                cards = []
            col_name = col.get("title", "?")
            parts.append(f"<b>{col_name}</b> ({len(cards)}):")
            for card in cards[:10]:
                parts.append(f"  • {card.get('title', '?')[:40]}")
            if len(cards) > 10:
                parts.append(f"  … и {len(cards) - 10} ещё")
            parts.append("")
        await message.answer("\n".join(parts)[:4000])

    elif kind == "move_task":
        task_query = _get("task_title")
        target_column = _get("target_column")
        if not task_query or not target_column:
            await message.answer("❓ Укажи, какую задачу и в какую колонку переместить.")
            return
        try:
            columns = await client.get_columns()
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")
            return
        col = next((c for c in columns if target_column.lower() in c.get("title", "").lower()), None)
        if not col:
            names = ", ".join(c.get("title", "?") for c in columns)
            await message.answer(f"❓ Колонка «{target_column}» не найдена. Доступны: {names}")
            return
        card_id = None
        for c in columns:
            try:
                cards = await client.get_cards_in_column(c["id"], limit=50)
            except Exception:
                continue
            card = next(
                (card for card in cards if task_query.lower() in card.get("title", "").lower()),
                None,
            )
            if card:
                card_id = card["id"]
                break
        if not card_id:
            await message.answer(f"❓ Не нашёл задачу «{task_query}» на доске.")
            return
        try:
            await client.move_card(card_id, col["id"])
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")
            return
        await message.answer(f"✅ Задача «{task_query}» перемещена в «{target_column}»!")

    elif kind == "update_kanban_card":
        task_query = _get("task_title")
        target_column = _get("target_column")
        if not task_query or not target_column:
            await message.answer("❓ Укажи задачу и статус (колонку).")
            return
        try:
            columns = await client.get_columns()
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")
            return
        col = next((c for c in columns if target_column.lower() in c.get("title", "").lower()), None)
        if not col:
            names = ", ".join(c.get("title", "?") for c in columns)
            await message.answer(f"❓ Колонка «{target_column}» не найдена. Доступны: {names}")
            return
        card_id = None
        for c in columns:
            try:
                cards = await client.get_cards_in_column(c["id"], limit=50)
            except Exception:
                continue
            card = next((card for card in cards if task_query.lower() in card.get("title", "").lower()), None)
            if card:
                card_id = card["id"]
                break
        if not card_id:
            await message.answer(f"❓ Не нашёл задачу «{task_query}».")
            return
        try:
            await client.move_card(card_id, col["id"])
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")
            return
        await message.answer(f"✅ «{task_query}» → <b>{col.get('title')}</b>")

    elif kind == "smalltalk":
        reply = _get("reply", "Готов помочь с канбан-доской!")
        await message.answer(reply)


async def _execute_intent(intent, message, state, userbot_manager, *, tz_name: str) -> None:
    kind = intent.get("intent")
    client = userbot_manager.get_client(message.from_user.id)

    # selectin-loaded settings/api_keys доступны после закрытия сессии
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        providers = await get_provider_chain(session, owner)
        heavy = owner.settings.use_heavy_model

    if kind in ("create_task", "show_boards", "move_task", "update_kanban_card", "smalltalk"):
        await _exec_kanban_intent(intent, message)
        return

    if kind == "chat":
        reply = sanitize_html(intent.get("reply"))
        if not reply:
            reply = "Готов помочь. Уточни, пожалуйста."
        await message.answer(reply)
        return

    if kind == "unknown" or kind is None:
        await message.answer(L.INTENT_UNKNOWN)
        return

    if kind == "list_todos":
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            items = await list_open_commitments(session, owner)
        if not items:
            await message.answer(L.STATUS_EMPTY_TODOS)
            return
        from src.core.timeutil import fmt_local
        lines = []
        for c in items[:30]:
            who = "Я" if c.direction == "mine" else (c.peer_name or "Они")
            d = fmt_local(c.deadline_at, tz_name)
            lines.append(f"• <b>{who}</b>: {c.text} (до {d})")
        await message.answer(
            f"📋 Открытых обязательств: <b>{len(items)}</b>\n\n" + "\n".join(lines)
        )
        return

    if kind == "show_my_tasks":
        async with get_session() as session:
            team = await get_team_for_event(session, message)
        board_id = (team.active_board_id or team.kanban_board_id) if team else None
        if team and team.kanban_token and board_id and len(board_id) >= 10:
            await _exec_kanban_intent({"intent": "show_boards", "parameters": {}}, message)
        else:
            async with get_session() as session:
                owner = await get_or_create_user(session, message.from_user.id)
                items = await list_open_commitments(session, owner)
            if not items:
                await message.answer("У тебя нет открытых задач 🎉")
                return
            lines = []
            for c in items[:20]:
                who = "Я" if c.direction == "mine" else (c.peer_name or "Они")
                deadline = fmt_local(c.deadline_at, tz_name) if c.deadline_at else "без срока"
                lines.append(f"• <b>{who}</b>: {c.text} · {deadline}")
            await message.answer(
                f"📋 <b>Мои задачи</b> ({len(items)}):\n\n" + "\n".join(lines)
            )
        return

    if kind == "trash_task":
        query = (intent.get("query") or "").strip().lower()
        if not query:
            await message.answer("Какое обязательство убрать в корзину?")
            return
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            items = await list_open_commitments(session, owner)
            matched = [c for c in items if query in (c.text or "").lower()]
            if not matched:
                await message.answer(f"Не нашёл обязательств по «{query}».")
                return
            for c in matched:
                await trash_commitment(session, c.id)
        names = "\n".join(f"• {c.text}" for c in matched)
        await message.answer(f"🗑 Переместил в корзину ({len(matched)}):\n{names}")
        return

    if kind == "restore_task":
        query = (intent.get("query") or "").strip().lower()
        if not query:
            await message.answer("Какое обязательство восстановить?")
            return
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            items = await list_trashed_commitments(session, owner)
            matched = [c for c in items if query in (c.text or "").lower()]
            if not matched:
                await message.answer(f"Не нашёл в корзине по «{query}».")
                return
            for c in matched:
                await restore_commitment(session, c.id)
        names = "\n".join(f"• {c.text}" for c in matched)
        await message.answer(f"♻ Восстановил из корзины ({len(matched)}):\n{names}")
        return

    if kind == "restore_kanban_task":
        task_title = (intent.get("task_title") or "").strip()
        if not task_title:
            await message.answer("Укажи название задачи для восстановления.")
            return

        async with get_session() as session:
            team = await get_team_for_event(session, message)
        board_id = (team.active_board_id or team.kanban_board_id) if team else None

        if not team or not team.kanban_token or not board_id or len(board_id) < 10:
            await message.answer("⚠️ Канбан-доска не настроена.")
            return

        from src.bot.handlers.yougile import YouGileClient
        client_yg = YouGileClient(team.kanban_token, board_id)

        try:
            columns = await client_yg.get_columns()
        except Exception as e:
            await message.answer(f"❌ Ошибка при получении колонок: {e}")
            return

        TRASH_KEYWORDS = ["корзин", "удал", "archive", "trash", "deleted"]
        trash_col = next(
            (c for c in columns
             if any(kw in c.get("title", "").lower() for kw in TRASH_KEYWORDS)),
            None,
        )

        if not trash_col:
            await message.answer(
                "❓ Не нашёл колонку корзины на доске.\n"
                "Убедись что есть колонка с названием «Корзина», «Удалённые» или «Archive»."
            )
            return

        try:
            cards = await client_yg.get_cards_in_column(trash_col["id"], limit=100)
        except Exception as e:
            await message.answer(f"❌ Ошибка при поиске в корзине: {e}")
            return

        card = next(
            (c for c in cards if task_title.lower() in c.get("title", "").lower()),
            None,
        )

        if not card:
            await message.answer(
                f"❓ Задача «{task_title}» не найдена в корзине.\n"
                f"В корзине сейчас: {len(cards)} задач."
            )
            return

        active_cols = [c for c in columns if c["id"] != trash_col["id"]]
        if not active_cols:
            await message.answer("❌ Нет активных колонок для восстановления.")
            return

        target_col = active_cols[0]
        try:
            await client_yg.move_card(card["id"], target_col["id"])
            await message.answer(
                f"✅ Задача «{card.get('title')}» восстановлена "
                f"в колонку «{target_col.get('title')}»."
            )
        except Exception as e:
            await message.answer(f"❌ Ошибка при восстановлении: {e}")
        return

    if kind == "show_team_risks":
        async with get_session() as session:
            team = await get_team_for_event(session, message)
            if not team:
                await message.answer("Команда не настроена.")
                return
            from src.db.repo import get_recent_risks
            risks = await get_recent_risks(session, team.id, limit=10)
        if not risks:
            await message.answer("✅ Рисков за последнее время не обнаружено.")
            return
        lines = [f"🚨 <b>Последние риски команды</b> ({len(risks)}):\n"]
        for r in risks:
            lines.append(
                f"• <b>{r.display_name}</b>: {r.risk_reason}\n"
                f"  <i>{r.created_at.strftime('%d.%m %H:%M')}</i>"
            )
        await message.answer("\n".join(lines))
        return

    if kind == "show_team_sentiment":
        async with get_session() as session:
            team = await get_team_for_event(session, message)
            if not team:
                await message.answer("Команда не настроена.")
                return
            from src.db.models import MessageSentiment
            from sqlalchemy import select, func
            result = await session.execute(
                select(
                    MessageSentiment.sentiment,
                    func.count(MessageSentiment.id).label("cnt")
                )
                .where(MessageSentiment.team_id == team.id)
                .group_by(MessageSentiment.sentiment)
            )
            rows = result.all()
        if not rows:
            await message.answer("Данных о тональности пока нет.")
            return
        total = sum(r.cnt for r in rows)
        emoji_map = {
            "positive": "😊", "negative": "😟",
            "neutral": "😐", "speech": "💬"
        }
        lines = [f"🧠 <b>Эмоциональный фон команды</b> (всего {total} сообщений):\n"]
        for r in sorted(rows, key=lambda x: x.cnt, reverse=True):
            pct = round(r.cnt / total * 100)
            em = emoji_map.get(r.sentiment, "•")
            lines.append(f"{em} {r.sentiment}: {r.cnt} ({pct}%)")
        await message.answer("\n".join(lines))
        return

    if kind == "notify_team":
        notify_text = (
            intent.get("message")
            or intent.get("parameters", {}).get("message")
            or ""
        ).strip()
        if not notify_text:
            await message.answer("Укажи текст оповещения.")
            return

        async with get_session() as session:
            team = await get_team_for_event(session, message)
            if not team:
                await message.answer("Команда не настроена.")
                return

            from src.db.models import TeamMember, User
            from sqlalchemy import select
            result = await session.execute(
                select(TeamMember).where(TeamMember.team_id == team.id)
            )
            members = list(result.scalars().all())

            mentions = []
            for m in members:
                if m.telegram_id == message.from_user.id:
                    continue
                user_result = await session.execute(
                    select(User).where(User.telegram_id == m.telegram_id)
                )
                user = user_result.scalar_one_or_none()
                name = (
                    getattr(m, "display_name", None)
                    or (user.display_name if user else None)
                    or str(m.telegram_id)
                )
                mentions.append(
                    f'<a href="tg://user?id={m.telegram_id}">{name}</a>'
                )

        if not mentions:
            await message.answer(
                f"📢 <b>Оповещение:</b>\n{notify_text}\n\n"
                f"(Участников для тега не найдено)"
            )
            return

        tags = " ".join(mentions)
        # В ДМ отправляем оповещение в групповой чат команды, чтобы все увидели
        if team.chat_id and message.chat.type == "private":
            try:
                await message.bot.send_message(
                    team.chat_id,
                    f"📢 <b>Оповещение от руководителя:</b>\n\n"
                    f"{notify_text}\n\n"
                    f"{tags}",
                    parse_mode="HTML",
                )
                await message.answer("✅ Оповещение отправлено в командный чат.")
            except Exception as e:
                await message.answer(f"❌ Не удалось отправить в группу: {e}")
        else:
            await message.answer(
                f"📢 <b>Оповещение для команды:</b>\n\n"
                f"{notify_text}\n\n"
                f"{tags}",
                parse_mode="HTML",
            )
        return

    if kind == "start_pulse":
        await message.answer(
            "🔔 Используй команду /pulse для запуска опроса в командном чате."
        )
        return

    if kind == "show_pulse_results":
        try:
            from src.group_bot.handlers.pulse import show_pulse_results as _show_pulse
            await _show_pulse(message)
        except (ImportError, AttributeError):
            async with get_session() as session:
                team = await get_team_for_event(session, message)
                if not team:
                    await message.answer("Команда не настроена.")
                    return
                from src.db.repo import aggregate_pulse_responses
                agg = await aggregate_pulse_responses(session, team.id, days=7)
            if agg.total_responses == 0:
                await message.answer("📋 За последние 7 дней пульс-опросов не проводилось.")
                return
            dist_lines = [f"  {'⭐' * v} {v}: {agg.distribution.get(v, 0)}" for v in sorted(agg.distribution, reverse=True)]
            await message.answer(
                f"📊 <b>Пульс команды за 7 дней</b>\n\n"
                f"Всего голосов: {agg.total_responses}\n"
                f"Средний балл: {agg.avg:.1f}/5\n"
                f"Тренд: {agg.trend}\n\n"
                f"Распределение:\n" + "\n".join(dist_lines)
            )
        return

    if kind == "show_task_report":
        async with get_session() as session:
            team = await get_team_for_event(session, message)
        board_id = (team.active_board_id or team.kanban_board_id) if team else None

        if not team or not team.kanban_token or not board_id or len(board_id) < 10:
            await message.answer("⚠️ Канбан-доска не настроена. Используй /kanban_board.")
            return

        from src.bot.handlers.yougile import YouGileClient
        client_yg = YouGileClient(team.kanban_token, board_id)

        try:
            columns = await client_yg.get_columns()
        except Exception as e:
            await message.answer(f"❌ Ошибка при получении данных: {e}")
            return

        total = 0
        lines = [f"📊 <b>Отчёт по задачам команды</b>\n"]
        for col in columns:
            try:
                cards = await client_yg.get_cards_in_column(col["id"], limit=100)
            except Exception:
                cards = []
            count = len(cards)
            total += count
            col_name = col.get("title", "?")
            lines.append(f"• <b>{col_name}</b>: {count} задач")

        lines.append(f"\n📌 Всего задач на доске: <b>{total}</b>")
        await message.answer("\n".join(lines))
        return

    if client is None:
        await message.answer("Сначала /login — нужен подключённый Telegram-аккаунт.")
        return

    if kind == "send_message":
        recipient = (intent.get("recipient") or "").strip()
        text = (intent.get("text") or "").strip()
        if not recipient or not text:
            await message.answer("Не хватает кому/что отправить. Уточни.")
            return
        candidates = await resolve(client, owner, recipient)
        if not candidates:
            await message.answer(f"Не нашёл контакт «{recipient}». Попробуй /sync.")
            return
        if len(candidates) == 1 or candidates[0].score >= 90:
            target = candidates[0]
            ctx_store.set_last_peer(message.from_user.id, target.peer_id, target.display_name)
            payload = {"peer_id": target.peer_id, "text": text}
            async with get_session() as session:
                owner = await get_or_create_user(session, message.from_user.id)
                action = await create_pending_action(
                    session, user_id=owner.id, kind="send_message", payload=payload
                )
            await message.answer(
                f"🤔 <b>Готов отправить</b>\n\n"
                f"→ <b>Кому:</b> {target.label()}\n"
                f"→ <b>Текст:</b>\n{text}",
                reply_markup=_confirm_keyboard(action.id),
            )
        else:
            await state.set_data({"send_text": text})
            await message.answer(
                f"Кому именно отправить «<i>{text[:80]}</i>»?",
                reply_markup=_candidates_keyboard_send(candidates),
            )
        return

    if kind == "search":
        query = (intent.get("query") or "").strip() or raw
        await message.answer(f"🔎 Ищу: <i>{query}</i>…")
        from src.bot.handlers.search import cmd_search
        from aiogram.filters import CommandObject
        await cmd_search(message, CommandObject(prefix="/", command="search", args=query), userbot_manager)
        return

    if kind == "find_in_chats":
        query = (intent.get("query") or "").strip()
        action = (intent.get("action") or "catchup").strip()
        if action not in {"catchup", "summary", "tasks", "draft"}:
            action = "catchup"
        if not query:
            await message.answer("Не понял, по какой теме искать.")
            return
        await message.answer(f"🔎 Ищу по моим чатам: «<i>{query}</i>»…")
        await _find_chats_and_offer(message, client, query, action)
        return

    if kind == "news_digest":
        topic = (intent.get("topic") or "").strip()
        if not topic:
            await message.answer("Уточни тему для новостей.")
            return
        try:
            hours = int(intent.get("hours") or 24)
        except (TypeError, ValueError):
            hours = 24
        hours = max(1, min(168, hours))
        await message.answer(f"📰 Готовлю дайджест: <i>{topic}</i> · окно {hours}ч…")
        text = await build_news_digest(client, message.from_user.id, topic, hours=hours)
        await message.answer(text, disable_web_page_preview=True)
        return

    # ниже — интенты, требующие конкретного контакта
    contact_query = (intent.get("contact") or "").strip()
    if not contact_query:
        await message.answer("Не понял, с каким контактом работать. Уточни имя.")
        return

    candidates = await resolve(client, owner, contact_query)
    if not candidates:
        await message.answer(f"Не нашёл контакт «{contact_query}». Попробуй /sync.")
        return

    action_map = {
        "summarize_chat": "summary",
        "tasks_for_chat": "tasks",
        "draft_reply":    "draft",
        "catchup":        "catchup",
    }
    cb_action = action_map.get(kind)
    if cb_action is None:
        await message.answer("Неизвестное действие.")
        return

    if len(candidates) > 1 and candidates[0].score < 90:
        await message.answer(
            f"С кем именно? (действие: <b>{cb_action}</b>)",
            reply_markup=_candidates_keyboard_chat(cb_action, candidates),
        )
        return

    target = candidates[0]
    ctx_store.set_last_peer(message.from_user.id, target.peer_id, target.display_name)
    await message.answer(f"⏳ Подгружаю чат с <b>{target.label()}</b>…")
    messages_loaded = await load_chat(
        client, message.from_user.id, target.peer_id,
        limit=CHAT_LOAD_LIMIT, transcribe=True,
    )
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        contact = await get_contact(session, owner, target.peer_id)

    if contact is None or not providers:
        await message.answer("Не удалось подготовить контекст.")
        return

    if kind == "summarize_chat":
        text = await summarize_chat(providers, contact, messages_loaded, heavy=heavy, notify_bot=message.bot, notify_chat_id=message.chat.id)
        await message.answer(f"📝 <b>Саммари — {contact.display_name}</b>\n\n{text}")

    elif kind == "tasks_for_chat":
        items = await extract_and_save_commitments(
            providers, user_id=owner.id, contact=contact, messages=messages_loaded,
            chat_id=message.chat.id, notify_bot=message.bot, notify_chat_id=message.chat.id,
        )
        if not items:
            body = "Явных обязательств не нашёл."
        else:
            lines = []
            for it in items:
                who = "Я" if it.get("direction") == "mine" else "Они"
                deadline = it.get("deadline")
                tail = f" · до {deadline}" if deadline else ""
                lines.append(f"• <b>{who}</b>: {it.get('text', '')}{tail}")
            body = "\n".join(lines)
        await message.answer(f"✅ <b>Обязательства — {contact.display_name}</b>\n\n{body}")

    elif kind == "draft_reply":
        instruction = intent.get("instruction") or None
        draft = await draft_reply(providers, contact, messages_loaded, instruction=instruction, heavy=heavy, notify_bot=message.bot, notify_chat_id=message.chat.id)
        payload = {"peer_id": target.peer_id, "text": draft}
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            action = await create_pending_action(
                session, user_id=owner.id, kind="send_message", payload=payload
            )
        await message.answer(
            f"💬 <b>Черновик — {contact.display_name}</b>\n\n{draft}\n\nОтправить?",
            reply_markup=_confirm_keyboard(action.id),
        )

    elif kind == "catchup":
        text = await catchup(providers, contact, messages_loaded, heavy=heavy, notify_bot=message.bot, notify_chat_id=message.chat.id)
        await message.answer(
            f"⏪ <b>Где мы остановились — {contact.display_name}</b>\n\n{text}"
        )


async def _find_chats_and_offer(message, client, query: str, action: str) -> None:
    from src.core.chat_finder import smart_find

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        providers = await get_provider_chain(session, owner)
    if not providers:
        await message.answer("Нужен LLM-ключ (/settings → 🔑).")
        return

    try:
        results = await smart_find(client, owner, providers, query, top_n=5)
    except Exception:
        logger.exception("smart_find failed")
        await message.answer("❌ Поиск не удался. Попробуй ещё раз или уточни запрос.")
        return

    if not results:
        await message.answer(
            f"Ничего не нашёл по «{query}» — ни по тексту, ни по именам контактов. "
            "Попробуй описать чуть конкретнее или назови сам контакт."
        )
        return

    # пишем контакты в БД, чтобы chat:<action>:<peer_id> handler знал display_name
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        for r in results:
            await upsert_contact(
                session,
                owner,
                peer_id=r.peer_id,
                peer_kind=r.kind,
                is_bot=r.is_bot,
                display_name=r.name,
                username=r.username,
            )

    kb = InlineKeyboardBuilder()
    for r in results:
        marks = []
        if r.text_hits:
            marks.append(f"{r.text_hits} совп.")
        if r.name_score:
            marks.append(f"имя {r.name_score}/5")
        meta = " · ".join(marks)
        label = f"{r.name}" + (f" · {meta}" if meta else "")
        if len(label) > 60:
            label = label[:57] + "…"
        kb.row(InlineKeyboardButton(text=label, callback_data=f"chat:{action}:{r.peer_id}"))
    kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="chat:cancel:0"))

    pretty_action = {
        "catchup": "«где остановились»",
        "summary": "саммари",
        "tasks":   "задачи/обещания",
        "draft":   "черновик ответа",
    }.get(action, action)

    await message.answer(
        f"Нашёл подходящие чаты. Выбери — соберу {pretty_action}:",
        reply_markup=kb.as_markup(),
    )


async def _exec_set_setting(intent, message) -> None:
    key = (intent.get("key") or "").strip()
    value = intent.get("value")
    spec = SETTING_FIELDS.get(key)
    if spec is None:
        await message.answer(f"Не умею менять «{key}».")
        return
    validated, err = _coerce_setting_value(spec, value)
    if err:
        await message.answer(f"Не понял значение для <b>{key}</b>: {err}.")
        return
    if spec == "token":
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            await upsert_api_key(session, owner, key.replace("_token", ""), validated)
        await message.answer(f"✅ API-ключ <b>{key}</b> сохранён.")
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        setattr(owner.settings, key, validated)
        new_tz = owner.settings.timezone
    if key == "timezone":
        await message.answer(f"✅ Часовой пояс: <b>{tz_short(new_tz)}</b>")
    elif isinstance(validated, bool):
        await message.answer(f"✅ <b>{key}</b>: {'ВКЛ' if validated else 'ВЫКЛ'}")
    else:
        shown = str(validated)
        if len(shown) > 100:
            shown = shown[:97] + "…"
        await message.answer(f"✅ <b>{key}</b> = <code>{shown}</code>")


async def _exec_add_news_topic(intent, message) -> None:
    topic = (intent.get("topic") or "").strip()
    if not topic:
        await message.answer("Не понял какую тему добавить.")
        return
    try:
        hours = int(intent.get("hours") or 24)
    except (TypeError, ValueError):
        hours = 24
    hours = max(1, min(168, hours))
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        await add_news_topic(session, owner, topic, hours=hours)
    await message.answer(f"✅ Добавил тему: <b>{topic}</b> (окно {hours}ч)")


async def _exec_remove_news_topic(intent, message) -> None:
    needle = (intent.get("topic") or "").strip().lower()
    if not needle:
        await message.answer("Какую тему удалить?")
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        topics = await list_news_topics(session, owner)
        matched = [t for t in topics if needle in t.topic.lower()]
        if not matched:
            await message.answer(f"Тем по «{needle}» не нашёл.")
            return
        for t in matched:
            await delete_news_topic(session, owner, t.id)
    names = ", ".join(f"«{t.topic}»" for t in matched)
    await message.answer(f"🗑 Удалил: {names}")


async def _process_text(
    raw: str,
    message: Message,
    state: FSMContext,
    userbot_manager: UserbotManager,
) -> None:
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        providers = await get_provider_chain(session, owner)
        tz_name = owner.settings.timezone

    if not providers:
        await message.answer(
            "Чтобы я мог понимать свободный текст — добавь LLM-ключ в /settings → 🔑 API-ключи."
        )
        return

    now_local_str = now_in_tz(tz_name).strftime("%Y-%m-%d %H:%M")
    history_block = ctx_store.render_history_block(message.from_user.id)
    try:
        intent = await route_intent(
            providers, raw,
            heavy=False,
            now_local=now_local_str,
            tz_name=tz_name,
            history_block=history_block,
            notify_bot=message.bot,
            notify_chat_id=message.chat.id,
        )
    except Exception:
        logger.exception("agent route_intent failed")
        await message.answer(L.ERR_LLM_FAIL)
        return

    if intent.get("intent") == "multi":
        actions = intent.get("actions") or []
        if not isinstance(actions, list) or not actions:
            await message.answer("Не понял, что сделать.")
            return
        for sub in actions:
            await _dispatch(sub, message, state, userbot_manager, tz_name=tz_name)
    else:
        await _dispatch(intent, message, state, userbot_manager, tz_name=tz_name)

    summary = _summarize_intent_for_memory(intent)
    ctx_store.add_turn(message.from_user.id, raw, summary)


def _summarize_intent_for_memory(intent: dict) -> str:
    # компактная запись «что я только что сделал» для памяти диалога
    kind = intent.get("intent")
    if kind == "multi":
        return "несколько действий: " + ", ".join(
            (a or {}).get("intent", "?") for a in intent.get("actions", [])[:5]
        )
    if kind == "send_message":
        return f"подготовил отправку «{(intent.get('text') or '')[:60]}» для {intent.get('recipient')}"
    if kind in {"summarize_chat", "tasks_for_chat", "draft_reply", "catchup"}:
        return f"{kind} с контактом {intent.get('contact')}"
    if kind == "find_in_chats":
        return f"искал в чатах: {intent.get('query')}"
    if kind == "news_digest":
        return f"новости: {intent.get('topic')}"
    if kind == "set_setting":
        return f"настройка {intent.get('key')} → {intent.get('value')}"
    if kind == "add_news_topic":
        return f"добавил тему: {intent.get('topic')}"
    if kind == "remove_news_topic":
        return f"убрал тему: {intent.get('topic')}"
    if kind == "add_reminder":
        return f"напоминание: {intent.get('text')}"
    if kind == "remove_reminder":
        return f"убрал напоминание: {intent.get('query')}"
    if kind == "add_reminders_from_chat":
        return f"вытащил обещания из чата с {intent.get('contact')}"
    if kind == "list_todos":
        return "показал список обещаний"
    if kind == "trash_task":
        return f"удалил в корзину: {intent.get('query')}"
    if kind == "restore_task":
        return f"восстановил из корзины: {intent.get('query')}"
    if kind == "join_meeting":
        url = intent.get("url", "")
        return f"подключился к встрече: {url}" if url else "запросил ссылку на встречу"
    if kind == "schedule_meeting":
        return f"запланировал встречу: {intent.get('title', '')}"
    if kind == "notify_team":
        return f"оповестил команду: {intent.get('message', '')[:60]}"
    if kind == "meeting_summary":
        return "запросил итоги встречи"
    if kind == "chat":
        return (intent.get("reply") or "")[:160]
    return kind or ""


def _extract_tasks_from_intent(intent: dict) -> list[dict]:
    kind = intent.get("intent")
    if kind == "create_task":
        return [{
            "title": intent.get("title", ""),
            "description": intent.get("description", ""),
            "deadline": intent.get("deadline"),
            "assignee": intent.get("assignee"),
        }]
    if kind == "multi":
        result = []
        for act in intent.get("actions") or []:
            if act.get("intent") == "create_task":
                result.append({
                    "title": act.get("title", ""),
                    "description": act.get("description", ""),
                    "deadline": act.get("deadline"),
                    "assignee": act.get("assignee"),
                })
        return result
    return []


@router.message(TaskCreationStates.waiting_for_board, F.text)
async def catch_while_waiting_board(message: Message, state: FSMContext) -> None:
    await message.answer(
        "Пожалуйста, выберите доску для предыдущих задач или нажмите «Отмена»."
    )


@router.message(F.text & ~F.text.startswith("/"))
async def free_text(
    message: Message,
    state: FSMContext,
    userbot_manager: UserbotManager,
) -> None:
    if await state.get_state() is not None:
        return
    raw = (message.text or "").strip()
    if not raw:
        return
    await _process_text(raw, message, state, userbot_manager)


@router.message(F.voice | F.audio)
async def free_voice(
    message: Message,
    state: FSMContext,
    userbot_manager: UserbotManager,
) -> None:
    current_state = await state.get_state()
    if current_state is not None and current_state != TaskCreationStates.waiting_for_board:
        return

    media = message.voice or message.audio
    if media is None:
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        mode = owner.settings.transcription_mode
        openai_key = await get_api_key(session, owner, "openai")

    media_dir = app_settings.data_dir / "media" / "control_bot"
    media_dir.mkdir(parents=True, exist_ok=True)
    target = media_dir / f"{message.message_id}_{media.file_unique_id}.ogg"

    notice = await message.answer("🎙 Слушаю… (транскрибирую)")
    try:
        await message.bot.download(media.file_id, destination=str(target))
    except Exception:
        client = userbot_manager.get_client(message.from_user.id)
        if client is None:
            await notice.edit_text(L.ERR_VOICE_FAIL)
            return
        try:
            tg_msg = await client.get_messages(message.chat.id, ids=message.message_id)
            if tg_msg is None:
                await notice.edit_text(L.ERR_VOICE_FAIL)
                return
            await tg_msg.download_media(file=str(target))
        except Exception:
            logger.exception("voice download failed")
            await notice.edit_text(L.ERR_VOICE_FAIL)
            return

    try:
        text = await transcription_service.transcribe(
            target,
            file_id=media.file_unique_id,
            mode=mode,
            openai_key=openai_key,
        )
    except Exception:
        logger.exception("voice transcription failed")
        try:
            await notice.edit_text(L.ERR_VOICE_FAIL)
        except Exception:
            pass
        return

    text = (text or "").strip()
    if not text:
        try:
            await notice.edit_text("Не услышал текста в этом сообщении.")
        except Exception:
            pass
        return

    try:
        await notice.edit_text(f"🎙 <i>Услышал:</i> {text}")
    except Exception:
        pass

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        providers = await get_provider_chain(session, owner)
        tz_name = owner.settings.timezone

    if not providers:
        await message.answer(
            "Чтобы я мог понимать свободный текст — добавь LLM-ключ в /settings → 🔑 API-ключи."
        )
        return

    now_local_str = now_in_tz(tz_name).strftime("%Y-%m-%d %H:%M")
    history_block = ctx_store.render_history_block(message.from_user.id)
    try:
        intent = await route_intent(
            providers, text,
            heavy=False,
            now_local=now_local_str,
            tz_name=tz_name,
            history_block=history_block,
            notify_bot=message.bot,
            notify_chat_id=message.chat.id,
        )
    except Exception:
        logger.exception("agent route_intent failed")
        try:
            await notice.edit_text(L.ERR_LLM_FAIL)
        except Exception:
            pass
        return

    raw_tasks = _extract_tasks_from_intent(intent)

    if raw_tasks:
        async with get_session() as session:
            team = await get_team_for_event(session, message)

        if not team or not team.kanban_token:
            try:
                await notice.edit_text("⚠️ Канбан не подключён. Используй /kanban.")
            except Exception:
                pass
            return

        from src.bot.handlers.yougile import YouGileClient
        client = YouGileClient(team.kanban_token)
        try:
            boards = await client.get_boards()
        except Exception as e:
            try:
                await notice.edit_text(f"❌ Ошибка при получении досок: {e}")
            except Exception:
                pass
            return

        if not boards:
            try:
                await notice.edit_text("❌ В YouGile нет ни одной доски.")
            except Exception:
                pass
            return

        board_refs = [(b["id"], b["title"]) for b in boards]
        new_tasks = [{
            "title": t.get("title", ""),
            "description": t.get("description", ""),
            "deadline": t.get("deadline"),
            "assignee": t.get("assignee"),
        } for t in raw_tasks]

        # --- ACCUMULATION: voice while waiting_for_board ---
        if current_state == TaskCreationStates.waiting_for_board:
            state_data = await state.get_data()
            existing_tasks = state_data.get("tasks", [])
            existing_tasks.extend(new_tasks)
            source_msg_id = state_data.get("source_message_id")
            existing_board_refs = state_data.get("board_refs", board_refs)

            await state.update_data(tasks=existing_tasks)

            kb = InlineKeyboardBuilder()
            for idx, b in enumerate(existing_board_refs):
                kb.row(InlineKeyboardButton(
                    text=b[1],
                    callback_data=f"tv:{idx}",
                ))
            kb.row(InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="tv:cancel",
            ))

            task_lines = "\n".join(f"• {t['title']}" for t in existing_tasks[:5])
            extra = f"\n… и ещё {len(existing_tasks) - 5}" if len(existing_tasks) > 5 else ""

            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=source_msg_id,
                    text=f"📋 Найдено задач ({len(existing_tasks)}):\n{task_lines}{extra}\n\n"
                         f"Выбери доску для сохранения:",
                    reply_markup=kb.as_markup(),
                )
            except Exception:
                pass

            summary = _summarize_intent_for_memory(intent)
            ctx_store.add_turn(message.from_user.id, text, summary)
            return

        # --- NORMAL FLOW (first voice) ---
        await state.update_data(
            tasks=new_tasks,
            board_refs=board_refs,
        )
        await state.set_state(TaskCreationStates.waiting_for_board)

        kb = InlineKeyboardBuilder()
        for idx, b in enumerate(boards):
            kb.row(InlineKeyboardButton(
                text=b["title"],
                callback_data=f"tv:{idx}",
            ))
        kb.row(InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="tv:cancel",
        ))

        task_lines = "\n".join(f"• {t['title']}" for t in new_tasks[:5])
        extra = f"\n… и ещё {len(new_tasks) - 5}" if len(new_tasks) > 5 else ""
        sent = await message.answer(
            f"📋 Найдено задач ({len(new_tasks)}):\n{task_lines}{extra}\n\n"
            f"Выбери доску для сохранения:",
            reply_markup=kb.as_markup(),
        )

        await state.update_data(source_message_id=sent.message_id)

        summary = _summarize_intent_for_memory(intent)
        ctx_store.add_turn(message.from_user.id, text, summary)
        return

    if current_state == TaskCreationStates.waiting_for_board:
        try:
            await notice.edit_text("❌ Не удалось извлечь задачи. Попробуйте ещё раз.")
        except Exception:
            pass
        return

    if intent.get("intent") == "multi":
        actions = intent.get("actions") or []
        for sub in actions:
            await _dispatch(sub, message, state, userbot_manager, tz_name=tz_name)
    else:
        await _dispatch(intent, message, state, userbot_manager, tz_name=tz_name)

    summary = _summarize_intent_for_memory(intent)
    ctx_store.add_turn(message.from_user.id, text, summary)


async def _dispatch(intent, message, state, userbot_manager, *, tz_name: str) -> None:
    kind = intent.get("intent")
    if kind == "set_setting":
        await _exec_set_setting(intent, message)
        return
    if kind == "add_news_topic":
        await _exec_add_news_topic(intent, message)
        return
    if kind == "remove_news_topic":
        await _exec_remove_news_topic(intent, message)
        return
    if kind == "add_reminder":
        await _exec_add_reminder(intent, message, tz_name=tz_name)
        return
    if kind == "remove_reminder":
        await _exec_remove_reminder(intent, message)
        return
    if kind == "add_reminders_from_chat":
        await _exec_add_reminders_from_chat(intent, message, userbot_manager)
        return
    if kind in ("join_meeting", "schedule_meeting", "meeting_summary"):
        await _exec_meeting_intent(intent, message)
        return
    if not await _check_intent_perms(kind, message):
        await message.answer("⛔ Доступ запрещён для вашей роли.")
        return
    await _execute_intent(intent, message, state, userbot_manager, tz_name=tz_name)


async def _check_intent_perms(kind: str, message: Message) -> bool:
    # В личке владелец имеет полный доступ — не проверяем роли
    if message.chat.type == "private":
        return True
    async with get_session() as session:
        team = await get_team_for_event(session, message)
        if team is None:
            return True
        member = await get_team_member(session, team.id, message.from_user.id)
        if member is None:
            return True
        return await check_user_permission(kind, member, session)


async def _exec_meeting_intent(intent: dict, message: Message) -> None:
    from src.services.meeting_room import create_meeting_room
    from src.bot.handlers.meeting import detect_platform

    kind = intent.get("intent")

    if kind in ("join_meeting", "schedule_meeting"):
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            mtslink_token = await get_api_key(session, owner, "mtslink")
            team = await get_team_for_event(session, message)

        if kind == "schedule_meeting":
            title = intent.get("title", "Встреча")
            starts_at = intent.get("datetime")
            try:
                url, event_id, session_id = await create_meeting_room(
                    title, mtslink_token, starts_at,
                    team_chat_id=message.chat.id if mtslink_token else None,
                )
            except Exception as e:
                await message.answer(f"❌ Не удалось создать встречу: {e}")
                return

            platform = detect_platform(url)

            if team:
                async with get_session() as session:
                    await create_meeting(session, team.id, url, platform, event_id, mtslink_session_id=session_id)

            parts = [f"📅 <b>Встреча создана</b>\n\nНазвание: <b>{title}</b>"]
            if starts_at:
                from datetime import datetime, timezone, timedelta
                _months = {
                    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
                    5: "мая", 6: "июня", 7: "июля", 8: "августа",
                    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
                }
                try:
                    s = starts_at.replace("Z", "+00:00")
                    dt = datetime.fromisoformat(s)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone(timedelta(hours=3)))
                    msk = dt.astimezone(timezone(timedelta(hours=3)))
                    parts.append(f"Начало: {msk.day} {_months[msk.month]} {msk.year} в {msk:%H:%M} (МСК)")
                except Exception:
                    pass
            parts.append(f"Ссылка: <code>{url}</code>\n\nОтправь эту ссылку участникам для подключения.")
            await message.answer("\n".join(parts))

            if team:
                async with get_session() as session:
                    from src.db.repo import get_team_members
                    members = await get_team_members(session, team.id)
                    from src.db.models import User
                    from sqlalchemy import select
                    mentions = []
                    for m in members:
                        if m.telegram_id == message.from_user.id:
                            continue
                        name = m.display_name
                        if not name:
                            result = await session.execute(
                                select(User).where(User.telegram_id == m.telegram_id)
                            )
                            user = result.scalar_one_or_none()
                            name = user.display_name if user else None
                        name = name or str(m.telegram_id)
                        mentions.append(f'<a href="tg://user?id={m.telegram_id}">{name}</a>')
                if mentions:
                    await message.answer(
                        f"📣 Приглашаю на встречу «<b>{title}</b>»:\n" + " ".join(mentions) +
                        f"\n\n🔗 <code>{url}</code>",
                    )
            return

        if kind == "join_meeting":
            url = (intent.get("url") or "").strip()
            if url:
                platform = detect_platform(url)
                if team:
                    async with get_session() as session:
                        await create_meeting(session, team.id, url, platform)

                await message.answer(
                    f"🔗 <b>Подключение к встрече</b>\n\n"
                    f"Ссылка: <code>{url}</code>\n"
                    f"Открой её в браузере, чтобы присоединиться."
                )
            else:
                await message.answer(
                    "🎥 <b>Подключение к встрече</b>\n\n"
                    "Отправь ссылку на видеовстречу, например:\n"
                    "<code>https://telemost.yandex.ru/j/...</code>\n"
                    "<code>https://my.mts-link.ru/...</code>\n"
                    "<code>https://jazz.sber.ru/...</code>\n"
                    "<code>https://kontur.ru/tolk/...</code>"
                )
            return

    if kind == "meeting_summary":
        await message.answer(
            "📝 <b>Итоги встречи</b>\n\n"
            "Используй:\n<code>/meeting</code>\n\n"
            "Запиши встречу, а я расшифрую и извлеку задачи."
        )
        return


def _parse_iso_to_utc_naive(value):
    if not value:
        return None
    try:
        from datetime import datetime, timezone
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


async def _exec_add_reminder(intent, message, *, tz_name: str) -> None:
    text = (intent.get("text") or "").strip()
    if not text:
        await message.answer("Не понял, о чём напомнить. Уточни.")
        return
    when = _parse_iso_to_utc_naive(intent.get("when"))
    peer_query = (intent.get("peer_query") or "").strip()

    peer_id = 0
    peer_name = None
    if peer_query:
        from src.userbot.manager import _MANAGER_SINGLETON
        client = _MANAGER_SINGLETON.get_client(message.from_user.id) if _MANAGER_SINGLETON else None
        if client is not None:
            from src.core.contact_resolver import resolve
            async with get_session() as session:
                owner = await get_or_create_user(session, message.from_user.id)
            cands = await resolve(client, owner, peer_query)
            if cands:
                peer_id = cands[0].peer_id
                peer_name = cands[0].display_name

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        await add_commitment(
            session,
            user_id=owner.id,
            peer_id=peer_id,
            peer_name=peer_name,
            message_id=None,
            direction="mine",
            text=text,
            deadline_at=when,
        )

    when_str = fmt_local(when, tz_name) if when else "без срока"
    extra = f" (контакт: {peer_name})" if peer_name else ""
    note = "" if owner.settings.reminders_enabled else "\n\n⚠ Напоминания выключены — включи в /settings → ⏰."
    await message.answer(f"⏰ Напоминание добавлено: <b>{text}</b>\nКогда: {when_str}{extra}{note}")


async def _exec_remove_reminder(intent, message) -> None:
    needle = (intent.get("query") or "").strip().lower()
    if not needle:
        await message.answer("Какое напоминание убрать?")
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        items = await list_open_commitments(session, owner)
        matched = [
            c for c in items
            if needle in (c.text or "").lower()
            or (c.peer_name and needle in c.peer_name.lower())
        ]
        if not matched:
            await message.answer(f"Не нашёл напоминаний по «{needle}».")
            return
        for c in matched:
            await update_commitment_status(session, c.id, "cancelled")
    names = "\n".join(f"• {c.text}" for c in matched)
    await message.answer(f"🗑 Снял ({len(matched)}):\n{names}")


async def _exec_add_reminders_from_chat(intent, message, userbot_manager) -> None:
    contact_query = (intent.get("contact") or "").strip()
    if not contact_query:
        await message.answer("С каким контактом извлечь обещания?")
        return
    client = userbot_manager.get_client(message.from_user.id)
    if client is None:
        await message.answer("Сначала /login.")
        return

    from src.core.contact_resolver import resolve
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        providers = await get_provider_chain(session, owner)
    if not providers:
        await message.answer("Нужен LLM-ключ (/settings → 🔑).")
        return

    cands = await resolve(client, owner, contact_query)
    if not cands:
        await message.answer(f"Контакт «{contact_query}» не найден.")
        return
    target = cands[0]

    await message.answer(f"⏳ Подгружаю чат с <b>{target.label()}</b> и извлекаю обещания…")
    msgs = await load_chat(client, message.from_user.id, target.peer_id, limit=80, transcribe=True)
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        contact = await get_contact(session, owner, target.peer_id)
    items = await extract_and_save_commitments(
        providers, user_id=owner.id, contact=contact, messages=msgs,
        chat_id=message.chat.id, notify_bot=message.bot, notify_chat_id=message.chat.id,
    )
    if not items:
        await message.answer("Явных обещаний в этом чате не нашёл.")
        return
    lines = []
    for it in items:
        who = "Я" if it.get("direction") == "mine" else "Они"
        deadline = it.get("deadline")
        tail = f" · до {deadline}" if deadline else ""
        lines.append(f"• <b>{who}</b>: {it.get('text', '')}{tail}")
    await message.answer(
        f"⏰ Поставил {len(items)} напоминаний из чата с {target.display_name}:\n\n"
        + "\n".join(lines)
    )


@router.callback_query(F.data.startswith("yg_assign:"))
async def cb_yg_assign(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.")
        return

    chat_id = int(parts[1])
    yougile_user_id = parts[2] if parts[2] != "none" else None

    pending = _pending_assignee_selection.pop(chat_id, None)
    if not pending:
        await callback.answer("Задача уже создана или истекло время.")
        try:
            await callback.message.edit_text("⏱ Время выбора истекло. Попробуй снова.")
        except Exception:
            pass
        return

    assignee_ids = [yougile_user_id] if yougile_user_id else None

    try:
        from src.bot.handlers.yougile import YouGileClient
        client = YouGileClient(pending["kanban_token"], pending["board_id"])
        await client.create_card(
            title=pending["title"],
            description=pending.get("description", ""),
            column_id=pending["column_id"],
            assignee_ids=assignee_ids,
            deadline=pending.get("deadline"),
        )

        assignee_display = "без исполнителя"
        if yougile_user_id:
            users = pending.get("users", [])
            matched = next(
                (u for u in users if u.get("id") == yougile_user_id), None
            )
            if matched:
                assignee_display = matched.get("name") or yougile_user_id

            # Сохранить привязку имени → yougile_user_id
            original_name = pending.get("original_name", "")
            if original_name:
                try:
                    async with get_session() as session:
                        from src.db.repo import save_name_alias
                        await save_name_alias(
                            session,
                            team_id=pending["team_id"],
                            alias=original_name.lower(),
                            yougile_user_id=yougile_user_id,
                            display_name=assignee_display,
                        )
                except Exception as e:
                    logger.warning("save_name_alias failed: %s", e)

            try:
                async with get_session() as session:
                    from src.db.repo import set_team_member_yougile_id
                    await set_team_member_yougile_id(
                        session,
                        team_id=pending["team_id"],
                        telegram_id=callback.from_user.id,
                        yougile_user_id=yougile_user_id,
                    )
            except Exception as e:
                logger.warning("set_team_member_yougile_id failed: %s", e)

        await callback.message.edit_text(
            f"✅ Задача «{pending['title']}» создана!\n"
            f"👤 Исполнитель: {assignee_display}"
        )

    except Exception as e:
        logger.exception("cb_yg_assign: create_card failed")
        await callback.message.edit_text(f"❌ Ошибка при создании задачи: {e}")

    await callback.answer()


@router.callback_query(F.data.startswith("yg_page:"))
async def cb_yg_page(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer()
        return

    chat_id = int(parts[1])
    page = int(parts[2])

    pending = _pending_assignee_selection.get(chat_id)
    if not pending:
        await callback.answer("Время выбора истекло.")
        return

    users = pending.get("users", [])
    kb = _build_yougile_user_keyboard(users, chat_id, page=page)
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(TaskCreationStates.waiting_for_board, F.data.startswith("tv:"))
async def cb_voice_board_select(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 2:
        await callback.answer("❌ Некорректный запрос", show_alert=True)
        return

    action = parts[1]
    if action == "cancel":
        await state.clear()
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer()
        return

    try:
        idx = int(action)
    except ValueError:
        await callback.answer("❌ Некорректный запрос", show_alert=True)
        return

    state_data = await state.get_data()
    tasks = state_data.get("tasks", [])
    board_refs = state_data.get("board_refs", [])

    if not tasks or not board_refs:
        await callback.answer("❌ Данные не найдены. Попробуйте снова.", show_alert=True)
        await state.clear()
        return

    if idx < 0 or idx >= len(board_refs):
        await callback.answer("❌ Доска не найдена", show_alert=True)
        return

    board_id, board_name = board_refs[idx]

    async with get_session() as session:
        team = await get_team_for_event(session, callback)

    if not team or not team.kanban_token:
        await callback.answer("❌ Канбан не подключён", show_alert=True)
        return

    from src.bot.handlers.yougile import YouGileClient

    client = YouGileClient(team.kanban_token, board_id)
    try:
        columns = await client.get_columns()
    except Exception as e:
        await callback.answer(f"❌ Ошибка колонок: {e}", show_alert=True)
        return

    first_col_id = columns[0]["id"] if columns else None
    if not first_col_id:
        await callback.answer("❌ На доске нет колонок", show_alert=True)
        return

    created = 0
    errors = 0
    for task in tasks:
        title = (task.get("title") or "").strip()
        if not title:
            continue
        assignee_name = task.get("assignee")
        assignee_ids = None
        if assignee_name:
            uid = await client.resolve_user_by_name(assignee_name)
            if uid:
                assignee_ids = [uid]
        try:
            await client.create_card(
                title=title,
                description=task.get("description", ""),
                column_id=first_col_id,
                assignee_ids=assignee_ids,
                deadline=task.get("deadline"),
            )
            created += 1
        except Exception:
            errors += 1

    try:
        await callback.message.edit_text(
            f"✅ Задачи успешно добавлены на доску «{board_name}»!\n"
            f"Создано: {created}" + (f"\n⚠️ Ошибок: {errors}" if errors else ""),
        )
    except Exception:
        pass

    await state.clear()
    await callback.answer()
