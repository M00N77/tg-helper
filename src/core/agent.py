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

8.1) "show_my_tasks" — показать задачи владельца.
     Параметров нет.
     Используй для фраз: «покажи мои задачи», «что мне делать сегодня»,
     «мои задачи на день», «что у меня запланировано».
      Если есть привязанная канбан-доска — показать задачи с неё.
      Если доски нет — показать открытые commitments как fallback.

  9.5) "show_team_risks" — показать последние обнаруженные риски команды.
      Параметров нет.
      Используй для фраз: «покажи риски», «риск-анализ», «риски команды»,
      «невыполнение задач», «обнаруженные риски», «что угрожает проекту».
      Пример: {"intent": "show_team_risks", "parameters": {}}

  9.6) "show_team_sentiment" — показать анализ тональности/настроения команды.
      Параметров нет.
      Используй для фраз: «эмоциональный фон», «настроение команды»,
      «анализ настроений», «кто выгорает», «негатив в сообщениях»,
      «психологический климат», «как себя чувствует команда».
      Пример: {"intent": "show_team_sentiment", "parameters": {}}

  9.7) "show_pulse_results" — показать результаты последнего пульс-опроса.
      Параметров нет.
      Используй для фраз: «результаты опроса», «покажи ответы на опрос»,
      «предыдущий опрос», «итоги пульс-опроса».
      Пример: {"intent": "show_pulse_results", "parameters": {}}

  9.8) "start_pulse" — запустить пульс-опрос.
      Параметров нет.
      Используй для фраз: «запусти пульс-опрос», «проведи опрос»,
      «опрос команды сейчас», «пульс-опрос».
      Пример: {"intent": "start_pulse", "parameters": {}}

  9.9) "show_task_report" — показать отчёт/статистику по задачам команды.
      Параметров нет.
      Используй для фраз: «отчёт по задачам», «статистика задач»,
      «сколько задач выполнено», «прогресс команды», «задачи команды»,
      «покажи доску», «что делает команда».
      Пример: {"intent": "show_task_report", "parameters": {}}

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
      "when": "ISO-8601 datetime в UTC (например 2026-05-10T15:00:00Z) или null если нет даты.
               Если пользователь назвал только время (например «в 16:00») без даты —
               используй текущую дату из системного промпта + указанное время.
               Часовой пояс — из переменной tz_name системного промпта.",
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

Примеры multi:
- «верни задачу из корзины и поставь напоминание»:
  {"intent": "multi", "actions": [
    {"intent": "restore_kanban_task", "task_title": "название задачи"},
    {"intent": "add_reminder", "text": "напоминание", "when": "ISO-datetime"}
  ]}

  Пример — «верни задачу из корзины и поставь напоминание на 16:00»
  (время без даты = сегодня в этом часовом поясе):
  {"intent": "multi", "actions": [
    {"intent": "restore_kanban_task", "task_title": "название задачи"},
    {"intent": "add_reminder", "text": "постанализ за квартал",
     "when": "2026-06-13T16:00:00+03:00"}
  ]}

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
    НЕ используй если в фразе есть слова «собрание», «созвон», «встреча», «митинг»,
    «онлайн-собрание» — это интент schedule_meeting.

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
    Используй для фраз: «созвон», «встреча», «запланируй митинг», «назначь звонок»,
    «онлайн-собрание», «пригласи команду на собрание», «собери команду онлайн»,
    «проведи собрание», «организуй встречу», «пригласи на встречу».
    Используй ТАКЖЕ для: «создай встречу», «создай онлайн-собрание»,
    «пригласи команду и создай встречу», «организуй и создай встречу».
    ВАЖНО: если в фразе есть «встреча» + «создай» — это schedule_meeting,
    а НЕ create_task.

    Пример:
    «Пригласи команду на онлайн-собрание сейчас и создай встречу» →
    {"intent": "multi", "actions": [
      {"intent": "schedule_meeting", "parameters": {"title": "Онлайн-собрание"}},
      {"intent": "notify_team", "parameters": {"message": "Скоро онлайн-собрание, подключайтесь!"}}
    ]}

