"""Интеграционные тесты саммаризации, черновика и catchup с реальным Gemini."""
from __future__ import annotations

import os

import pytest

from src.llm.base import ChatMessage
from src.llm.gemini_provider import GeminiProvider
from src.core.summarizer import SUMMARY_SYSTEM, DRAFT_SYSTEM, CATCHUP_SYSTEM

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY не задан",
)


@pytest.fixture(scope="module")
def provider():
    return GeminiProvider(os.environ["GEMINI_API_KEY"])


SAMPLE_CHAT = (
    "Я: Привет! Как продвигается проект?\n"
    "Коллега: Почти готово, осталась документация.\n"
    "Я: Отлично! Когда планируешь закончить?\n"
    "Коллега: Думаю, к пятнице управлюсь.\n"
    "Я: Хорошо, тогда в пятницу вечером созвонимся и всё утвердим.\n"
    "Коллега: Договорились, я к тому времени доделаю."
)


class TestSummarizeChatReal:
    async def test_summary_has_required_sections(self, provider):
        user_prompt = (
            f"Собеседник: Коллега\n\n"
            f"Переписка (последние 6 сообщений):\n{SAMPLE_CHAT}"
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=SUMMARY_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ])
        assert len(raw) > 50
        assert "Главное" in raw
        assert "Открытые" in raw or "Вопрос" in raw
        assert "Тон" in raw or "тон" in raw

    async def test_summary_mentions_key_topics(self, provider):
        user_prompt = (
            f"Собеседник: Коллега\n\n"
            f"Переписка (последние 6 сообщений):\n{SAMPLE_CHAT}"
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=SUMMARY_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ])
        assert "пятниц" in raw.lower()
        assert "документаци" in raw.lower()

    async def test_summary_uses_html_tags(self, provider):
        user_prompt = (
            f"Собеседник: Коллега\n\n"
            f"Переписка (последние 6 сообщений):\n{SAMPLE_CHAT}"
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=SUMMARY_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ])
        assert "<b>" in raw or "<i>" in raw


class TestDraftReplyReal:
    async def test_draft_reply_reasonable(self, provider):
        transcript = (
            "Коллега: Ты получил мой файл?\n"
            "Коллега: Нужно твоё подтверждение до обеда."
        )
        user_prompt = (
            f"Собеседник: Коллега\n\n"
            f"Контекст переписки:\n{transcript}\n\n"
            f"Напиши уместный ответ на последнее сообщение."
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=DRAFT_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ])
        assert len(raw) > 10
        assert len(raw) < 2000
        assert "подтвержд" in raw.lower() or "файл" in raw.lower() or "получи" in raw.lower()

    async def test_draft_reply_with_instruction(self, provider):
        transcript = "Коллега: Когда будет готов отчёт?"
        user_prompt = (
            f"Собеседник: Коллега\n\n"
            f"Контекст переписки:\n{transcript}\n\n"
            f"Инструкция: скажи что отчёт будет завтра к обеду"
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=DRAFT_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ])
        assert len(raw) > 10
        assert "завтра" in raw.lower() or "обед" in raw.lower() or "завтра" in raw.lower()

    async def test_draft_reply_no_prefix(self, provider):
        transcript = "Коллега: Пришли контакты, пожалуйста."
        user_prompt = (
            f"Собеседник: Коллега\n\n"
            f"Контекст переписки:\n{transcript}\n\n"
            f"Напиши уместный ответ на последнее сообщение."
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=DRAFT_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ])
        assert not raw.startswith("Черновик")
        assert not raw.startswith("Ответ")


class TestCatchupReal:
    async def test_catchup_has_required_parts(self, provider):
        transcript = (
            "Я: Давай обсудим новый дизайн в среду.\n"
            "Коллега: Хорошо, у меня будут наброски.\n"
            "Я: Отлично, жду.\n"
            "Коллега: Готовь правки к макетам."
        )
        user_prompt = (
            f"Собеседник: Коллега\n\nПоследние сообщения:\n{transcript}"
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=CATCHUP_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ])
        assert len(raw) > 50
        assert "Где мы остановились" in raw or "остановились" in raw.lower()
        assert "Черновик" in raw or "черновик" in raw.lower() or "ответ" in raw.lower()

    async def test_catchup_mentions_design_topic(self, provider):
        transcript = (
            "Я: Давай обсудим новый дизайн в среду.\n"
            "Коллега: Хорошо, у меня будут наброски.\n"
            "Я: Отлично, жду.\n"
            "Коллега: Готовь правки к макетам."
        )
        user_prompt = (
            f"Собеседник: Коллега\n\nПоследние сообщения:\n{transcript}"
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=CATCHUP_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ])
        assert "дизайн" in raw.lower() or "макет" in raw.lower()

    async def test_catchup_uses_html_tags(self, provider):
        transcript = "Коллега: Привет! Как дела?"
        user_prompt = (
            f"Собеседник: Коллега\n\nПоследние сообщения:\n{transcript}"
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=CATCHUP_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ])
        assert "<b>" in raw
