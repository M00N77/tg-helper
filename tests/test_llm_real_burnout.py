"""Интеграционные тесты анализа выгорания с реальным Gemini."""
from __future__ import annotations

import os

import pytest

from src.bot.handlers.burnout import BURNOUT_PROMPT
from src.llm.base import ChatMessage
from src.llm.gemini_provider import GeminiProvider

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY не задан",
)


@pytest.fixture(scope="module")
def provider():
    return GeminiProvider(os.environ["GEMINI_API_KEY"])


STRESSED_MESSAGES = """Устал, столько задач сегодня навалилось
Не успеваю, слишком много всего
Опять дедлайн горит, разберусь
Когда это закончится наконец
Немного устал но держусь
Задач много, но интересно"""


class TestBurnoutReal:
    async def test_analysis_has_required_sections(self, provider):
        prompt = BURNOUT_PROMPT.format(messages=STRESSED_MESSAGES)
        raw = await provider.chat([
            ChatMessage(role="user", content=prompt),
        ])
        assert "Общее состояние" in raw or "Состояние" in raw
        assert "Индикатор" in raw or "Энергия" in raw
        assert "Рекомендаци" in raw or "рекомендаци" in raw.lower()

    async def test_identifies_stress(self, provider):
        prompt = BURNOUT_PROMPT.format(messages=STRESSED_MESSAGES)
        raw = await provider.chat([
            ChatMessage(role="user", content=prompt),
        ])
        assert "стресс" in raw.lower() or "высок" in raw.lower() or "устал" in raw.lower()

    async def test_positive_messages_low_burnout(self, provider):
        positive = """Всё отлично, работаю с удовольствием
Отличный день, много успел
Команда супер, всё нравится
Новые задачи интересные
Чувствую прилив энергии"""
        prompt = BURNOUT_PROMPT.format(messages=positive)
        raw = await provider.chat([
            ChatMessage(role="user", content=prompt),
        ])
        assert "низк" in raw.lower() or "низкий" in raw.lower() or "отсутствует" in raw.lower() or "норма" in raw.lower()

    async def test_empty_messages(self, provider):
        prompt = BURNOUT_PROMPT.format(messages="")
        raw = await provider.chat([
            ChatMessage(role="user", content=prompt),
        ])
        assert len(raw) > 0
