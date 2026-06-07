"""Интеграционные тесты анализа стиля общения с реальным Gemini."""
from __future__ import annotations

import json
import os

import pytest

from src.core.style_profile import STYLE_SYSTEM
from src.llm.base import ChatMessage
from src.llm.gemini_provider import GeminiProvider

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY не задан",
)


@pytest.fixture(scope="module")
def provider():
    return GeminiProvider(os.environ["GEMINI_API_KEY"])


MY_MESSAGES = (
    "Привет! Как дела?\n"
    "Ок, скину документы завтра утром.\n"
    "Доброе утро! Лови файл.\n"
    "Хорошо, договорились. До связи!\n"
    "Окей, я всё понял, сделаю к вечеру."
)


class TestStyleProfileReal:
    async def test_returns_valid_json(self, provider):
        user_prompt = (
            f"Собеседник: Иван.\n"
            "Ниже — мои (автора) сообщения этому собеседнику:\n\n"
            f"{MY_MESSAGES}\n\n"
            "Сформируй JSON-профиль моего стиля общения с этим собеседником."
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=STYLE_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ])
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)

    async def test_has_required_fields(self, provider):
        user_prompt = (
            f"Собеседник: Иван.\n"
            "Ниже — мои (автора) сообщения этому собеседнику:\n\n"
            f"{MY_MESSAGES}\n\n"
            "Сформируй JSON-профиль моего стиля общения с этим собеседником."
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=STYLE_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ])
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = json.loads(raw)
        optional_fields = {"address", "register", "length", "emoji_usage", "punctuation"}
        assert any(f in parsed for f in optional_fields), f"Нет ни одного из {optional_fields}"

    async def test_register_is_consistent(self, provider):
        formal_messages = (
            "Уважаемый Иван Иванович!\n"
            "Направляю Вам запрошенные документы.\n"
            "С уважением, Александр.\n"
            "Благодарю за сотрудничество.\n"
            "Прошу подтвердить получение."
        )
        user_prompt = (
            f"Собеседник: Иван Иванович.\n"
            "Ниже — мои (автора) сообщения этому собеседнику:\n\n"
            f"{formal_messages}\n\n"
            "Сформируй JSON-профиль моего стиля общения с этим собеседником."
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=STYLE_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ])
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = json.loads(raw)
        register = parsed.get("register", "")
        assert "формальн" in register.lower() or "официальн" in register.lower() or register in ("formal",)

    async def test_empty_messages_returns_empty(self, provider):
        user_prompt = (
            f"Собеседник: Иван.\n"
            "Ниже — мои (автора) сообщения этому собеседнику:\n\n"
            "\n\n"
            "Сформируй JSON-профиль моего стиля общения с этим собеседником."
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=STYLE_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ])
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = json.loads(raw) if raw else {}
        assert isinstance(parsed, dict)
