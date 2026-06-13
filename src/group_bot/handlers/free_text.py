import logging
import re

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy import select

from src.config import settings as app_settings
from src.core.auth import check_user_permission
from src.core.agent import route_group_intent
from src.core.sentiment import analyze_sentiment_and_risk
from src.core.timeutil import now_in_tz
from src.db.models import TeamMember
from src.db.repo import (
    create_pending_action,
    delete_pending_action,
    find_team_member_by_name,
    get_or_create_user,
    get_pending_action,
    get_team_by_chat,
    get_team_member,
    list_team_members,
    ensure_team_member,
    save_message_sentiment,
    save_message_risk,
)
from src.db.session import get_session
from src.group_bot.filters import GroupOnly
from src.group_bot.permissions import is_admin
from src.bot.handlers.yougile import YouGileClient, get_board_id
from src.llm.router import get_provider_chain
import json
from aiogram.types import InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from src.db.repo import get_team_members

logger = logging.getLogger(__name__)

# Временное хранилище задач, ожидающих выбора исполнителя
# ключ: message_id сообщения с кнопками, значение: dict с данными задачи
_pending_task_selection: dict[int, dict] = {}

router = Router(name="group_free_text")
router.message.filter(GroupOnly())


async def _create_kanban_task(
    message: Message, team, title: str, description: str,
    deadline: str | None, target_member,
) -> None:
    board_id = get_board_id(team)
    if not team.kanban_token or not board_id:
        await message.reply("📊 Канбан команды не настроен. Обратитесь к директору.")
        return

    # Исполнитель не определён — показываем список участников
    if target_member is None:
        async with get_session() as session:
            members = await get_team_members(session, team.id)

        if not members:
            await message.reply("❌ В команде нет участников.")
            return

        kb = InlineKeyboardBuilder()
        for m in members:
            label = m.display_name or str(m.telegram_id)
            cb_data = f"pick_assignee:{m.telegram_id}"
            kb.row(InlineKeyboardButton(text=label, callback_data=cb_data))
        kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="pick_assignee:cancel"))

        sent = await message.reply(
            f"❓ Не нашёл исполнителя. Выберите из списка:\n"
            f"📝 <b>{title}</b>",
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
        # Сохраняем данные задачи для callback-хендлера
        _pending_task_selection[sent.message_id] = {
            "team_id": team.id,
            "title": title,
            "description": description,
            "deadline": deadline,
        }
        return

    # Исполнитель найден — создаём задачу сразу
    client = YouGileClient(team.kanban_token, board_id)
    try:
        columns = await client.get_columns()
        if not columns:
            await message.reply("❌ На доске нет колонок.")
            return
        column_id = columns[0]["id"]
        assignee_ids = [target_member.yougile_user_id] if target_member.yougile_user_id else []
        await client.create_card(
            title, description, column_id,
            assignee_ids=assignee_ids or None,
            deadline=deadline,
        )
    except Exception as e:
        logger.exception("create_card failed")
        await message.reply(f"❌ Ошибка при создании задачи: {e}")
        return

    who = (
        "себе"
        if target_member.telegram_id == message.from_user.id
        else f"<b>{target_member.display_name or target_member.telegram_id}</b>"
    )
    tail = f"\n📅 Срок: {deadline[:10]}" if deadline else ""
    await message.reply(f"✅ Задача «{title}» создана для {who}{tail}", parse_mode="HTML")


@router.callback_query(lambda c: c.data and c.data.startswith("pick_assignee:"))
async def on_pick_assignee(callback: CallbackQuery) -> None:
    await callback.answer()

    value = callback.data.split(":", 1)[1]
    msg_id = callback.message.message_id

    if value == "cancel":
        _pending_task_selection.pop(msg_id, None)
        await callback.message.edit_text("❌ Создание задачи отменено.")
        return

    task_data = _pending_task_selection.pop(msg_id, None)
    if not task_data:
        await callback.message.edit_text("❌ Данные задачи устарели. Попробуйте снова.")
        return

    if not value.isdigit():
        await callback.message.edit_text("❌ Неверный исполнитель.")
        return

    chosen_telegram_id = int(value)

    async with get_session() as session:
        from src.db.models import Team as TeamModel
        team = await session.get(TeamModel, task_data["team_id"])
        members = await get_team_members(session, task_data["team_id"])

    if not team:
        await callback.message.edit_text("❌ Команда не найдена.")
        return

    board_id = get_board_id(team)

    target = next((m for m in members if m.telegram_id == chosen_telegram_id), None)
    if not target:
        await callback.message.edit_text("❌ Участник не найден.")
        return

    if not target.yougile_user_id:
        try:
            client = YouGileClient(team.kanban_token, board_id)
            users = await client.get_users()
        except Exception:
            users = []

        if users:
            task_data["chosen_telegram_id"] = chosen_telegram_id
            task_data["users"] = users
            _pending_task_selection[msg_id] = task_data

            from src.bot.handlers.free_text import _build_yougile_user_keyboard
            kb = _build_yougile_user_keyboard(users, msg_id, prefix="yg_group")
            await callback.message.edit_text(
                f"👤 <b>{target.display_name or target.telegram_id}</b> выбран.\n"
                f"Привяжи его к аккаунту YouGile:",
                reply_markup=kb,
                parse_mode="HTML",
            )
            return

    client = YouGileClient(team.kanban_token, board_id)
    try:
        columns = await client.get_columns()
        if not columns:
            await callback.message.edit_text("❌ На доске нет колонок.")
            return
        column_id = columns[0]["id"]
        assignee_ids = [target.yougile_user_id] if target.yougile_user_id else []
        await client.create_card(
            task_data["title"],
            task_data["description"],
            column_id,
            assignee_ids=assignee_ids or None,
            deadline=task_data["deadline"],
        )
    except Exception as e:
        logger.exception("create_card failed in callback")
        await callback.message.edit_text(f"❌ Ошибка при создании задачи: {e}")
        return

    name = target.display_name or str(target.telegram_id)
    tail = f"\n📅 Срок: {task_data['deadline'][:10]}" if task_data.get("deadline") else ""
    await callback.message.edit_text(
        f"✅ Задача «{task_data['title']}» создана для <b>{name}</b>{tail}",
        parse_mode="HTML",
    )


async def _request_approval(
    message: Message, team, title: str, description: str, deadline: str | None,
    target_member, author,
) -> None:
    async with get_session() as session:
        admins = [m for m in await list_team_members(session, team.id) if m.role == "admin"]

    if not admins:
        await message.reply(
            "⚠️ В команде нет назначенного директора, согласование невозможно. "
            "Используйте /i_am_director."
        )
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, author.telegram_id)
        action = await create_pending_action(
            session,
            user_id=owner.id,
            kind="group_task_approval",
            payload={
                "chat_id": message.chat.id,
                "title": title,
                "description": description,
                "deadline": deadline,
                "target_telegram_id": target_member.telegram_id,
                "requested_by": author.telegram_id,
                "team_id": team.id,
            },
        )

    tags = " ".join(
        f'<a href="tg://user?id={a.telegram_id}">@{a.display_name or a.telegram_id}</a>'
        for a in admins
    )
    tail = f"\n📅 Срок: {deadline[:10]}" if deadline else ""
    desc_line = f"\n📝 {description}" if description else ""

    await message.reply(
        f"⏳ <b>{author.display_name or author.telegram_id}</b> просит создать задачу для "
        f"<b>{target_member.display_name or target_member.telegram_id}</b>:\n\n"
        f"📋 {title}{desc_line}{tail}\n\n"
        f"(заявка #{action.id})\n"
        f"{tags}, требуется согласование. Ответьте «да» на это сообщение чтобы подтвердить."
    )