19) "notify_team" — оповестить всех участников команды сообщением с тегами.
    Параметры:
      "message": "текст оповещения (обязательно)".
    Используй для фраз: «оповести всех», «уведоми команду»,
    «напомни всем», «тегни всех», «скажи команде»,
    «сообщи участникам», «оповести что скоро встреча»,
    «предупреди всех».
    Пример:
    {"intent": "notify_team",
     "parameters": {"message": "Скоро начнётся встреча, подключайтесь!"}}

20) "join_meeting" — подключиться к существующей видеовстрече по ссылке.
    Параметры:
      "url": "ссылка на видеовстречу".
    Используй для фраз: «подключись к встрече», «зайди на созвон»,
    «ссылку на конференцию», а также когда пользователь отправляет
    ссылку вида telemost.yandex.ru, *.tolk.ru, my.mts-link.ru,
    jazz.sber.ru, zoom.us/j/... и т.п.

20.1) "restore_kanban_task" — вернуть задачу из корзины/архива обратно на доску.
      Параметры: "task_title": "название задачи (обязательно)".
      Используй для фраз: «верни из корзины», «восстанови задачу»,
      «достань из архива», «удалённая задача».
      ВАЖНО: эта задача находится в YouGile, а не в личных todo/commitments.
      НЕ путай с restore_reminder (для личных напоминаний) и
      НЕ используй move_task (он ищет на активной доске, а не в корзине).

21) "meeting_summary" — показать итоги/расшифровку последней встречи.
    Параметров нет.
    Используй для фраз: «что обсудили на встрече», «итоги встречи»,
    «задачи со встречи», «расшифровку встречи».

"""

KANBAN_AGENT_SYSTEM = """\
Ты AI-агент для управления Канбан-доской (YouGile). Получаешь свободную фразу
пользователя и возвращаешь СТРОГИЙ JSON (без markdown-обёртки) в формате:
{"intent": "<intent>", "parameters": {<поля>}}.

Доступные интенты:

