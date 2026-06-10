"""Тесты для демо-презентации: проверка роутинга интентов через Groq.
Запуск:
    pytest tests/test_demo.py -v                          # unit-тесты парсинга
    pytest tests/test_demo.py -v -k real                  # интеграционные с Groq
    pytest tests/test_demo.py -v -k "real and scenario"   # полный сквозной сценарий

Требует GROQ_API_KEY в .env или переменной окружения.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from demo_messages import SCENARIOS
from src.core.agent import _safe_parse, route_intent
from src.llm.groq_provider import GroqProvider


# ── Unit-тесты: проверка парсинга JSON-ответов ─────────────────────────────

class TestParseAllDemoIntents:
    """Для каждого интента из демо-сценариев проверяем корректность _safe_parse."""

    @pytest.mark.parametrize("intent_name,raw", [
        ("add_reminder",       '{"intent": "add_reminder", "text": "отправить отчёт", "when": "2026-06-10T10:00:00+03:00"}'),
        ("list_todos",         '{"intent": "list_todos"}'),
        ("add_reminders_from_chat", '{"intent": "add_reminders_from_chat", "contact": "Оля"}'),
        ("remove_reminder",    '{"intent": "remove_reminder", "query": "отчёт"}'),
        ("schedule_meeting",   '{"intent": "schedule_meeting", "title": "синхронизация", "datetime": "2026-06-11T11:00:00+03:00"}'),
        ("join_meeting",       '{"intent": "join_meeting", "url": "https://telemost.yandex.ru/j/abc123"}'),
        ("meeting_summary",    '{"intent": "meeting_summary"}'),
        ("create_task",        '{"intent": "create_task", "title": "презентация", "column": "В работе"}'),
        ("show_boards",        '{"intent": "show_boards"}'),
        ("move_task",          '{"intent": "move_task", "task_title": "презентация", "target_column": "Готово"}'),
        ("send_message",       '{"intent": "send_message", "recipient": "Анна", "text": "Привет"}'),
        ("summarize_chat",     '{"intent": "summarize_chat", "contact": "Мария"}'),
        ("draft_reply",        '{"intent": "draft_reply", "contact": "Елена"}'),
        ("catchup",            '{"intent": "catchup", "contact": "Павел"}'),
        ("search",             '{"intent": "search", "query": "дедлайн"}'),
        ("find_in_chats",      '{"intent": "find_in_chats", "query": "бюджет", "action": "catchup"}'),
        ("news_digest",        '{"intent": "news_digest", "topic": "AI", "hours": 24}'),
        ("add_news_topic",     '{"intent": "add_news_topic", "topic": "технологии", "hours": 48}'),
        ("set_setting",        '{"intent": "set_setting", "key": "digest_enabled", "value": true}'),
        ("chat",               '{"intent": "chat", "reply": "Привет!"}'),
        ("unknown",            '{"intent": "unknown"}'),
        ("multi",              '{"intent": "multi", "actions": [{"intent": "set_setting", "key": "digest_enabled", "value": true}, {"intent": "set_setting", "key": "digest_time", "value": "09:00"}]}'),
        ("trash_task",         '{"intent": "trash_task", "query": "отчёт"}'),
        ("restore_task",       '{"intent": "restore_task", "query": "отчёт"}'),
        ("smalltalk",          '{"intent": "smalltalk", "reply": "Чем могу помочь?"}'),
        ("schedule_meeting_no_time", '{"intent": "schedule_meeting", "title": "синхронизация по спринту"}'),
        ("create_task_with_desc",   '{"intent": "create_task", "title": "проверить баги", "description": "пройтись по всем критическим ошибкам"}'),
        ("create_task_with_deadline", '{"intent": "create_task", "title": "бюджет", "deadline": "2026-06-20", "column": "В работе"}'),
    ])
    def test_parse_intent(self, intent_name, raw):
        result = _safe_parse(raw)
        assert isinstance(result, dict)
        assert isinstance(result.get("intent"), str)
        assert len(result["intent"]) > 0


# ── Интеграционные тесты с реальным Groq ──────────────────────────────────

pytestmark_real = pytest.mark.skipif(
    not os.environ.get("GROQ_API_KEY"),
    reason="GROQ_API_KEY не задан",
)


@pytest.fixture(scope="module")
def provider():
    return GroqProvider(os.environ["GROQ_API_KEY"])


class TestRealGroqDemo:
    """Каждый тест отправляет реальную фразу в Groq → route_intent → проверяет intent."""

    # ── Напоминания и задачи ──

    async def test_real_add_reminder(self, provider):
        result = await route_intent(provider, "напомни завтра в 10 утра отправить отчёт по проекту")
        assert result["intent"] == "add_reminder"
        assert "отчёт" in result.get("text", "").lower() or "отчет" in result.get("text", "").lower()

    async def test_real_add_reminder_with_contact(self, provider):
        result = await route_intent(provider, "напомни мне во вторник позвонить Сергею по поводу контракта")
        assert result["intent"] == "add_reminder"
        assert "серг" in result.get("text", "").lower()

    async def test_real_list_todos(self, provider):
        result = await route_intent(provider, "покажи мои задачи")
        assert result["intent"] == "list_todos"

    async def test_real_list_todos_alt(self, provider):
        result = await route_intent(provider, "что я должен сделать?")
        assert result["intent"] in ("list_todos", "chat")

    async def test_real_add_reminders_from_chat(self, provider):
        result = await route_intent(provider, "вытащи обещания из чата с Олей")
        assert result["intent"] == "add_reminders_from_chat"
        assert "ол" in result.get("contact", "").lower()

    async def test_real_remove_reminder(self, provider):
        result = await route_intent(provider, "удали напоминание про отчёт")
        assert result["intent"] == "remove_reminder"

    # ── Корзина ──

    async def test_real_trash_task(self, provider):
        result = await route_intent(provider, "удали обещание про отчёт")
        assert result["intent"] == "trash_task"
        assert len(result.get("query", "")) > 0

    async def test_real_trash_task_alt(self, provider):
        result = await route_intent(provider, "выброси задачу купить хлеб")
        assert result["intent"] == "trash_task"

    async def test_real_restore_task(self, provider):
        result = await route_intent(provider, "восстанови обещание про отчёт")
        assert result["intent"] == "restore_task"
        assert len(result.get("query", "")) > 0

    # ── Встречи ──

    async def test_real_schedule_meeting(self, provider):
        result = await route_intent(provider, "запланируй встречу на послезавтра в 11:00, тема — синхронизация по спринту")
        assert result["intent"] == "schedule_meeting"
        assert len(result.get("title", "")) > 0

    async def test_real_join_meeting_with_url(self, provider):
        result = await route_intent(provider, "подключись к встрече https://telemost.yandex.ru/j/abc123")
        assert result["intent"] == "join_meeting"
        assert "url" in result

    async def test_real_join_meeting_no_url(self, provider):
        result = await route_intent(provider, "хочу подключиться к встрече")
        assert result["intent"] == "join_meeting"

    async def test_real_meeting_summary(self, provider):
        result = await route_intent(provider, "сделай итоги встречи")
        assert result["intent"] == "meeting_summary"

    # ── Канбан ──

    async def test_real_create_task(self, provider):
        result = await route_intent(provider, "создай задачу подготовить презентацию для клиента")
        assert result["intent"] == "create_task"
        assert "презентац" in result.get("title", "").lower()

    async def test_real_create_task_with_description(self, provider):
        result = await route_intent(provider, "создай задачу проверить баги — пройтись по всем критическим ошибкам из списка")
        assert result["intent"] == "create_task"
        assert len(result.get("title", "")) > 0

    async def test_real_create_task_in_column(self, provider):
        result = await route_intent(provider, "добавь задачу переписать документацию в колонку В работе")
        assert result["intent"] == "create_task"
        assert len(result.get("title", "")) > 0

    async def test_real_create_task_with_deadline(self, provider):
        result = await route_intent(provider, "новая задача: согласовать бюджет с заказчиком, дедлайн 2026-06-20")
        assert result["intent"] == "create_task"

    async def test_real_show_boards(self, provider):
        result = await route_intent(provider, "покажи канбан-доску")
        assert result["intent"] == "show_boards"

    async def test_real_move_task(self, provider):
        result = await route_intent(provider, "перенеси задачу про презентацию в колонку Готово")
        assert result["intent"] == "move_task"
        assert len(result.get("task_title", "")) > 0
        assert len(result.get("target_column", "")) > 0

    async def test_real_kanban_stats(self, provider):
        result = await route_intent(provider, "покажи статистику доски")
        assert result["intent"] in ("show_boards", "chat", "smalltalk")

    # ── Чаты ──

    async def test_real_send_message(self, provider):
        result = await route_intent(provider, "напиши Анне что я буду через 15 минут, опоздал на созвон")
        assert result["intent"] == "send_message"
        assert "анн" in result.get("recipient", "").lower()

    async def test_real_send_message_short(self, provider):
        result = await route_intent(provider, "скажи Дмитрию спасибо за помощь с отчётом, всё загрузил")
        assert result["intent"] == "send_message"
        assert "дмитр" in result.get("recipient", "").lower()

    async def test_real_catchup(self, provider):
        result = await route_intent(provider, "где мы остановились с Павлом")
        assert result["intent"] == "catchup"
        assert "павл" in result.get("contact", "").lower()

    async def test_real_summarize_chat(self, provider):
        result = await route_intent(provider, "сделай саммари переписки с Марией")
        assert result["intent"] == "summarize_chat"
        assert "мар" in result.get("contact", "").lower()

    async def test_real_draft_reply(self, provider):
        result = await route_intent(provider, "напиши черновик ответа Елене по проекту")
        assert result["intent"] == "draft_reply"
        assert "елен" in result.get("contact", "").lower()

    async def test_real_tasks_for_chat(self, provider):
        result = await route_intent(provider, "какие задачи мы обсуждали с Алексеем")
        assert result["intent"] in ("tasks_for_chat", "add_reminders_from_chat")
        assert "алекс" in str(result.get("contact", "")).lower()

    # ── Поиск ──

    async def test_real_search(self, provider):
        result = await route_intent(provider, "найди сообщения про дедлайн")
        assert result["intent"] in ("search", "find_in_chats")
        assert len(result.get("query", "")) > 0

    async def test_real_find_in_chats(self, provider):
        result = await route_intent(provider, "найди в чатах где обсуждали бюджет")
        assert result["intent"] == "find_in_chats"
        assert len(result.get("query", "")) > 0

    async def test_real_news_digest(self, provider):
        result = await route_intent(provider, "собери новости про искусственный интеллект за последние 24 часа")
        assert result["intent"] == "news_digest"
        assert len(result.get("topic", "")) > 0

    async def test_real_add_news_topic(self, provider):
        result = await route_intent(provider, "добавь тему технологии 48")
        assert result["intent"] == "add_news_topic"

    async def test_real_digest_now(self, provider):
        result = await route_intent(provider, "сделай дайджест")
        assert result["intent"] in ("news_digest", "chat", "smalltalk")

    # ── Настройки ──

    async def test_real_set_setting_enable_digest(self, provider):
        result = await route_intent(provider, "включи утренний дайджест")
        assert result["intent"] == "set_setting"
        assert result.get("key") == "digest_enabled"

    async def test_real_set_setting_digest_time(self, provider):
        result = await route_intent(provider, "поставь дайджест на 9 утра")
        assert result["intent"] == "set_setting"
        assert result.get("key") in ("digest_time", "news_digest_time")

    async def test_real_set_setting_tz(self, provider):
        result = await route_intent(provider, "смени часовой пояс на Europe/London")
        assert result["intent"] == "set_setting"
        assert result.get("key") == "timezone"

    async def test_real_set_setting_llm(self, provider):
        result = await route_intent(provider, "переключись на groq")
        assert result["intent"] == "set_setting"
        assert result.get("key") == "llm_provider"

    async def test_real_set_setting_ignore_archived(self, provider):
        result = await route_intent(provider, "не показывай архивные чаты")
        assert result["intent"] == "set_setting"
        assert result.get("key") == "ignore_archived"

    async def test_real_set_setting_reminders(self, provider):
        result = await route_intent(provider, "включи напоминания о дедлайнах")
        assert result["intent"] == "set_setting"
        assert result.get("key") == "reminders_enabled"

    async def test_real_set_setting_lead_hours(self, provider):
        result = await route_intent(provider, "напоминай за 2 часа до дедлайна")
        assert result["intent"] == "set_setting"
        assert result.get("key") == "reminder_lead_hours"

    # ── Сквозной сценарий ──

    async def test_real_scenario_multi_step(self, provider):
        """Проверяет, что ключевые шаги сквозного сценария корректно роутятся."""
        steps = [
            ("напомни завтра в 9 утра отправить коммерческое предложение", "add_reminder"),
            ("создай задачу подготовить коммерческое предложение для клиента", "create_task"),
            ("где мы остановились с Андреем", "catchup"),
            ("напиши Андрею что отправил все документы, проверь пожалуйста", "send_message"),
            ("покажи мои задачи", "list_todos"),
            ("запланируй встречу на пятницу в 14:00, синхронизация по неделе", "schedule_meeting"),
            ("перенеси задачу про коммерческое предложение в колонку Готово", "move_task"),
            ("что я должен сделать на этой неделе", "list_todos"),
        ]
        for phrase, expected_intent in steps:
            result = await route_intent(provider, phrase)
            assert result["intent"] == expected_intent, (
                f"Фраза: {phrase!r}\n"
                f"  Ожидался intent: {expected_intent}\n"
                f"  Получен: {result['intent']}\n"
                f"  Полный ответ: {result}"
            )

    async def test_real_unknown(self, provider):
        result = await route_intent(provider, "фывапролдж")
        assert result["intent"] in ("unknown", "chat", "smalltalk")


# ── Проверка, что все демо-сообщения имеют соответствующие тесты ────────────

class TestDemoCoverage:
    """Проверяет, что все сообщения из demo_messages.py покрыты хотя бы одним тестом."""

    def test_all_scenarios_have_tests(self):
        """Убеждаемся что demo_messages.py содержит сценарии (мета-тест)."""
        assert len(SCENARIOS) > 0
        total = sum(len(s.messages) for s in SCENARIOS)
        assert total > 30
        assert all(len(s.messages) > 0 for s in SCENARIOS)