async def _handle_create_task_for(message: Message, team, member, intent: dict) -> None:
    title = (intent.get("title") or "").strip()
    if not title:
        await message.reply("❓ Не понял, какую задачу создать. Уточни название.")
        return

    description = (intent.get("description") or "").strip()
    deadline = intent.get("deadline")
    assignee_query = (intent.get("assignee") or "себя").strip().lower()

    async with get_session() as session:
        members = await list_team_members(session, team.id)

        if assignee_query in ("себя", "мне", "меня", "я"):
            target_member = member
        else:
            target_member = await find_team_member_by_name(
                session, team.id, intent.get("assignee", ""),
            )
            if target_member is None:
                await message.reply(
                    f"❓ Не нашёл «{intent.get('assignee')}» среди участников команды."
                )
                return

    is_self = target_member.telegram_id == member.telegram_id
    author_is_admin = member.role == "admin"

    if is_self or author_is_admin:
        await _create_kanban_task(message, team, title, description, deadline, target_member)
    else:
        await _request_approval(message, team, title, description, deadline, target_member, member)


async def _handle_show_my_tasks(message: Message, team, member) -> None:
    board_id = get_board_id(team)
    if not team.kanban_token or not board_id:
        await message.reply("📊 Канбан команды не настроен. Обратитесь к директору.")
        return

    client = YouGileClient(team.kanban_token, board_id)
    try:
        columns = await client.get_columns()
    except Exception as e:
        await message.reply(f"❌ Ошибка при получении колонок: {e}")
        return

    parts = [f"📋 <b>Задачи для {member.display_name or 'вас'}</b>:\n"]
    found = 0
    for col in columns:
        try:
            cards = await client.get_cards_in_column(col["id"], limit=50)
        except Exception:
            continue
        for card in cards:
            assigned = card.get("assigned") or []
            if member.yougile_user_id and member.yougile_user_id in assigned:
                parts.append(f"  • {card.get('title', '?')[:50]}")
                found += 1
    if found == 0:
        await message.reply("✅ У вас нет активных задач на доске.")
        return
    await message.reply("\n".join(parts))


