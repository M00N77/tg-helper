"""Интеграционные тесты роутинга интентов с реальным Gemini.
Проверяет, что LLM корректно классифицирует все 25+ интентов из AGENT_SYSTEM."""
from __future__ import annotations

import os

import pytest

from src.core.agent import route_intent
from src.llm.gemini_provider import GeminiProvider

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY не задан",
)


@pytest.fixture(scope="module")
def provider():
    return GeminiProvider(os.environ["GEMINI_API_KEY"])


class TestRouteIntentReal:
    """Каждый тест отправляет фразу → route_intent → проверяет intent."""

    async def test_send_message(self, provider):
        result = await route_intent(
            provider, "напиши Анне что я приду в 6 часов",
        )
        assert result["intent"] == "send_message"
        assert "анн" in result.get("recipient", "").lower()
        assert "приду" in result.get("text", "").lower()

    async def test_send_message_no_recipient(self, provider):
        result = await route_intent(
            provider, "скажи что я сегодня не приду",
        )
        assert result["intent"] in ("chat", "send_message", "unknown")

    async def test_summarize_chat(self, provider):
        result = await route_intent(
            provider, "сделай саммари переписки с Артёмом",
        )
        assert result["intent"] == "summarize_chat"
        assert "артём" in result.get("contact", "").lower()

    async def test_tasks_for_chat(self, provider):
        result = await route_intent(
            provider, "извлеки задачи из переписки с Мариной",
        )
        assert result["intent"] == "tasks_for_chat"
        assert "марин" in result.get("contact", "").lower()

    async def test_draft_reply(self, provider):
        result = await route_intent(
            provider, "напиши черновик ответа Сергею",
        )
        assert result["intent"] == "draft_reply"
        assert "серг" in result.get("contact", "").lower()

    async def test_catchup(self, provider):
        result = await route_intent(
            provider, "где мы остановились с Алексеем",
        )
        assert result["intent"] == "catchup"
        assert "алекс" in result.get("contact", "").lower()

    async def test_search(self, provider):
        result = await route_intent(
            provider, "найди сообщение про договор с поставщиком",
        )
        assert result["intent"] == "search"
        assert len(result.get("query", "")) > 0

    async def test_news_digest(self, provider):
        result = await route_intent(
            provider, "собери новости по теме искусственный интеллект",
        )
        assert result["intent"] == "news_digest"
        assert "искусствен" in result.get("topic", "").lower()

    async def test_news_digest_with_hours(self, provider):
        result = await route_intent(
            provider, "пришли новости по Python за последние 48 часов",
        )
        assert result["intent"] == "news_digest"
        assert result.get("hours") in (48, "48")

    async def test_list_todos(self, provider):
        result = await route_intent(
            provider, "покажи мои открытые обещания",
        )
        assert result["intent"] == "list_todos"

    async def test_set_setting_bool(self, provider):
        result = await route_intent(
            provider, "включи утренний дайджест",
        )
        assert result["intent"] == "set_setting"
        assert result.get("key") == "digest_enabled"
        assert result.get("value") in (True, "true")

    async def test_set_setting_time(self, provider):
        result = await route_intent(
            provider, "поставь дайджест на 9 утра",
        )
        assert result["intent"] == "set_setting"
        assert result.get("key") in ("digest_time",)
        assert "09:00" in str(result.get("value", ""))

    async def test_set_setting_tz(self, provider):
        result = await route_intent(
            provider, "смени часовой пояс на Europe/London",
        )
        assert result["intent"] == "set_setting"
        assert result.get("key") == "timezone"
        assert "london" in str(result.get("value", "")).lower()

    async def test_set_setting_llm_provider(self, provider):
        result = await route_intent(
            provider, "переключись на gemini",
        )
        assert result["intent"] == "set_setting"
        assert result.get("key") == "llm_provider"

    async def test_find_in_chats(self, provider):
        result = await route_intent(
            provider, "найди где я обсуждал мебель",
        )
        assert result["intent"] == "find_in_chats"
        assert len(result.get("query", "")) > 0

    async def test_find_in_chats_with_action(self, provider):
        result = await route_intent(
            provider, "в одном из чатов обсуждали проект, покажи саммари",
        )
        assert result["intent"] == "find_in_chats"
        assert result.get("action") == "summary"

    async def test_add_news_topic(self, provider):
        result = await route_intent(
            provider, "добавь тему новостей про блокчейн",
        )
        assert result["intent"] == "add_news_topic"
        assert "блокчейн" in result.get("topic", "").lower()

    async def test_remove_news_topic(self, provider):
        result = await route_intent(
            provider, "удали тему новостей про криптовалюты",
        )
        assert result["intent"] == "remove_news_topic"
        assert len(result.get("topic", "")) > 0

    async def test_add_reminder(self, provider):
        result = await route_intent(
            provider, "напомни мне завтра в 18:00 позвонить маме",
        )
        assert result["intent"] == "add_reminder"
        assert "позвонить" in result.get("text", "").lower()
        assert "мам" in result.get("text", "").lower()

    async def test_add_reminder_no_date(self, provider):
        result = await route_intent(
            provider, "поставь напоминание купить хлеб",
        )
        assert result["intent"] == "add_reminder"
        assert "хлеб" in result.get("text", "").lower()

    async def test_remove_reminder(self, provider):
        result = await route_intent(
            provider, "убери напоминание про звонок маме",
        )
        assert result["intent"] == "remove_reminder"
        assert len(result.get("query", "")) > 0

    async def test_add_reminders_from_chat(self, provider):
        result = await route_intent(
            provider, "вытащи обещания из чата с Павлом",
        )
        assert result["intent"] == "add_reminders_from_chat"
        assert "павл" in result.get("contact", "").lower()

    async def test_chat_simple(self, provider):
        result = await route_intent(
            provider, "как дела?",
        )
        assert result["intent"] in ("chat", "smalltalk")
        if result["intent"] == "chat":
            assert len(result.get("reply", "")) > 0

    async def test_chat_question(self, provider):
        result = await route_intent(
            provider, "сколько будет 2+2?",
        )
        assert result["intent"] in ("chat", "smalltalk")

    async def test_create_task(self, provider):
        result = await route_intent(
            provider, "создай задачу купить молоко на канбане",
        )
        assert result["intent"] == "create_task"
        assert "молок" in result.get("title", "").lower()

    async def test_create_task_with_column(self, provider):
        result = await route_intent(
            provider, "добавь карточку \"сделать отчёт\" в колонку \"В работе\"",
        )
        assert result["intent"] == "create_task"
        title = result.get("title", "")
        assert "отчёт" in title.lower() or "отчет" in title.lower()

    async def test_show_boards(self, provider):
        result = await route_intent(
            provider, "покажи канбан-доску",
        )
        assert result["intent"] == "show_boards"

    async def test_move_task(self, provider):
        result = await route_intent(
            provider, "перемести задачу купить молоко в колонку Готово",
        )
        assert result["intent"] == "move_task"
        assert len(result.get("task_title", "")) > 0
        assert len(result.get("target_column", "")) > 0

    async def test_smalltalk_kanban(self, provider):
        result = await route_intent(
            provider, "расскажи что ты умеешь",
        )
        assert result["intent"] in ("smalltalk", "chat")

    async def test_schedule_meeting(self, provider):
        result = await route_intent(
            provider, "запланируй созвон на завтра на 15:00",
        )
        assert result["intent"] == "schedule_meeting"
        assert len(result.get("title", "")) > 0

    async def test_join_meeting(self, provider):
        result = await route_intent(
            provider, "подключись к встрече https://telemost.yandex.ru/j/abc123",
        )
        assert result["intent"] == "join_meeting"
        assert "telemost" in result.get("url", "")

    async def test_meeting_summary(self, provider):
        result = await route_intent(
            provider, "что обсудили на последней встрече",
        )
        assert result["intent"] == "meeting_summary"

    async def test_multi_intent(self, provider):
        result = await route_intent(
            provider, "включи дайджест и поставь на 7 утра",
        )
        assert result["intent"] == "multi"
        assert isinstance(result.get("actions"), list)
        assert len(result["actions"]) >= 2

    async def test_unknown_intent(self, provider):
        result = await route_intent(
            provider, "фывапролдж",
        )
        assert result["intent"] in ("unknown", "chat", "smalltalk")

    async def test_with_timezone_injection(self, provider):
        result = await route_intent(
            provider, "напомни завтра в 10 утра позвонить врачу",
            now_local="2026-06-07 18:00",
            tz_name="Europe/Moscow",
        )
        assert result["intent"] == "add_reminder"

    async def test_with_history_block(self, provider):
        result = await route_intent(
            provider, "напиши ему что я опаздываю",
            history_block="Последний контакт: Анна",
        )
        assert result["intent"] in ("send_message", "chat")

    async def test_set_setting_auto_reply_text(self, provider):
        result = await route_intent(
            provider, "поставь текст автоответа: я сейчас занят, перезвоню позже",
        )
        assert result["intent"] == "set_setting"
        assert result.get("key") == "auto_reply_text"

    async def test_reminder_lead_time(self, provider):
        result = await route_intent(
            provider, "напоминай за 2 часа до дедлайна",
        )
        assert result["intent"] == "set_setting"
        assert result.get("key") == "reminder_lead_hours"

    async def test_ignore_archived(self, provider):
        result = await route_intent(
            provider, "не показывай архивные чаты",
        )
        assert result["intent"] == "set_setting"
        assert result.get("key") == "ignore_archived"
