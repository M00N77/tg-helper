"""Интеграционные тесты поиска чатов с реальным Gemini (keyword expansion + classification)."""
from __future__ import annotations

import json
import os

import pytest

from src.core.chat_finder import _EXPAND_SYS, _CLASSIFY_SYS
from src.llm.base import ChatMessage
from src.llm.gemini_provider import GeminiProvider

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY не задан",
)


@pytest.fixture(scope="module")
def provider():
    return GeminiProvider(os.environ["GEMINI_API_KEY"])


class TestExpandKeywordsReal:
    async def test_returns_keywords_list(self, provider):
        raw = await provider.chat([
            ChatMessage(role="system", content=_EXPAND_SYS),
            ChatMessage(role="user", content="мебель"),
        ])
        raw_clean = raw.strip().strip("`")
        if raw_clean.lower().startswith("json"):
            raw_clean = raw_clean[4:].strip()
        parsed = json.loads(raw_clean)
        assert isinstance(parsed, dict)
        assert "keywords" in parsed
        assert isinstance(parsed["keywords"], list)
        assert len(parsed["keywords"]) >= 3

    async def test_contains_synonyms(self, provider):
        raw = await provider.chat([
            ChatMessage(role="system", content=_EXPAND_SYS),
            ChatMessage(role="user", content="программирование Python"),
        ])
        raw_clean = raw.strip().strip("`")
        if raw_clean.lower().startswith("json"):
            raw_clean = raw_clean[4:].strip()
        parsed = json.loads(raw_clean)
        kws = [k.lower() for k in parsed.get("keywords", [])]
        assert "python" in kws or "питон" in kws or "programming" in kws

    async def test_english_query(self, provider):
        raw = await provider.chat([
            ChatMessage(role="system", content=_EXPAND_SYS),
            ChatMessage(role="user", content="car repair"),
        ])
        raw_clean = raw.strip().strip("`")
        if raw_clean.lower().startswith("json"):
            raw_clean = raw_clean[4:].strip()
        parsed = json.loads(raw_clean)
        kws = parsed.get("keywords", [])
        assert len(kws) >= 3


class TestClassifyContactsReal:
    async def test_returns_matches(self, provider):
        contacts = [
            {"peer_id": 1, "name": "Мебельный Мир", "kind": "user"},
            {"peer_id": 2, "name": "Иван Петров", "kind": "user"},
            {"peer_id": 3, "name": "Строй-Маркет", "kind": "user"},
        ]
        payload = json.dumps({"topic": "мебель", "contacts": contacts}, ensure_ascii=False)
        raw = await provider.chat([
            ChatMessage(role="system", content=_CLASSIFY_SYS),
            ChatMessage(role="user", content=payload),
        ])
        raw_clean = raw.strip().strip("`")
        if raw_clean.lower().startswith("json"):
            raw_clean = raw_clean[4:].strip()
        parsed = json.loads(raw_clean)
        assert isinstance(parsed, dict)
        matches = parsed.get("matches", [])
        assert isinstance(matches, list)
        peer_ids = [m.get("peer_id") for m in matches]
        assert 1 in peer_ids, "Мебельный Мир должен быть найден по теме мебель"

    async def test_scores_in_range(self, provider):
        contacts = [
            {"peer_id": 1, "name": "Кофейня на углу", "kind": "user"},
            {"peer_id": 2, "name": "Книжный Магазин", "kind": "user"},
        ]
        payload = json.dumps({"topic": "книги", "contacts": contacts}, ensure_ascii=False)
        raw = await provider.chat([
            ChatMessage(role="system", content=_CLASSIFY_SYS),
            ChatMessage(role="user", content=payload),
        ])
        raw_clean = raw.strip().strip("`")
        if raw_clean.lower().startswith("json"):
            raw_clean = raw_clean[4:].strip()
        parsed = json.loads(raw_clean)
        for m in parsed.get("matches", []):
            assert 1 <= m.get("score", 0) <= 5

    async def test_no_matches_for_unrelated(self, provider):
        contacts = [
            {"peer_id": 1, "name": "Ресторан Уют", "kind": "user"},
            {"peer_id": 2, "name": "Автосервис", "kind": "user"},
        ]
        payload = json.dumps({"topic": "квантовая физика", "contacts": contacts}, ensure_ascii=False)
        raw = await provider.chat([
            ChatMessage(role="system", content=_CLASSIFY_SYS),
            ChatMessage(role="user", content=payload),
        ])
        raw_clean = raw.strip().strip("`")
        if raw_clean.lower().startswith("json"):
            raw_clean = raw_clean[4:].strip()
        parsed = json.loads(raw_clean)
        matches = parsed.get("matches", [])
        assert len(matches) < 2  # должно быть 0 или 1