@router.message(F.reply_to_message & F.text.lower().in_(["да", "ok", "ок", "подтверждаю"]))
async def confirm_task_approval(message: Message) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not await is_admin(chat_id, user_id):
        return

    replied_text = message.reply_to_message.text or ""
    m = re.search(r"заявка #(\d+)", replied_text)
    if not m:
        return
    action_id = int(m.group(1))

    async with get_session() as session:
        action = await get_pending_action(session, action_id)
        if action is None or action.kind != "group_task_approval":
            return
        payload = action.payload
        team = await get_team_by_chat(session, chat_id)
        if team is None:
            return
        target_member = await get_team_member(session, team.id, payload["target_telegram_id"])
        if target_member is None:
            await message.reply("❌ Целевой участник больше не в команде.")
            return

    await _create_kanban_task(
        message, team, payload["title"], payload["description"],
        payload.get("deadline"), target_member,
    )

    async with get_session() as session:
        await delete_pending_action(session, action_id)


async def _find_task_by_title(client: YouGileClient, title_hint: str) -> dict | None:
    try:
        matches = await client.find_task_by_title(title_hint)
    except Exception:
        return None
    if not matches:
        return None
    return matches[0]


async def _is_task_owner(member, task: dict) -> bool:
    if not member.yougile_user_id:
        return False
    assigned = task.get("assigned") or []
    return member.yougile_user_id in assigned


