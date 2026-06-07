"""Тесты роутинга интентов: парсинг всех 25 интентов, route_intent с историей/часовым поясом,
диспетчеризация add_news_topic / remove_news_topic."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.agent import _safe_parse, route_intent
from src.bot.handlers.free_text import _exec_add_news_topic, _exec_remove_news_topic


# ── Все 25 интентов из AGENT_SYSTEM ──────────────────────────────────────

class TestAllIntentsParsed:
    """Для каждого intent из AGENT_SYSTEM проверяем, что _safe_parse
    корректно парсит минимально валидный JSON."""

    def test_send_message(self):
        raw = '{"intent": "send_message", "recipient": "Анна", "text": "Привет"}'
        assert _safe_parse(raw)["intent"] == "send_message"

    def test_summarize_chat(self):
        raw = '{"intent": "summarize_chat", "contact": "Анна"}'
        assert _safe_parse(raw)["intent"] == "summarize_chat"

    def test_tasks_for_chat(self):
        raw = '{"intent": "tasks_for_chat", "contact": "Анна"}'
        assert _safe_parse(raw)["intent"] == "tasks_for_chat"

    def test_draft_reply(self):
        raw = '{"intent": "draft_reply", "contact": "Анна", "instruction": null}'
        assert _safe_parse(raw)["intent"] == "draft_reply"

    def test_catchup(self):
        raw = '{"intent": "catchup", "contact": "Анна"}'
        assert _safe_parse(raw)["intent"] == "catchup"

    def test_search(self):
        raw = '{"intent": "search", "query": "проект X"}'
        assert _safe_parse(raw)["intent"] == "search"

    def test_find_in_chats(self):
        raw = '{"intent": "find_in_chats", "query": "мебель", "action": "catchup"}'
        assert _safe_parse(raw)["intent"] == "find_in_chats"

    def test_news_digest(self):
        raw = '{"intent": "news_digest", "topic": "AI", "hours": 24}'
        assert _safe_parse(raw)["intent"] == "news_digest"

    def test_list_todos(self):
        raw = '{"intent": "list_todos"}'
        assert _safe_parse(raw)["intent"] == "list_todos"

    def test_set_setting(self):
        raw = '{"intent": "set_setting", "key": "news_enabled", "value": true}'
        assert _safe_parse(raw)["intent"] == "set_setting"

    def test_add_news_topic(self):
        raw = '{"intent": "add_news_topic", "topic": "AI", "hours": 24}'
        assert _safe_parse(raw)["intent"] == "add_news_topic"

    def test_remove_news_topic(self):
        raw = '{"intent": "remove_news_topic", "topic": "AI"}'
        assert _safe_parse(raw)["intent"] == "remove_news_topic"

    def test_add_reminder(self):
        raw = (
            '{"intent": "add_reminder", "text": "позвонить маме",'
            '"when": "2026-05-10T15:00:00Z", "peer_query": null}'
        )
        assert _safe_parse(raw)["intent"] == "add_reminder"

    def test_remove_reminder(self):
        raw = '{"intent": "remove_reminder", "query": "мама"}'
        assert _safe_parse(raw)["intent"] == "remove_reminder"

    def test_add_reminders_from_chat(self):
        raw = '{"intent": "add_reminders_from_chat", "contact": "Артём"}'
        assert _safe_parse(raw)["intent"] == "add_reminders_from_chat"

    def test_chat(self):
        raw = '{"intent": "chat", "reply": "Привет!"}'
        assert _safe_parse(raw)["intent"] == "chat"

    def test_unknown(self):
        raw = '{"intent": "unknown"}'
        assert _safe_parse(raw)["intent"] == "unknown"

    def test_multi(self):
        raw = (
            '{"intent": "multi", "actions": ['
            '{"intent": "chat", "reply": "ok"},'
            '{"intent": "set_setting", "key": "news_enabled", "value": true}'
            "]}"
        )
        assert _safe_parse(raw)["intent"] == "multi"

    def test_create_task(self):
        raw = '{"intent": "create_task", "title": "Купить молоко", "column": "В работе"}'
        assert _safe_parse(raw)["intent"] == "create_task"

    def test_show_boards(self):
        raw = '{"intent": "show_boards"}'
        assert _safe_parse(raw)["intent"] == "show_boards"

    def test_move_task(self):
        raw = (
            '{"intent": "move_task",'
            '"task_title": "Купить молоко", "target_column": "Готово"}'
        )
        assert _safe_parse(raw)["intent"] == "move_task"

    def test_smalltalk(self):
        raw = '{"intent": "smalltalk", "reply": "Чем могу помочь?"}'
        assert _safe_parse(raw)["intent"] == "smalltalk"

    def test_schedule_meeting(self):
        raw = (
            '{"intent": "schedule_meeting",'
            '"title": "Cозвон", "datetime": "2026-06-10T15:00:00Z"}'
        )
        assert _safe_parse(raw)["intent"] == "schedule_meeting"

    def test_join_meeting(self):
        raw = '{"intent": "join_meeting", "url": "https://telemost.yandex.ru/abc"}'
        assert _safe_parse(raw)["intent"] == "join_meeting"

    def test_meeting_summary(self):
        raw = '{"intent": "meeting_summary"}'
        assert _safe_parse(raw)["intent"] == "meeting_summary"

    def test_multi_intent_structure(self):
        raw = (
            '{"intent": "multi", "actions": ['
            '{"intent": "add_news_topic", "topic": "AI"},'
            '{"intent": "set_setting", "key": "news_enabled", "value": true}'
            "]}"
        )
        result = _safe_parse(raw)
        assert result["intent"] == "multi"
        assert len(result["actions"]) == 2
        assert result["actions"][0]["intent"] == "add_news_topic"


# ── route_intent — история, часовой пояс, невалидный intent ───────────────

class TestRouteIntentWithHistory:
    async def test_history_injected_into_system_prompt(self):
        provider = MagicMock()
        provider.chat = AsyncMock(return_value='{"intent": "chat", "reply": "ok"}')
        await route_intent(
            provider, "ему ответь",
            history_block="Последний контакт: Артём",
        )
        call_args = provider.chat.call_args[0][0]
        assert any("Последний контакт" in m.content for m in call_args)

    async def test_now_local_injected_into_system_prompt(self):
        provider = MagicMock()
        provider.chat = AsyncMock(return_value='{"intent": "chat", "reply": "ok"}')
        await route_intent(
            provider, "напомни завтра",
            now_local="2026-06-07 18:00",
            tz_name="Europe/Moscow",
        )
        call_args = provider.chat.call_args[0][0]
        assert any("2026-06-07 18:00" in m.content for m in call_args)

    async def test_llm_returns_unknown_intent_not_in_spec(self):
        provider = MagicMock()
        provider.chat = AsyncMock(return_value='{"intent": "fly_to_moon"}')
        result = await route_intent(provider, "test")
        assert result["intent"] == "fly_to_moon"


# ── Диспетчеризация add_news_topic / remove_news_topic ────────────────────

class TestAddNewsTopicIntent:
    async def test_add_news_topic_saves_to_db(self):
        intent = {
            "intent": "add_news_topic",
            "topic": "Искусственный интеллект",
            "hours": 48,
        }
        message = AsyncMock()
        message.answer = AsyncMock()
        message.from_user.id = 1

        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = False

        with (
            patch("src.bot.handlers.free_text.get_session", return_value=mock_session),
            patch("src.bot.handlers.free_text.get_or_create_user", return_value=MagicMock(id=1)),
            patch("src.bot.handlers.free_text.add_news_topic", new_callable=AsyncMock) as mock_add,
        ):
            await _exec_add_news_topic(intent, message)

        mock_add.assert_called_once()
        args = mock_add.call_args
        assert args[0][2] == "Искусственный интеллект"
        assert args.kwargs["hours"] == 48
        message.answer.assert_called_once()
        assert "Искусственный интеллект" in message.answer.call_args[0][0]

    async def test_add_news_topic_default_hours(self):
        intent = {"intent": "add_news_topic", "topic": "Python"}
        message = AsyncMock()
        message.answer = AsyncMock()
        message.from_user.id = 1

        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = False

        with (
            patch("src.bot.handlers.free_text.get_session", return_value=mock_session),
            patch("src.bot.handlers.free_text.get_or_create_user", return_value=MagicMock(id=1)),
            patch("src.bot.handlers.free_text.add_news_topic", new_callable=AsyncMock) as mock_add,
        ):
            await _exec_add_news_topic(intent, message)

        mock_add.assert_called_once()
        assert mock_add.call_args.kwargs.get("hours") == 24


class TestRemoveNewsTopicIntent:
    async def test_remove_news_topic_found(self):
        intent = {"intent": "remove_news_topic", "topic": "python"}
        message = AsyncMock()
        message.answer = AsyncMock()
        message.from_user.id = 1

        topic_python = MagicMock(id=1, topic="Python", hours=24, enabled=True)
        topic_java = MagicMock(id=2, topic="Java", hours=24, enabled=True)

        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = False

        with (
            patch("src.bot.handlers.free_text.get_session", return_value=mock_session),
            patch("src.bot.handlers.free_text.get_or_create_user", return_value=MagicMock(id=1)),
            patch(
                "src.bot.handlers.free_text.list_news_topics",
                return_value=[topic_python, topic_java],
            ),
            patch("src.bot.handlers.free_text.delete_news_topic", new_callable=AsyncMock) as mock_del,
        ):
            await _exec_remove_news_topic(intent, message)

        mock_del.assert_called_once()
        assert mock_del.call_args[0][2] == 1
        message.answer.assert_called_once()
        assert "удалил" in message.answer.call_args[0][0].lower()

    async def test_remove_news_topic_not_found(self):
        intent = {"intent": "remove_news_topic", "topic": "python"}
        message = AsyncMock()
        message.answer = AsyncMock()
        message.from_user.id = 1

        topic_java = MagicMock(id=2, topic="Java", hours=24, enabled=True)

        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = False

        with (
            patch("src.bot.handlers.free_text.get_session", return_value=mock_session),
            patch("src.bot.handlers.free_text.get_or_create_user", return_value=MagicMock(id=1)),
            patch(
                "src.bot.handlers.free_text.list_news_topics",
                return_value=[topic_java],
            ),
            patch("src.bot.handlers.free_text.delete_news_topic", new_callable=AsyncMock) as mock_del,
        ):
            await _exec_remove_news_topic(intent, message)

        mock_del.assert_not_called()
        message.answer.assert_called_once()
        assert "не нашёл" in message.answer.call_args[0][0].lower()


# Список покрытых сценариев:
# TestAllIntentsParsed:
#   - send_message, summarize_chat, tasks_for_chat, draft_reply, catchup
#   - search, find_in_chats, news_digest, list_todos, set_setting
#   - add_news_topic, remove_news_topic, add_reminder, remove_reminder, add_reminders_from_chat
#   - chat, unknown, multi, create_task, show_boards, move_task, smalltalk
#   - schedule_meeting, join_meeting, meeting_summary
#   - multi_intent_structure (actions list)
# TestRouteIntentWithHistory:
#   - history_block injected into system prompt
#   - now_local/tz_name injected into system prompt
#   - unknown intent not in spec → still parsed, no crash
# TestAddNewsTopicIntent:
#   - add_news_topic saves with hours=48
#   - add_news_topic defaults hours=24
# TestRemoveNewsTopicIntent:
#   - remove_news_topic finds by substring, deletes correct id
#   - remove_news_topic not found → no delete, "не нашёл"
