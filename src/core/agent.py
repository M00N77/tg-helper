"""LLM-роутер интентов: свободный текст владельца → структурированное действие.

Подтверждение действий, видимых другим (отправка), решается на уровне хэндлера.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.llm.base import ChatMessage, LLMProvider
from src.llm.router import llm_with_fallback


logger = logging.getLogger(__name__)


# ── Pydantic-схема для Канбан-интентов ──────────────────────────────────────

class KanbanIntentResponse(BaseModel):
    """Структурированный ответ LLM-агента для управления Канбан-доской."""
    intent: Literal["create_task", "show_boards", "move_task", "smalltalk"]
    parameters: dict[str, Any] = Field(default_factory=dict)


AGENT_SYSTEM = """\
Ты роутер интентов для AI-ассистента в Telegram. Получаешь свободную фразу владельца и
возвращаешь СТРОГИЙ JSON-объект (без markdown-обёртки, без префиксов), описывающий
действие, которое нужно выполнить.

Доступные действия (поле "intent"):

1) "send_message"  — отправить сообщение конкретному контакту от имени владельца.
   Параметры:
     "recipient": "имя/ник/описание контакта",
     "text":      "финальный текст сообщения, в первом лице, без префиксов «передай»/«скажи»".
   Используй когда фраза вида «напиши/скажи/передай/отправь Х что Y».

2) "summarize_chat" — сделать саммари переписки с контактом.
   Параметры: "contact": "имя".

3) "tasks_for_chat" — извлечь задачи/обещания из переписки с контактом.
   Параметры: "contact": "имя".

4) "draft_reply"   — подготовить черновик ответа в чате с контактом.
   Параметры: "contact": "имя", "instruction": "опц. инструкция или null".

5) "catchup"       — «где мы остановились» + черновик ответа в чате с контактом.
   Параметры: "contact": "имя".

6) "search"        — поиск по моим сообщениям (по смыслу, не точному совпадению).
   Параметры: "query": "формулировка для поиска".

7) "news_digest"   — собрать новостной дайджест по теме из моих подписанных каналов.
   Параметры: "topic": "тема", "hours": int (окно часов; 24 если не указано).

8) "list_todos"    — показать мои открытые обещания.
   Параметров нет.

9) "set_setting"   — изменить одну настройку.
   Параметры: "key": <одно из перечисленного>, "value": <значение>.
   Допустимые ключи и форматы значений:
     - "auto_reply_enabled"        : true/false
     - "auto_reply_mode"           : "static" | "smart"
     - "auto_reply_text"           : строка (сам текст автоответа)
     - "auto_reply_cooldown_min"   : int (минуты)
     - "digest_enabled"            : true/false
     - "digest_time"               : "HH:MM" (в TZ владельца)
     - "news_enabled"              : true/false
     - "news_digest_time"          : "HH:MM"
     - "news_window_hours"         : int часов
     - "reminders_enabled"         : true/false
     - "reminder_lead_hours"       : int
     - "reminder_overdue_enabled"  : true/false
     - "ignore_archived"           : true/false
     - "use_heavy_model"           : true/false
       - "llm_provider"              : "openai" | "gemini" | "gigachat" | "groq"
     - "transcription_mode"        : "local" | "api" | "hybrid"
      - "timezone"                  : IANA (Europe/Moscow и т.п.)
      - "mtslink_token"            : строка (API-токен МТС Линк для создания встреч)
   Используй для фраз: «включи Х», «выключи Х», «дайджест в 7 утра», «часовой пояс Лондон»,
    «новости в 9», «не показывай архив», «переключись на gemini», «текст автоответа: …»,
    «сохрани MTS Link токен», «установи ключ MTS Link» и т.п.
   Если просят «новости в 9», ставь news_digest_time = "09:00" И ОТДЕЛЬНО news_enabled=true
   ВТОРЫМ интентом (см. ниже формат "multi"). Аналогично для дайджеста.

9.1) "find_in_chats" — найти чат по теме (когда конкретный контакт НЕ назван) и
   выполнить действие.
   Параметры:
     "query":  "ключевые слова для глобального поиска по моим перепискам в Telegram
                (на русском или английском, как в исходном сообщении)",
     "action": "catchup" | "summary" | "tasks" | "draft"  (по умолчанию "catchup").
   Используй когда фраза вида «в одном из чатов», «где-то я обсуждал», «у меня где-то
   был разговор про X», «на чём мы остановились с тем магазином мебели». Не выдумывай
   контакт — пусть пользователь выберет из найденных кнопками.