async def _handle_edit_task(message: Message, team, member, intent: dict) -> None:
    title_hint = (intent.get("task_title_hint") or "").strip()
    if not title_hint:
        await message.reply("❓ Не понял, какую задачу редактировать. Уточни название.")
        return

    new_title = (intent.get("new_title") or "").strip()
    new_description = (intent.get("new_description") or "").strip()
    if not new_title and not new_description:
        await message.reply("❓ Укажи, что изменить: новое название или описание.")
        return

    board_id = get_board_id(team)
    if not team.kanban_token or not board_id:
        await message.reply("📊 Канбан команды не настроен.")
        return

    client = YouGileClient(team.kanban_token, board_id)
    task = await _find_task_by_title(client, title_hint)
    if not task:
        await message.reply(f"❓ Не нашёл задачу «{title_hint}» на доске.")
        return

    if member.role != "admin" and not await _is_task_owner(member, task):
        await message.reply("⛔ Только автор задачи или руководитель может её редактировать.")
        return

    payload = {}
    if new_title:
        payload["title"] = new_title
    if new_description:
        payload["description"] = new_description

    try:
        await client.update_card(task["id"], **payload)
    except Exception as e:
        logger.exception("edit_task failed")
        await message.reply(f"❌ Ошибка при обновлении задачи: {e}")
        return

    parts = []
    if new_title:
        parts.append(f"название → «{new_title}»")
    if new_description:
        parts.append("описание обновлено")
    await message.reply(f"✅ Задача «{task.get('title', '?')}» обновлена: {', '.join(parts)}.")


async def _handle_transfer_deadline(message: Message, team, member, intent: dict) -> None:
    title_hint = (intent.get("task_title_hint") or "").strip()
    new_deadline = intent.get("new_deadline")

    if not title_hint:
        await message.reply("❓ Не понял, у какой задачи перенести срок. Уточни название.")
        return
    if not new_deadline:
        await message.reply("❓ Не понял, на какой срок перенести. Уточни дату.")
        return

    board_id = get_board_id(team)
    if not team.kanban_token or not board_id:
        await message.reply("📊 Канбан команды не настроен.")
        return

    client = YouGileClient(team.kanban_token, board_id)
    task = await _find_task_by_title(client, title_hint)
    if not task:
        await message.reply(f"❓ Не нашёл задачу «{title_hint}» на доске.")
        return

    if member.role != "admin" and not await _is_task_owner(member, task):
        await message.reply("⛔ Только автор задачи или руководитель может менять срок.")
        return

    try:
        from src.bot.handlers.yougile import _parse_deadline
        deadline_payload = _parse_deadline(new_deadline)
        await client.update_card(task["id"], **deadline_payload)
    except Exception as e:
        logger.exception("transfer_deadline failed")
        await message.reply(f"❌ Ошибка при обновлении срока: {e}")
        return

    tail = new_deadline[:10] if len(new_deadline) > 10 else new_deadline
    await message.reply(f"✅ Срок задачи «{task.get('title', '?')}» перенесён на {tail}.")


async def _handle_change_assignee(message: Message, team, member, intent: dict) -> None:
    title_hint = (intent.get("task_title_hint") or "").strip()
    new_assignee_name = (
        intent.get("new_assignee_name")
        or intent.get("assignee")
        or ""
    ).strip()

    if not title_hint:
        await message.reply("❓ Не понял, у какой задачи сменить ответственного. Уточни название.")
        return
    if not new_assignee_name:
        await message.reply("❓ Не понял, на кого переназначить. Уточни имя.")
        return

    board_id = get_board_id(team)
    if not team.kanban_token or not board_id:
        await message.reply("📊 Канбан команды не настроен.")
        return

    # Ищем исполнителя в YouGile (не в TeamMember)
    client = YouGileClient(team.kanban_token, board_id)
    try:
        users = await client.get_users()
    except Exception as e:
        await message.reply(f"❌ Не удалось получить участников доски: {e}")
        return

    if not users:
        await message.reply("❌ На доске нет участников.")
        return

    new_assignee_query = new_assignee_name.lower()
    matched_user = next(
        (u for u in users
         if new_assignee_query and new_assignee_query in u.get("name", "").lower()),
        None
    )

    if not matched_user:
        names = "\n".join(f"• {u.get('name', '?')}" for u in users[:15])
        await message.reply(
            f"❓ Участник «{new_assignee_name}» не найден на доске.\n\n"
            f"<b>Участники доски:</b>\n{names}"
        )
        return

    yougile_user_id = matched_user["id"]

    # Проверка прав: админ делает без согласования
    if member.role != "admin":
        async with get_session() as session:
            from src.db.repo import find_team_member_by_name
            target_member = await find_team_member_by_name(session, team.id, new_assignee_name)

        if target_member:
            await _request_approval(
                message, team,
                title=title_hint,
                description=f"Смена ответственного на {new_assignee_name}",
                deadline=None,
                target_member=target_member,
                author=member,
            )
            return

    task = await _find_task_by_title(client, title_hint)
    if not task:
        await message.reply(f"❓ Не нашёл задачу «{title_hint}» на доске.")
        return

    try:
        await client.update_card(task["id"], assigned=[yougile_user_id])
    except Exception as e:
        logger.exception("change_assignee failed")
        await message.reply(f"❌ Ошибка при смене ответственного: {e}")
        return

    await message.reply(
        f"✅ Задача «{task.get('title', '?')}» переназначена на <b>{matched_user.get('name', new_assignee_name)}</b>."
    )


