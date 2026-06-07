"""Интеграционные тесты извлечения обещаний с реальным Gemini.
Проверяет, что LLM корректно выделяет обязательства из переписки."""
from __future__ import annotations

import json
import os

import pytest

from src.core.commitment_extractor import COMMITMENTS_SYSTEM, _parse_json_array
from src.llm.base import ChatMessage
from src.llm.gemini_provider import GeminiProvider

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY не задан",
)


@pytest.fixture(scope="module")
def provider():
    return GeminiProvider(os.environ["GEMINI_API_KEY"])


def _json_fence(text: str) -> str:
    return f"```json\n{text}\n```"


async def _extract_raw(provider: GeminiProvider, conversation: str) -> list[dict]:
    user_prompt = f"Собеседник: Коллега.\nПереписка:\n\n{conversation}\n\nВыдели обязательства."
    raw = await provider.chat([
        ChatMessage(role="system", content=COMMITMENTS_SYSTEM),
        ChatMessage(role="user", content=user_prompt),
    ])
    return _parse_json_array(raw)


class TestCommitmentExtractionReal:
    async def test_extract_mine_commitment(self, provider):
        conversation = (
            "Коллега: Привет! Не забудь прислать отчёт до пятницы.\n"
            "Я: Ок, сделаю к четвергу."
        )
        items = await _extract_raw(provider, conversation)
        assert len(items) >= 1
        mine = [i for i in items if i.get("direction") == "mine"]
        assert len(mine) >= 1
        assert any("отчёт" in i.get("text", "").lower() for i in mine)

    async def test_extract_theirs_commitment(self, provider):
        conversation = (
            "Я: Ты пришлёшь макет до вторника?\n"
            "Коллега: Да, конечно, сделаю к понедельнику."
        )
        items = await _extract_raw(provider, conversation)
        assert len(items) >= 1
        theirs = [i for i in items if i.get("direction") == "theirs"]
        assert len(theirs) >= 1
        assert any("макет" in i.get("text", "").lower() for i in theirs)

    async def test_extract_with_deadline(self, provider):
        conversation = (
            "Я: Нужно подготовить презентацию к следующей среде.\n"
            "Коллега: Хорошо, я подготовлю.\n"
            "Я: Ок, тогда я жду."
        )
        items = await _extract_raw(provider, conversation)
        assert len(items) >= 1
        with_deadline = [i for i in items if i.get("deadline")]
        assert len(with_deadline) >= 1

    async def test_no_commitments(self, provider):
        conversation = (
            "Коллега: Как дела?\n"
            "Я: Нормально, работаю.\n"
            "Коллега: Понял, удачи!"
        )
        items = await _extract_raw(provider, conversation)
        assert len(items) == 0

    async def test_multiple_commitments(self, provider):
        conversation = (
            "Я: Пришли мне, пожалуйста, контакты бухгалтера.\n"
            "Коллега: Хорошо, скину сегодня вечером.\n"
            "Коллега: И ещё я позвоню заказчику завтра утром.\n"
            "Я: Отлично, тогда я подготовлю спецификацию к пятнице."
        )
        items = await _extract_raw(provider, conversation)
        assert len(items) >= 2
        directions = [i.get("direction") for i in items]
        assert "mine" in directions
        assert "theirs" in directions

    async def test_deadline_in_iso_format(self, provider):
        conversation = (
            "Коллега: Нужно сдать проект до 15 июля 2026 года.\n"
            "Я: Ок, сделаю."
        )
        items = await _extract_raw(provider, conversation)
        mine = [i for i in items if i.get("direction") == "mine"]
        if mine:
            dl = mine[0].get("deadline")
            if dl:
                assert isinstance(dl, str)
                assert "2026-07-15" in dl or "2026-15" in dl or "2026" in dl

    async def test_returns_json_array(self, provider):
        conversation = (
            "Я: Я куплю продукты завтра.\n"
            "Коллега: Супер!"
        )
        raw = await provider.chat([
            ChatMessage(role="system", content=COMMITMENTS_SYSTEM),
            ChatMessage(role="user", content=f"Собеседник: Коллега.\nПереписка:\n\n{conversation}\n\nВыдели обязательства."),
        ])
        parsed = json.loads(raw.strip().strip("`").strip())
        assert isinstance(parsed, list)

    async def test_empty_conversation(self, provider):
        items = await _extract_raw(provider, "")
        assert len(items) == 0

    async def test_commitment_without_explicit_deadline(self, provider):
        conversation = (
            "Я: Я позвоню завтра утром.\n"
            "Коллега: Договорились."
        )
        items = await _extract_raw(provider, conversation)
        mine = [i for i in items if i.get("direction") == "mine"]
        assert len(mine) >= 1