10) "add_news_topic" — добавить тему утренних авто-новостей.
    Параметры: "topic": "тема", "hours": int (опционально, default 24).
    Используй для «добавь тему Х», «следить за Х», «утром присылай по теме Х».

11) "remove_news_topic" — удалить тему утренних авто-новостей.
    Параметры: "topic": "подстрока для поиска темы".

11.1) "add_reminder" — поставить персональное напоминание (Commitment direction="mine").
    Параметры:
      "text": "что напомнить (одна фраза)",
      "when": "ISO-8601 datetime в UTC (например 2026-05-10T15:00:00Z) или null если нет даты",
      "peer_query": null | "имя контакта, если контекст связан с конкретным чатом".
    Используй для «напомни мне завтра в 18:00 позвонить маме», «поставь напоминание
    через час сделать X», «не забыть Y».

11.2) "remove_reminder" — снять напоминание/обещание.
    Параметры:
      "query": "подстрока в тексте напоминания или имени контакта".
    Используй для «убери напоминание про X», «выключи напоминание Y».

11.3) "add_reminders_from_chat" — извлечь обещания из чата с контактом и поставить как
    напоминания.
    Параметры: "contact": "имя контакта".
    Используй для «поставь напоминания из чата с Артёмом», «выкуси задачи из переписки
    с боссом и поставь напоминания».

11.4) "trash_task" — переместить обязательство в корзину (soft-delete).
    Параметры:
      "query": "подстрока в тексте обязательства".
    Используй для фраз: «удали обещание», «в корзину», «выброси задачу».

11.5) "restore_task" — восстановить обязательство из корзины.
    Параметры:
      "query": "подстрока в тексте обязательства".
    Используй для фраз: «восстанови обещание», «верни из корзины».

12) "chat"         — просто ответить владельцу текстом (общий вопрос/болтовня/совет/
    объяснение, не требующее действий с Telegram).
    Параметры: "reply": "готовый ответ в свободной форме (можно несколько абзацев,
    HTML aiogram-разметка допустима: <b>, <i>, <code>)".

13) "unknown"      — не получилось понять. Параметров нет.

ОСОБЫЙ СЛУЧАЙ — несколько действий за раз.
Если фраза требует НЕСКОЛЬКО действий ("включи новости и дайджест в 7 утра",
"добавь тему AI и пришли дайджест прямо сейчас"), верни:
  {"intent": "multi", "actions": [<intent-объект>, <intent-объект>, ...]}
где каждый элемент — обычный intent-объект (включая "set_setting" и пр.).

Возвращай ТОЛЬКО валидный JSON-объект. Никаких пояснений снаружи. Не выдумывай поля,
которых нет. Если для действия не хватает данных — выбери "chat" и в "reply" попроси
уточнение.

14) "create_task" — создать задачу на канбан-доске.
    Параметры:
      "title":       "название задачи (обязательно)",
      "description": "описание задачи (опционально)",
      "column":      "название колонки (опционально, по умолчанию первая)",
      "deadline":    "ISO-8601 дата дедлайна в TZ владельца (опционально, если в речи указан срок)",
      "assignee":    "имя исполнителя задачи (опционально, если в речи назван исполнитель)".
    Используй для фраз: «создай задачу», «добавь карточку», «новая задача: ...».

15) "show_boards" — показать текущее состояние канбан-доски.
    Параметров нет.
    Используй для фраз: «покажи доску», «что на канбане», «статус задач».

16) "move_task" — переместить задачу в другую колонку.
    Параметры:
      "task_title":    "название задачи (обязательно)",
      "target_column": "название колонки назначения (обязательно)".
    Используй для фраз: «перемести задачу Х в колонку Y», «передвинь карточку».

17) "smalltalk" — ответить на общий вопрос про канбан (без действия).
    Параметры: "reply": "текст ответа".

18) "schedule_meeting" — запланировать видеовстречу.
    Параметры:
      "title": "название встречи",
      "datetime": "ISO-8601 дата и время (опционально)".
    Используй для фраз: «созвон», «встреча», «запланируй митинг», «назначь звонок».

19) "join_meeting" — подключиться к существующей видеовстрече по ссылке.
    Параметры:
      "url": "ссылка на видеовстречу".
    Используй для фраз: «подключись к встрече», «зайди на созвон»,
    «ссылку на конференцию», а также когда пользователь отправляет
    ссылку вида telemost.yandex.ru, *.tolk.ru, my.mts-link.ru,
    jazz.sber.ru, zoom.us/j/... и т.п.

