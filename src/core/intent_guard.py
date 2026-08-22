"""Защита от prompt injection: allowlist интентов + санитизация user-text.

Каждый интент, пришедший из LLM, проверяется по allowlist
перед исполнением. Неизвестные интенты заменяются на "unknown".
"""
import re
import logging
from typing import Literal

logger = logging.getLogger(__name__)

# ── DM-интенты (owner private chat) ──────────────────────────────────
ALLOWED_DM_INTENTS: frozenset[str] = frozenset({
    # Messaging
    "send_message",
    "summarize_chat",
    "tasks_for_chat",
    "draft_reply",
    "catchup",
    "search",
    "find_in_chats",
    # News
    "news_digest",
    "add_news_topic",
    "remove_news_topic",
    # Todos / Commitments
    "list_todos",
    "show_my_tasks",
    "trash_task",
    "restore_task",
    # Kanban
    "create_task",
    "show_boards",
    "move_task",
    "update_kanban_card",
    "select_board",
    "smalltalk",
    "restore_kanban_task",
    # Settings
    "set_setting",
    # Reminders
    "add_reminder",
    "remove_reminder",
    "add_reminders_from_chat",
    # Meetings
    "schedule_meeting",
    "join_meeting",
    "meeting_summary",
    # Team
    "show_team_risks",
    "show_team_sentiment",
    "show_pulse_results",
    "start_pulse",
    "show_task_report",
    "notify_team",
    # Generic
    "chat",
    "unknown",
    "multi",
})

# ── Групповые интенты (group chat) ───────────────────────────────────
ALLOWED_GROUP_INTENTS: frozenset[str] = frozenset({
    "create_task_for",
    "show_my_tasks",
    "edit_task",
    "transfer_deadline",
    "change_assignee",
    "close_task",
    "comment_task",
    "notify_team",
    "schedule_meeting",
    "start_pulse",
    "show_pulse_results",
    "chat",
    "unknown",
})


def validate_intent(kind: str | None, source: Literal["dm", "group"]) -> str:
    """Проверяет intent по allowlist. Возвращает kind или 'unknown'.

    Если LLM вернул intent не из списка допустимых —
    это либо баг в промпте, либо prompt injection.
    """
    if not kind or not isinstance(kind, str):
        return "unknown"

    allowed = ALLOWED_DM_INTENTS if source == "dm" else ALLOWED_GROUP_INTENTS

    if kind in allowed:
        return kind

    logger.warning(
        "intent_guard: blocked intent %r (source=%s) — not in allowlist",
        kind, source,
    )
    return "unknown"


# Паттерн: JSON-подобные конструкции с полем "intent" внутри user-текста
_INJECTION_PATTERN = re.compile(
    r'\{\s*"intent"\s*:', re.IGNORECASE,
)


def sanitize_user_text(text: str, *, max_len: int = 4000) -> str:
    """Подготавливает пользовательский текст перед отправкой в LLM.

    - Обрезает до max_len символов
    - Экранирует JSON-подобные паттерны с "intent" (prompt injection vector)
    """
    text = text[:max_len]

    # Заменяем потенциальные injection-блоки: {"intent": ...}
    if _INJECTION_PATTERN.search(text):
        logger.warning("intent_guard: sanitized injection pattern in user text")
        text = _INJECTION_PATTERN.sub('{ "user_text": ', text)

    return text