1) "create_task" — создать одну задачу на доске.
   Параметры:
     "title":       "название задачи (обязательно)",
     "description": "описание (опционально)",
     "column":      "название колонки (опционально)",
     "assignee":    "имя исполнителя (опционально)".

   ВАЖНО: Если пользователь называет несколько задач через «и», «,»
   или перечислением — создай НЕСКОЛЬКО задач через multi, не объединяй
   их в одну.

   Примеры когда нужен multi:
   - «создай задачу X и Y» →
     {"intent": "multi", "actions": [
       {"intent": "create_task", "parameters": {"title": "X"}},
       {"intent": "create_task", "parameters": {"title": "Y"}}
     ]}
   - «создай задачи: доделать MVP, подготовить сценарий» →
     {"intent": "multi", "actions": [
       {"intent": "create_task", "parameters": {"title": "Доделать MVP"}},
       {"intent": "create_task", "parameters": {"title": "Подготовить сценарий"}}
     ]}

   Признаки нескольких задач в одном сообщении:
   - союз «и» между глаголами («доделать … и подготовить …»)
   - перечисление через запятую
   - слово «задачи» во множественном числе

   Каждая задача — отдельный глагольный оборот. Разбивай по смыслу.

   Если нужно создать НЕСКОЛЬКО задач — используй multi:
   {"intent": "multi", "actions": [
     {"intent": "create_task", "parameters": {"title": "задача 1", "assignee": "Имя"}},
     {"intent": "create_task", "parameters": {"title": "задача 2", "assignee": "Имя"}}
   ]}

   НЕ возвращай несколько JSON-объектов подряд без запятой — это невалидный JSON.
   НЕ используй интент "create_task_for" — его не существует.

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
  {"intent": "multi", "actions": [
    {"intent": "create_task", "parameters": {"title": "Сделать презентацию", "assignee": "Иван"}},
    {"intent": "create_task", "parameters": {"title": "Доделать MVP", "assignee": "Пётр"}}
  ]}
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

    # Шаг 1: стандартный парсинг
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and isinstance(parsed.get("intent"), str):
            return parsed
    except json.JSONDecodeError:
        pass

    # Шаг 2: извлечь первый валидный JSON-объект
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(raw[start:end + 1])
            if isinstance(parsed, dict) and isinstance(parsed.get("intent"), str):
                return parsed
        except json.JSONDecodeError:
            pass

    # Шаг 3: если два объекта подряд — завернуть в multi
    import re
    objects = []
    for m in re.finditer(r'\{', raw):
        depth = 0
        for i in range(m.start(), len(raw)):
            if raw[i] == '{':
                depth += 1
            elif raw[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(raw[m.start():i + 1])
                        if isinstance(obj.get("intent"), str):
                            objects.append(obj)
                    except Exception:
                        pass
                    break
        if len(objects) >= 10:
            break

    if len(objects) == 1:
        return objects[0]
    if len(objects) > 1:
        return {"intent": "multi", "actions": objects}

    logger.warning("agent: bad JSON: %r", raw[:200])
    return {"intent": "unknown"}


def _safe_parse_kanban(raw: str) -> list[KanbanIntentResponse]:
    """Парсит ответ LLM. Возвращает список интентов."""
    # Шаг 1: извлечь JSON из "грязного" ответа
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        import re
        objects = re.findall(r'\{[^{}]+\}', raw, re.DOTALL)
        if objects:
            results = []
            for obj in objects:
                try:
                    parsed = json.loads(obj)
                    if isinstance(parsed.get("intent"), str):
                        if parsed["intent"] == "create_task_for":
                            parsed["intent"] = "create_task"
                        results.append(KanbanIntentResponse(**parsed))
                except Exception:
                    continue
            if results:
                return results
        logger.warning("kanban_agent: no JSON found: %r", raw[:200])
        return [KanbanIntentResponse(
            intent="smalltalk",
            parameters={"reply": "Не понял запрос."},
        )]

    raw = raw[start:end + 1]

    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("not a dict")

        if parsed.get("intent") == "create_task_for":
            parsed["intent"] = "create_task"

        if parsed.get("intent") == "multi":
            actions = parsed.get("actions", [])
            results = []
            for a in actions:
                if not isinstance(a, dict):
                    continue
                if a.get("intent") == "create_task_for":
                    a["intent"] = "create_task"
                if isinstance(a.get("intent"), str):
                    try:
                        results.append(KanbanIntentResponse(**a))
                    except Exception:
                        continue
            return results or [KanbanIntentResponse(
                intent="smalltalk",
                parameters={"reply": "Не понял запрос."},
            )]

        if isinstance(parsed.get("intent"), str):
            return [KanbanIntentResponse(**parsed)]
    except Exception:
        logger.warning("kanban_agent: bad JSON: %r", raw[:200])
    return [KanbanIntentResponse(
        intent="smalltalk",
        parameters={"reply": "Не понял запрос. Я умею: создавать задачи, показывать доску, перемещать задачи."},
    )]


async def route_intent(
    providers: list[LLMProvider],
    user_text: str,
    *,
    heavy: bool = False,
    now_local: str | None = None,
    tz_name: str | None = None,
    dictionary_block: str | None = None,
    history_block: str | None = None,
    notify_bot=None,
    notify_chat_id: int | None = None,
) -> dict[str, Any]:
    """now_local + tz_name инжектятся в системный промпт, чтобы LLM мог парсить
    относительные даты («завтра в 18:00») в корректный UTC ISO.
    dictionary_block — найденные в тексте термины из словаря команды.
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
    if dictionary_block:
        system = dictionary_block + "\n\n" + system
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


GROUP_AGENT_SYSTEM = """\
Ты роутер интентов для AI-ассистента в РАБОЧЕМ ГРУППОВОМ чате команды. Получаешь
свободную фразу участника и возвращаешь СТРОГИЙ JSON-объект (без markdown-обёртки),
описывающий действие с командной Канбан-доской.

Доступные действия (поле "intent"):

1) "create_task_for" — создать задачу на доске (себе или другому участнику).
   Поля:
     "title":       "название задачи (обязательно)",
     "description": "описание (опционально, иначе пустая строка)",
     "deadline":    "ISO-8601 дата в TZ команды или null",
     "assignee":    "имя исполнителя; если задача себе — 'себя'".
   Используй для: «создай задачу …», «поставь Ивану задачу …», «мне нужно …».