20) "meeting_summary" — показать итоги/расшифровку последней встречи.
    Параметров нет.
    Используй для фраз: «что обсудили на встрече», «итоги встречи»,
    «задачи со встречи», «расшифровку встречи».
"""

KANBAN_AGENT_SYSTEM = """\
Ты AI-агент для управления Канбан-доской (YouGile). Получаешь свободную фразу
пользователя и возвращаешь СТРОГИЙ JSON (без markdown-обёртки) в формате:
{"intent": "<intent>", "parameters": {<поля>}}.

Доступные интенты:

1) "create_task" — создать новую задачу на доске.
   Параметры:
     "title":       "название задачи (обязательно)",
     "description": "описание задачи (опционально)",
     "column":      "название колонки (опционально, по умолчанию первая)",
     "deadline":    "ISO-8601 дата дедлайна (опционально)",
     "assignee":    "имя исполнителя задачи (опционально)".

2) "show_boards" — показать содержимое канбан-доски (все колонки с задачами).
   Параметров нет.

3) "move_task" — переместить задачу в другую колонку.
   Параметры:
     "task_title":    "название задачи (обязательно)",
     "target_column": "название колонки назначения (обязательно)".

4) "smalltalk" — пользователь общается, а не просит действие.
   Параметры: "reply": "твой дружелюбный ответ (можно HTML-разметку: <b>, <i>, <code>)".

Примеры:
  {"intent": "create_task", "parameters": {"title": "Купить молоко", "column": "В работе"}}
  {"intent": "show_boards", "parameters": {}}
  {"intent": "move_task", "parameters": {"task_title": "Купить молоко", "target_column": "Готово"}}
  {"intent": "smalltalk", "parameters": {"reply": "Я умею создавать задачи и показывать доску!"}}