async def _handle_close_task(message: Message, team, member, intent: dict) -> None:
    title_hint = (intent.get("task_title_hint") or "").strip()
    if not title_hint:
        await message.reply("❓ Не понял, какую задачу закрыть. Уточни название.")
        return

    if member.role != "admin":
        await message.reply("⛔ Только руководитель может закрыть задачу.")
        return

    board_id = get_board_id(team)
    if not team.kanban_token or not board_id:
        await message.reply("📊 Канбан команды не настроен.")
        return

    client = YouGileClient(team.kanban_token, board_id)
    task = await _find_task_by_title(client, title_hint)
    if not task:
        await message.reply(f"❓ Не нашёл задачу «{title_hint}» на доске.")
        return

    try:
        columns = await client.get_columns()
        done_col = None
        for col in columns:
            title_lower = col.get("title", "").lower()
            if title_lower in ("done", "готово", "closed", "завершено", "выполнено"):
                done_col = col
                break
        if done_col:
            await client.move_card(task["id"], done_col["id"])
        else:
            await client.update_card(task["id"], completed=True)
    except Exception as e:
        logger.exception("close_task failed")
        await message.reply(f"❌ Ошибка при закрытии задачи: {e}")
        return

    await message.reply(f"✅ Задача «{task.get('title', '?')}» закрыта.")


async def _handle_comment_task(message: Message, team, member, intent: dict) -> None:
    title_hint = (intent.get("task_title_hint") or "").strip()
    comment = (intent.get("comment") or "").strip()

    if not title_hint:
        await message.reply("❓ Не понял, к какой задаче комментарий. Уточни название.")
        return
    if not comment:
        await message.reply("❓ Пустой комментарий. Напиши текст заметки.")
        return

    board_id = get_board_id(team)
    if not team.kanban_token or not board_id:
        await message.reply("📊 Канбан команды не настроен.")
        return

    client = YouGileClient(team.kanban_token, board_id)
    task = await _find_task_by_title(client, title_hint)
    if not task:
        await message.reply(f"❓ Не нашёл задачу «{title_hint}» на доске.")
        return

    # Комментировать может автор задачи или руководитель.
    if member.role != "admin" and not await _is_task_owner(member, task):
        await message.reply("⛔ Только автор задачи или руководитель может её комментировать.")
        return

    author = member.display_name or str(member.telegram_id)
    body = f"💬 {author}: {comment}"
    try:
        await client.add_comment(task["id"], body)
    except Exception as e:
        logger.exception("comment_task failed")
        await message.reply(f"❌ Ошибка при добавлении комментария: {e}")
        return

    await message.reply(f"✅ Комментарий добавлен к задаче «{task.get('title', '?')}».")