2) "show_my_tasks" — показать задачи текущего участника на доске.
   Полей нет. Используй для: «мои задачи», «что на мне», «покажи мои таски».

3) "edit_task" — изменить название/описание существующей задачи.
   Поля:
     "task_title_hint": "часть названия задачи для поиска (обязательно)",
     "new_title":       "новое название или пустая строка",
     "new_description": "новое описание или пустая строка".
   Используй для: «переименуй задачу …», «измени описание …».

4) "transfer_deadline" — перенести срок задачи.
   Поля:
     "task_title_hint": "часть названия задачи (обязательно)",
     "new_deadline":    "ISO-8601 дата в TZ команды (обязательно)".
   Используй для: «перенеси срок …», «дедлайн … на пятницу».

5) "change_assignee" — сменить ответственного по задаче.
   Поля:
     "task_title_hint":   "часть названия задачи (обязательно)",
     "new_assignee_name": "имя нового ответственного (обязательно)".
   Используй для: «переназначь … на Петра», «отдай задачу … Маше».

6) "close_task" — закрыть/завершить задачу.
   Поля: "task_title_hint": "часть названия задачи (обязательно)".
   Используй для: «закрой задачу …», «задача … готова», «заверши …».

7) "comment_task" — добавить комментарий к существующей задаче.
   Поля:
     "task_title_hint": "часть названия задачи (обязательно)",
     "comment":         "текст комментария (обязательно)".
   Используй для: «прокомментируй задачу … : …», «добавь комментарий к … : …»,
   «оставь заметку по задаче …».

8) "notify_team" — оповестить всех участников команды сообщением с тегами.
   Поля:
     "message": "текст оповещения (обязательно)".
   Используй для: «оповести всех», «напомни всем», «тегни всех»,
   «скажи команде», «предупреди всех», «уведоми команду».

9) "chat" — обычная реплика/вопрос, не требующий действий с доской.
   Поля: "reply": "короткий дружелюбный ответ (HTML aiogram: <b>, <i>, <code>)".
   Используй для болтовни, приветствий, общих вопросов.

Правила:
- Возвращай ТОЛЬКО валидный JSON-объект. Без пояснений снаружи.
- Не выдумывай поля, которых нет в выбранном интенте.
- Если действие с доской подразумевается, но данных не хватает — выбери "chat"
  и в "reply" вежливо попроси уточнение.