Возвращай ТОЛЬКО валидный JSON. Без пояснений снаружи.
Если не хватает данных — выбери "smalltalk" и попроси уточнение.
"""


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return text


def _safe_parse(raw: str) -> dict[str, Any]:
    raw = _strip_fence(raw)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and isinstance(parsed.get("intent"), str):
            return parsed
    except Exception:
        logger.warning("agent: bad JSON: %r", raw[:200])
    return {"intent": "unknown"}


def _safe_parse_kanban(raw: str) -> KanbanIntentResponse:
    raw = _strip_fence(raw)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and isinstance(parsed.get("intent"), str):
            return KanbanIntentResponse(**parsed)
    except Exception:
        logger.warning("kanban_agent: bad JSON: %r", raw[:200])
    return KanbanIntentResponse(
        intent="smalltalk",
        parameters={"reply": "Не понял запрос. Я умею: создавать задачи, показывать доску, перемещать задачи."},
    )


async def route_intent(
    providers: list[LLMProvider],
    user_text: str,
    *,
    heavy: bool = False,
    now_local: str | None = None,
    tz_name: str | None = None,
    history_block: str | None = None,
    notify_bot=None,
    notify_chat_id: int | None = None,
) -> dict[str, Any]:
    """now_local + tz_name инжектятся в системный промпт, чтобы LLM мог парсить
    относительные даты («завтра в 18:00») в корректный UTC ISO.
    history_block — краткосрочная память диалога владельца с ботом, чтобы понимать
    отсылки вроде «ему», «в том же чате»."""
    system = AGENT_SYSTEM
    if now_local and tz_name:
        system = (
            f"Текущее локальное время владельца: {now_local} ({tz_name}).\n"
            f"Когда нужно превратить относительную дату («завтра», «через час», «в пятницу 18:00») "
            f"в ISO-8601, возвращай дату в этом же TZ (не конвертируй в UTC).\n"
            f"Формат: «2026-06-09T10:00:00+03:00».\n\n"
            + system
        )
    if history_block:
        system = system + "\n\n" + history_block
    raw = await llm_with_fallback(
        providers,
        [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=user_text),
        ],
        heavy=heavy,
        notify_bot=notify_bot,
        notify_chat_id=notify_chat_id,
    )
    return _safe_parse(raw)


async def process_free_text(
    text: str,
    user_id: int,
    chat_id: int,
) -> str:
    """Вызывает LLM с канбан-промптом, парсит JSON в KanbanIntentResponse
    и маршрутизирует выполнение в YouGileClient. Возвращает ответ для пользователя.

    Параметры:
        text:     сырой текст пользователя (на русском или английском)
        user_id:  Telegram user_id (для доступа к LLM-ключу и настройкам)
        chat_id:  Telegram chat_id (для получения привязки к доске)

    Возвращает:
        str — готовый текст ответа (можно передать в message.answer())
    """
    from src.bot.handlers.yougile import YouGileClient
    from src.db.repo import get_or_create_user, get_team_by_chat
    from src.db.session import get_session
    from src.llm.router import get_provider_chain, llm_with_fallback

    async with get_session() as session:
        owner = await get_or_create_user(session, user_id)
        providers = await get_provider_chain(session, owner)
        if not providers:
            return "🔑 Нужен LLM-ключ. Добавь в /settings → 🔑 API-ключи."
        team = await get_team_by_chat(session, chat_id)

    board_id = team.active_board_id or team.kanban_board_id
    if not team or not team.kanban_token or not board_id:
        return "⚠️ Доска для задач не выбрана. Пожалуйста, выберите нужную доску в настройках команды, чтобы я мог создавать карточки."

    raw = await llm_with_fallback(providers, [
        ChatMessage(role="system", content=KANBAN_AGENT_SYSTEM),
        ChatMessage(role="user", content=text),
    ])

    response = _safe_parse_kanban(raw)
    client = YouGileClient(team.kanban_token, board_id)

    if response.intent == "create_task":
        title = response.parameters.get("title", "").strip()
        if not title:
            return "❓ Не понял название задачи. Уточни, что нужно создать."
        description = response.parameters.get("description", "").strip()
        column_query = response.parameters.get("column", "").strip()
        deadline_raw = response.parameters.get("deadline") or None
        assignee_name = response.parameters.get("assignee") or None

        try:
            columns = await client.get_columns()
        except Exception as e:
            logger.exception("get_columns failed")
            return f"❌ Не удалось получить колонки доски: {e}"

        if column_query:
            col = next(
                (c for c in columns if column_query.lower() in c.get("title", "").lower()),
                None,
            )
            if not col:
                names = ", ".join(c.get("title", "?") for c in columns)
                return f"❓ Колонка «{column_query}» не найдена. Доступны: {names}"
            column_id = col["id"]
        else:
            column_id = columns[0]["id"] if columns else None
            if not column_id:
                return "❌ На доске нет колонок."

        assignee_ids = None
        if assignee_name:
            uid = await client.resolve_user_by_name(assignee_name)
            if uid:
                assignee_ids = [uid]

        try:
            await client.create_card(title, description, column_id,
                                     assignee_ids=assignee_ids,
                                     deadline=deadline_raw)
        except Exception as e:
            logger.exception("create_card failed")
            return f"❌ Ошибка при создании задачи: {e}"

        board_name = team.active_board_name or "по умолчанию"
        tail = ""
        if assignee_ids:
            tail += f"\n👤 Исполнитель: {assignee_name}"
        if deadline_raw:
            tail += f"\n📅 Дедлайн: {deadline_raw}"
        return f"✅ Задача «{title}» создана!\n📋 Доска: {board_name}{tail}"

    if response.intent == "show_boards":
        try:
            columns = await client.get_columns()
        except Exception as e:
            return f"❌ Не удалось получить данные доски: {e}"

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
        return "\n".join(parts)[:4000]

    if response.intent == "move_task":
        task_query = response.parameters.get("task_title", "").strip()
        target_column = response.parameters.get("target_column", "").strip()
        if not task_query or not target_column:
            return "❓ Укажи, какую задачу и в какую колонку переместить."

        try:
            columns = await client.get_columns()
        except Exception as e:
            return f"❌ Ошибка при получении колонок: {e}"

        col = next(
            (c for c in columns if target_column.lower() in c.get("title", "").lower()),
            None,
        )
        if not col:
            names = ", ".join(c.get("title", "?") for c in columns)
            return f"❓ Колонка «{target_column}» не найдена. Доступны: {names}"

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
            return f"❓ Не нашёл задачу «{task_query}» на доске."

        try:
            await client.move_card(card_id, col["id"])
        except Exception as e:
            return f"❌ Ошибка при перемещении: {e}"

        return f"✅ Задача «{task_query}» перемещена в «{target_column}»!"

    if response.intent == "smalltalk":
        return response.parameters.get("reply", "Готов помочь с канбан-доской!")

    return "❓ Не понял запрос. Я умею: создавать задачи, показывать доску, перемещать задачи."
