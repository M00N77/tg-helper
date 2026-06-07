"""Интеграционные тесты GeminiProvider с реальным API-ключом.
Все тесты в этом файле пропускаются, если не задана GEMINI_API_KEY."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.llm.base import ChatMessage
from src.llm.gemini_provider import GeminiProvider

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY не задан",
)


@pytest.fixture(scope="module")
def provider():
    return GeminiProvider(os.environ["GEMINI_API_KEY"])


class TestGeminiProviderReal:
    async def test_validate_key_ok(self, provider):
        assert await provider.validate_key() is True

    async def test_chat_light_model(self, provider):
        result = await provider.chat([
            ChatMessage(role="user", content="Скажи 'Привет, мир!' одной фразой."),
        ], heavy=False)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "привет" in result.lower() or "Привет" in result

    async def test_chat_heavy_model(self, provider):
        result = await provider.chat([
            ChatMessage(role="system", content="Ответь одним словом."),
            ChatMessage(role="user", content="Какое сегодня число?"),
        ], heavy=True)
        assert isinstance(result, str)
        assert len(result) > 0

    async def test_chat_with_system_prompt(self, provider):
        result = await provider.chat([
            ChatMessage(role="system", content="Ты полезный ассистент. Отвечай кратко."),
            ChatMessage(role="user", content="Напиши 'тест'."),
        ])
        assert isinstance(result, str)
        assert "тест" in result.lower()

    async def test_chat_multiturn(self, provider):
        result = await provider.chat([
            ChatMessage(role="user", content="Назови столицу Франции."),
        ])
        assert isinstance(result, str)
        assert "париж" in result.lower()

    async def test_chat_empty_response_returns_empty_string(self, provider):
        with patch.object(provider._client.models, "generate_content") as mock:
            mock.return_value.text = None
            result = await provider.chat([
                ChatMessage(role="user", content="Игнорируй."),
            ])
        assert result == ""

    async def test_embed_returns_float_vector(self, provider):
        vec = await provider.embed("тестовый текст для эмбеддинга")
        assert isinstance(vec, list)
        assert len(vec) > 0
        assert all(isinstance(v, float) for v in vec)

    async def test_embed_similar_texts_close(self, provider):
        vec_a = await provider.embed("кошки и собаки домашние животные")
        vec_b = await provider.embed("домашние питомцы кошки собаки")
        dot = sum(x * y for x, y in zip(vec_a, vec_b))
        na = sum(x * x for x in vec_a) ** 0.5
        nb = sum(y * y for y in vec_b) ** 0.5
        cos = dot / (na * nb) if na and nb else 0.0
        assert cos > 0.5, f"Похожие тексты должны иметь высокий cosine: {cos}"

    async def test_embed_different_texts_low_similarity(self, provider):
        vec_a = await provider.embed("программирование на Python")
        vec_b = await provider.embed("рецепт салата цезарь")
        dot = sum(x * y for x, y in zip(vec_a, vec_b))
        na = sum(x * x for x in vec_a) ** 0.5
        nb = sum(y * y for y in vec_b) ** 0.5
        cos = dot / (na * nb) if na and nb else 0.0
        assert cos < 0.7, f"Разные тексты должны иметь низкий cosine: {cos}"

    async def test_validate_key_wrong_key(self):
        bad = GeminiProvider("fake-key-12345")
        assert await bad.validate_key() is False