@router.message(F.text & ~F.text.startswith("/"))
async def group_free_text(message: Message) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id
    display_name = message.from_user.full_name

    async with get_session() as session:
        team = await get_team_by_chat(session, chat_id)
        if team is None:
            return

        member = await ensure_team_member(session, team.id, user_id, display_name)
        owner_for_llm = await get_or_create_user(session, message.from_user.id)
        providers = await get_provider_chain(session, owner_for_llm)

        if not providers:
            owner_for_llm = await get_or_create_user(session, app_settings.owner_telegram_id)
            providers = await get_provider_chain(session, owner_for_llm)
        if not providers:
            await message.answer("🔑 Нужен LLM-ключ. Добавь в /settings → 🔑 API-ключи.")
            return

        tz_name = owner_for_llm.settings.timezone or "UTC"

        result = await analyze_sentiment_and_risk(message.text, providers[0])
        if result is not None:
            logger.info(
                "sentiment_check: chat=%s user=%s has_risk=%s sentiment=%s text=%r",
                message.chat.id, message.from_user.id,
                result.has_risk, result.sentiment, message.text[:80],
            )
            await save_message_sentiment(
                session,
                team_id=team.id,
                user_id=message.from_user.id,
                display_name=message.from_user.full_name,
                sentiment=result.sentiment,
            )
            if result.has_risk:
                await save_message_risk(
                    session,
                    team_id=team.id,
                    user_id=message.from_user.id,
                    display_name=message.from_user.full_name,
                    message_text=message.text,
                    risk_reason=result.risk_reason,
                )
                await message.answer(
                    f"⚠️ <b>Обнаружен риск</b>\n"
                    f"👤 {message.from_user.full_name}\n"
                    f"📝 {result.risk_reason}",
                    disable_notification=True,
                )

    now_local_str = now_in_tz(tz_name).strftime("%Y-%m-%d %H:%M")

    try:
        intent = await route_group_intent(
            providers[0], message.text, now_local=now_local_str, tz_name=tz_name,
        )
    except Exception:
        logger.exception("group agent failed")
        return

    kind = intent.get("intent")

    async with get_session() as check_session:
        allowed = await check_user_permission(kind, member, check_session)
    if not allowed:
        await message.answer("⛔ Доступ запрещён для вашей роли.")
        return

    if kind == "chat":
        reply = intent.get("reply", "")
        if reply:
            await message.reply(reply)
        return

    if kind == "show_my_tasks":
        await _handle_show_my_tasks(message, team, member)
        return

    if kind == "create_task_for":
        await _handle_create_task_for(message, team, member, intent)
        return

    if kind == "edit_task":
        await _handle_edit_task(message, team, member, intent)
        return

    if kind == "transfer_deadline":
        await _handle_transfer_deadline(message, team, member, intent)
        return

    if kind == "change_assignee":
        await _handle_change_assignee(message, team, member, intent)
        return

    if kind == "close_task":
        await _handle_close_task(message, team, member, intent)
        return

    if kind == "comment_task":
        await _handle_comment_task(message, team, member, intent)
        return

    if kind == "notify_team":
        notify_text = (
            intent.get("message")
            or intent.get("parameters", {}).get("message")
            or ""
        ).strip()
        if not notify_text:
            await message.reply("Укажи текст оповещения.")
            return

        try:
            chat_members = []
            async for cm in message.bot.get_chat_members(message.chat.id):
                if not cm.user.is_bot and cm.user.id != message.from_user.id:
                    name = cm.user.full_name or str(cm.user.id)
                    chat_members.append(
                        f'<a href="tg://user?id={cm.user.id}">{name}</a>'
                    )
            if chat_members:
                mentions = chat_members
            else:
                mentions = []
        except Exception:
            async with get_session() as session:
                from src.db.models import TeamMember, User
                from sqlalchemy import select
                result = await session.execute(
                    select(TeamMember).where(TeamMember.team_id == team.id)
                )
                members_list = list(result.scalars().all())
                mentions = []
                for m in members_list:
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
            await message.reply(
                f"📢 <b>Оповещение:</b>\n{notify_text}\n\n"
                f"(Участников для тега не найдено)"
            )
            return

        tags = " ".join(mentions)
        await message.reply(
            f"📢 <b>Оповещение для команды:</b>\n\n"
            f"{notify_text}\n\n"
            f"{tags}",
            parse_mode="HTML",
        )
        return


    if kind == "schedule_meeting":
        title = (
            intent.get("title")
            or intent.get("parameters", {}).get("title")
            or "Встреча"
        )
        datetime_str = (
            intent.get("datetime")
            or intent.get("parameters", {}).get("datetime")
        )

        async with get_session() as session:
            team = await get_team_by_chat(session, message.chat.id)

        # Получить mtslink_token
        mtslink_token = None
        if team:
            try:
                from src.crypto import decrypt
                from src.db.models import ApiKey
                from sqlalchemy import select
                async with get_session() as session:
                    result = await session.execute(
                        select(ApiKey).where(ApiKey.provider == "mtslink")
                    )
                    key_row = result.scalar_one_or_none()
                    if key_row:
                        mtslink_token = decrypt(key_row.key_enc)
            except Exception as e:
                logger.warning("mtslink token fetch failed: %s", e)

        try:
            from src.services.meeting_room import create_meeting_room
            url, event_id, session_id = await create_meeting_room(
                title, mtslink_token, datetime_str,
                team_chat_id=message.chat.id if mtslink_token else None,
            )
        except Exception as e:
            await message.answer(f"❌ Не удалось создать встречу: {e}")
            return

        # Сохранить встречу в БД
        if team:
            from src.bot.handlers.meeting import detect_platform
            platform = detect_platform(url)
            async with get_session() as session:
                from src.db.repo import create_meeting
                await create_meeting(
                    session, team.id, url, platform,
                    mtslink_event_id=event_id,
                    mtslink_session_id=session_id,
                )

        await message.answer(
            f"📅 <b>Встреча создана</b>\n\n"
            f"Название: <b>{title}</b>\n"
            f"Ссылка: <code>{url}</code>\n\n"
            f"Отправь эту ссылку участникам для подключения.",
            parse_mode="HTML",
        )
        return

    if kind == "start_pulse":
        try:
            from src.group_bot.handlers.pulse import start_pulse_survey
            await start_pulse_survey(message)
        except ImportError:
            from aiogram.types import InlineKeyboardButton
            from aiogram.utils.keyboard import InlineKeyboardBuilder
            kb = InlineKeyboardBuilder()
            kb.row(
                InlineKeyboardButton(text="😊 Хорошо", callback_data="pulse:good"),
                InlineKeyboardButton(text="😐 Нормально", callback_data="pulse:ok"),
                InlineKeyboardButton(text="😟 Плохо", callback_data="pulse:bad"),
            )
            await message.answer(
                "🔔 <b>Пульс-опрос команды</b>\n\n"
                "Как твоё настроение сегодня?",
                reply_markup=kb.as_markup(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("start_pulse_survey failed: %s", e)
            await message.answer(
                "❌ Не удалось запустить опрос. Попробуй команду /pulse напрямую."
            )
        return

    if kind == "show_pulse_results":
        try:
            from src.group_bot.handlers.pulse import show_pulse_results
            await show_pulse_results(message)
        except ImportError:
            async with get_session() as session:
                team = await get_team_by_chat(session, message.chat.id)
                if not team:
                    await message.answer("Команда не настроена.")
                    return
                from src.db.repo import get_recent_risks
                risks = await get_recent_risks(session, team.id, limit=5)
            if not risks:
                await message.answer("📋 Данных пульс-опроса пока нет.")
            else:
                lines = ["📋 <b>Последние сигналы команды:</b>\n"]
                for r in risks:
                    lines.append(
                        f"• {r.display_name}: {r.risk_reason} "
                        f"<i>({r.created_at.strftime('%d.%m %H:%M')})</i>"
                    )
                await message.answer("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.warning("show_pulse_results failed: %s", e)
            await message.answer("Используй /pulse results.")
        return


@router.callback_query(F.data.startswith("yg_group:"))
async def cb_yg_group_assign(callback: CallbackQuery) -> None:
    """Выбор YouGile-пользователя для привязки в групповом боте."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.")
        return

    msg_id = int(parts[1])
    yougile_user_id = parts[2] if parts[2] != "none" else None

    task_data = _pending_task_selection.pop(msg_id, None)
    if not task_data:
        await callback.answer("Данные устарели.")
        try:
            await callback.message.edit_text("⏱ Время выбора истекло.")
        except Exception:
            pass
        return

    chosen_telegram_id = task_data.get("chosen_telegram_id")
    team_id = task_data["team_id"]

    async with get_session() as session:
        from src.db.models import Team as TeamModel
        team = await session.get(TeamModel, team_id)
        members = await get_team_members(session, team_id)

    if not team:
        await callback.message.edit_text("❌ Команда не найдена.")
        return

    board_id = get_board_id(team)

    target = next((m for m in members if m.telegram_id == chosen_telegram_id), None)

    if yougile_user_id and target:
        try:
            async with get_session() as session:
                from src.db.repo import set_team_member_yougile_id
                await set_team_member_yougile_id(
                    session, team_id, target.telegram_id, yougile_user_id
                )
        except Exception as e:
            logger.warning("set_team_member_yougile_id failed: %s", e)

    assignee_ids = [yougile_user_id] if yougile_user_id else []

    try:
        client = YouGileClient(team.kanban_token, board_id)
        columns = await client.get_columns()
        if not columns:
            await callback.message.edit_text("❌ На доске нет колонок.")
            return
        column_id = columns[0]["id"]
        await client.create_card(
            task_data["title"],
            task_data.get("description", ""),
            column_id,
            assignee_ids=assignee_ids or None,
            deadline=task_data.get("deadline"),
        )
    except Exception as e:
        logger.exception("create_card failed")
        await callback.message.edit_text(f"❌ Ошибка при создании задачи: {e}")
        return

    name = target.display_name if target else str(chosen_telegram_id or "?")
    assignee_display = "без исполнителя"
    if yougile_user_id:
        users = task_data.get("users", [])
        matched = next((u for u in users if u.get("id") == yougile_user_id), None)
        assignee_display = matched.get("name") if matched else yougile_user_id
    tail = f"\n📅 Срок: {task_data['deadline'][:10]}" if task_data.get("deadline") else ""
    await callback.message.edit_text(
        f"✅ Задача «{task_data['title']}» создана для <b>{name}</b> ({assignee_display}){tail}",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pulse:"))
async def cb_pulse_simple(callback: CallbackQuery) -> None:
    value = callback.data.split(":", 1)[1] if ":" in callback.data else ""
    labels = {"good": "😊 Хорошо", "ok": "😐 Нормально", "bad": "😟 Плохо"}
    label = labels.get(value, value)
    await callback.answer(f"Ты выбрал: {label}")
    await callback.message.edit_text(
        f"✅ Спасибо за ответ! Твой выбор: {label}\n\n"
        f"Результаты опроса будут доступны позже.",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("yg_group_page:"))
async def cb_yg_group_page(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer()
        return

    msg_id = int(parts[1])
    page = int(parts[2])

    task_data = _pending_task_selection.get(msg_id)
    if not task_data:
        await callback.answer("Время выбора истекло.")
        return

    users = task_data.get("users", [])
    from src.bot.handlers.free_text import _build_yougile_user_keyboard
    kb = _build_yougile_user_keyboard(users, msg_id, page=page, page_size=8)
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass
    await callback.answer()
