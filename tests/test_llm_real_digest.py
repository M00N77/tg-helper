"""Интеграционные тесты дайджестов с реальным Gemini."""
from __future__ import annotations

import os

import pytest

from src.core.digest import DIGEST_SYSTEM
from src.llm.base import ChatMessage
from src.llm.gemini_provider import GeminiProvider
from src.core.news import NEWS_SYSTEM

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY не задан",
)


@pytest.fixture(scope="module")
def provider():
    return GeminiProvider(os.environ["GEMINI_API_KEY"])


class TestDigestReal:
    async def test_morning_digest_has_structure(self, provider):
        payload = (
            "Ждут ответа:\n"
            "- Анна: пришли отчёт, жду\n"
            "- Сергей: когда созвонимся?\n\n"
            "Мои горящие обещания:\n"
            "- Коллега: подготовить презентацию (до 2026-06-10 18:00)\n\n"
            "Авто-ответов: 2 (кому: Олег, Мария)"
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=DIGEST_SYSTEM),
            ChatMessage(role="user", content=payload),
        ])
        assert "Доброе утро" in raw or "доброе" in raw.lower()
        assert "Ждут ответа" in raw or "Ответа" in raw
        assert "обещани" in raw.lower()
        assert "<b>" in raw or "<i>" in raw

    async def test_empty_digest(self, provider):
        raw = await provider.chat([
            ChatMessage(role="system", content=DIGEST_SYSTEM),
            ChatMessage(role="user", content="Активности не было."),
        ])
        assert len(raw) > 0

    async def test_digest_no_markdown(self, provider):
        payload = "Ждут ответа:\n- Анна: привет"
        raw = await provider.chat([
            ChatMessage(role="system", content=DIGEST_SYSTEM),
            ChatMessage(role="user", content=payload),
        ])
        assert "```" not in raw


class TestNewsDigestReal:
    async def test_news_system_prompt_accepts_posts(self, provider):
        posts = (
            "[2026-06-07 10:00] <TechNews> (https://t.me/technews/123)\n"
            "OpenAI выпустила новую модель GPT-5 с улучшенным reasoning\n\n"
            "---\n\n"
            "[2026-06-07 09:00] <AI Daily> (https://t.me/aidaily/456)\n"
            "Нейросети научились генерировать видео высокого качества"
        )
        user_prompt = (
            "Тема запроса: искусственный интеллект\n"
            "Окно: последние 24 часов\n"
            "Каналов: 2, релевантных постов: 2\n\n"
            f"Посты:\n\n{posts}"
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=NEWS_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ])
        assert len(raw) > 50
        assert "Главное" in raw or "главн" in raw.lower()
        assert "<b>" in raw

    async def test_news_with_single_post(self, provider):
        posts = (
            "[2026-06-07 10:00] <Python News> (https://t.me/pythonnews/1)\n"
            "Вышел Python 3.14 с новыми фичами"
        )
        user_prompt = (
            "Тема запроса: Python\n"
            "Окно: последние 24 часов\n"
            "Каналов: 1, релевантных постов: 1\n\n"
            f"Посты:\n\n{posts}"
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=NEWS_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ])
        assert len(raw) > 30
        assert "Python" in raw or "python" in raw