- Относительные даты («завтра», «в пятницу 18:00») возвращай в ISO-8601 в TZ команды.
"""


async def route_group_intent(
    provider,
    user_text: str,
    *,
    now_local: str | None = None,
    tz_name: str | None = None,
    dictionary_block: str | None = None,
) -> dict[str, Any]:
    """LLM-роутер для группового чата команды: свободный текст участника →
    структурированный интент управления Канбан-доской.

    provider может быть как одним LLMProvider, так и списком провайдеров —
    нормализуем к списку для llm_with_fallback.
    """
    providers = provider if isinstance(provider, list) else [provider]
    system = GROUP_AGENT_SYSTEM
    if now_local and tz_name:
        system = (
            f"Текущее локальное время команды: {now_local} ({tz_name}).\n"
            f"Относительные даты возвращай в ISO-8601 в этом TZ "
            f"(например «2026-06-09T18:00:00+03:00»).\n\n"
            + system
        )
    if dictionary_block:
        system = dictionary_block + "\n\n" + system
    raw = await llm_with_fallback(
        providers,
        [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=user_text),
        ],
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

    if not team or not team.kanban_token:
        return "⚠️ Доска для задач не выбрана. Пожалуйста, выберите нужную доску в настройках команды, чтобы я мог создавать карточки."
    board_id = team.active_board_id or team.kanban_board_id
    if not board_id or len(board_id) < 10:
        return "⚠️ Доска не настроена корректно. Выбери доску через /kanban_board."

    raw = await llm_with_fallback(providers, [
        ChatMessage(role="system", content=KANBAN_AGENT_SYSTEM),
        ChatMessage(role="user", content=text),
    ])

    responses = _safe_parse_kanban(raw)
    client = YouGileClient(team.kanban_token, board_id)

    results = []
    for response in responses:
        if response.intent == "create_task":
            title = response.parameters.get("title", "").strip()
            if not title:
                results.append("❓ Не понял название задачи. Уточни, что нужно создать.")
                continue
            description = response.parameters.get("description", "").strip()
            column_query = response.parameters.get("column", "").strip()
            deadline_raw = response.parameters.get("deadline") or None
            assignee_name = response.parameters.get("assignee") or None

            try:
                columns = await client.get_columns()
            except Exception as e:
                logger.exception("get_columns failed")
                results.append(f"❌ Не удалось получить колонки доски: {e}")
                continue

            if column_query:
                col = next(
                    (c for c in columns if column_query.lower() in c.get("title", "").lower()),
                    None,
                )
                if not col:
                    names = ", ".join(c.get("title", "?") for c in columns)
                    results.append(f"❓ Колонка «{column_query}» не найдена. Доступны: {names}")
                    continue
                column_id = col["id"]
            else:
                column_id = columns[0]["id"] if columns else None
                if not column_id:
                    results.append("❌ На доске нет колонок.")
                    continue

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
                results.append(f"❌ Ошибка при создании задачи «{title}»: {e}")
                continue

            board_name = team.active_board_name or "по умолчанию"
            tail = ""
            if assignee_ids:
                tail += f"\n👤 Исполнитель: {assignee_name}"
            elif assignee_name:
                users = await client.get_users()
                matched = next(
                    (u for u in users if assignee_name.lower() in u.get("name", "").lower()),
                    None,
                )
                if not matched:
                    names = ", ".join(u.get("name", "?") for u in users[:10])
                    tail += f"\n⚠️ Исполнитель «{assignee_name}» не найден.\nУчастники доски: {names}"
            if deadline_raw:
                tail += f"\n📅 Дедлайн: {deadline_raw}"
            results.append(f"✅ Задача «{title}» создана!\n📋 Доска: {board_name}{tail}")

        elif response.intent == "show_boards":
            try:
                columns = await client.get_columns()
            except Exception as e:
                results.append(f"❌ Не удалось получить данные доски: {e}")
                continue

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
            results.append("\n".join(parts)[:4000])

        elif response.intent == "move_task":
            task_query = response.parameters.get("task_title", "").strip()
            target_column = response.parameters.get("target_column", "").strip()
            if not task_query or not target_column:
                results.append("❓ Укажи, какую задачу и в какую колонку переместить.")
                continue

            try:
                columns = await client.get_columns()
            except Exception as e:
                results.append(f"❌ Ошибка при получении колонок: {e}")
                continue

            col = next(
                (c for c in columns if target_column.lower() in c.get("title", "").lower()),
                None,
            )
            if not col:
                names = ", ".join(c.get("title", "?") for c in columns)
                results.append(f"❓ Колонка «{target_column}» не найдена. Доступны: {names}")
                continue

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
                results.append(f"❓ Не нашёл задачу «{task_query}» на доске.")
                continue

            try:
                await client.move_card(card_id, col["id"])
            except Exception as e:
                results.append(f"❌ Ошибка при перемещении: {e}")
                continue

            results.append(f"✅ Задача «{task_query}» перемещена в «{target_column}»!")

        elif response.intent == "smalltalk":
            results.append(response.parameters.get("reply", "Готов помочь с канбан-доской!"))

        else:
            results.append("❓ Не понял запрос. Я умею: создавать задачи, показывать доску, перемещать задачи.")

    return "\n\n".join(results)
