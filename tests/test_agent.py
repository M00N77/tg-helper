"""Unit-тесты для LLM-агента: KanbanIntentResponse, парсинг, process_free_text."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.core.agent import (
    AGENT_SYSTEM,
    KANBAN_AGENT_SYSTEM,
    KanbanIntentResponse,
    _safe_parse,
    _safe_parse_kanban,
    _strip_fence,
    process_free_text,
    route_intent,
)


# ── KanbanIntentResponse Pydantic ────────────────────────────────────────────

class TestKanbanIntentResponse:
    def test_valid_create_task(self):
        r = KanbanIntentResponse(intent="create_task", parameters={"title": "Test", "description": "Desc"})
        assert r.intent == "create_task"
        assert r.parameters["title"] == "Test"
        assert r.parameters["description"] == "Desc"

    def test_valid_show_boards(self):
        r = KanbanIntentResponse(intent="show_boards", parameters={})
        assert r.intent == "show_boards"
        assert r.parameters == {}

    def test_valid_move_task(self):
        r = KanbanIntentResponse(intent="move_task", parameters={"task_title": "Buy milk", "target_column": "Done"})
        assert r.intent == "move_task"
        assert r.parameters["task_title"] == "Buy milk"

    def test_valid_smalltalk(self):
        r = KanbanIntentResponse(intent="smalltalk", parameters={"reply": "Hello!"})
        assert r.intent == "smalltalk"
        assert r.parameters["reply"] == "Hello!"

    def test_default_parameters_empty_dict(self):
        r = KanbanIntentResponse(intent="smalltalk")
        assert r.parameters == {}

    def test_invalid_intent_raises(self):
        with pytest.raises(ValidationError):
            KanbanIntentResponse(intent="invalid_intent")

    def test_serialize_roundtrip(self):
        original = KanbanIntentResponse(intent="create_task", parameters={"title": "X"})
        data = original.model_dump()
        restored = KanbanIntentResponse(**data)
        assert restored == original


# ── _strip_fence ─────────────────────────────────────────────────────────────

class TestStripFence:
    def test_no_fence(self):
        assert _strip_fence('{"intent": "test"}') == '{"intent": "test"}'

    def test_triple_backtick(self):
        raw = "```\n{\"intent\": \"test\"}\n```"
        assert _strip_fence(raw) == '{"intent": "test"}'

    def test_json_fence(self):
        raw = "```json\n{\"intent\": \"test\"}\n```"
        assert _strip_fence(raw) == '{"intent": "test"}'

    def test_whitespace(self):
        assert _strip_fence("  \n  test  ") == "test"


# ── _safe_parse ──────────────────────────────────────────────────────────────

class TestSafeParse:
    def test_valid_json(self):
        assert _safe_parse('{"intent": "chat", "reply": "Hi"}') == {"intent": "chat", "reply": "Hi"}

    def test_valid_with_fence(self):
        assert _safe_parse('```\n{"intent": "chat"}\n```') == {"intent": "chat"}

    def test_bad_json_returns_unknown(self):
        assert _safe_parse("not json") == {"intent": "unknown"}

    def test_empty_string_returns_unknown(self):
        assert _safe_parse("") == {"intent": "unknown"}


# ── _safe_parse_kanban ───────────────────────────────────────────────────────

class TestSafeParseKanban:
    """_safe_parse_kanban() возвращает список KanbanIntentResponse."""

    def test_valid_create_task(self):
        raw = '{"intent": "create_task", "parameters": {"title": "Buy milk"}}'
        r = _safe_parse_kanban(raw)
        assert len(r) == 1
        r = r[0]
        assert r.intent == "create_task"
        assert r.parameters["title"] == "Buy milk"

    def test_valid_show_boards(self):
        r = _safe_parse_kanban('{"intent": "show_boards", "parameters": {}}')
        assert len(r) == 1
        assert r[0].intent == "show_boards"

    def test_valid_with_fence(self):
        raw = '```json\n{"intent": "move_task", "parameters": {"task_title": "X", "target_column": "Y"}}\n```'
        r = _safe_parse_kanban(raw)
        assert len(r) == 1
        r = r[0]
        assert r.intent == "move_task"
        assert r.parameters["task_title"] == "X"

    def test_bad_json_falls_to_smalltalk(self):
        r = _safe_parse_kanban("garbage")
        assert len(r) == 1
        r = r[0]
        assert r.intent == "smalltalk"
        assert "reply" in r.parameters

    def test_empty_string_falls_to_smalltalk(self):
        r = _safe_parse_kanban("")
        assert len(r) == 1
        assert r[0].intent == "smalltalk"

    def test_multi_returns_several_intents(self):
        raw = (
            '{"intent": "multi", "actions": ['
            '{"intent": "create_task", "parameters": {"title": "X"}},'
            '{"intent": "create_task", "parameters": {"title": "Y"}}'
            "]}"
        )
        r = _safe_parse_kanban(raw)
        assert len(r) == 2
        assert [i.intent for i in r] == ["create_task", "create_task"]
        assert r[0].parameters["title"] == "X"
        assert r[1].parameters["title"] == "Y"


# ── route_intent ─────────────────────────────────────────────────────────────

class TestRouteIntent:
    @pytest.mark.asyncio
    async def test_routes_correctly(self):
        provider = MagicMock()
        provider.chat = AsyncMock(return_value='{"intent": "chat", "reply": "Hello"}')
        result = await route_intent([provider], "привет", heavy=False)
        assert result["intent"] == "chat"
        assert result["reply"] == "Hello"
        provider.chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bad_llm_response(self):
        provider = MagicMock()
        provider.chat = AsyncMock(return_value="not json")
        result = await route_intent([provider], "test")
        assert result["intent"] == "unknown"


# ── process_free_text ────────────────────────────────────────────────────────

class TestProcessFreeText:
    """process_free_text использует ЛОКАЛЬНЫЕ импорты внутри функции
    (в т.ч. get_provider_chain из src.llm.router).
    Патчим актуальные зависимости по тем namespace'ам, где они резолвятся."""

    @pytest.mark.asyncio
    async def test_no_llm_key_returns_message(self):
        with (
            patch("src.db.session.get_session"),
            patch("src.db.repo.get_or_create_user", return_value=MagicMock()),
            patch("src.llm.router.get_provider_chain", return_value=[]),
        ):
            result = await process_free_text(text="создай задачу", user_id=1, chat_id=1)
            assert "LLM-ключ" in result

    @pytest.mark.asyncio
    async def test_no_kanban_token_returns_message(self):
        mock_owner = MagicMock()
        mock_team = MagicMock()
        mock_team.kanban_token = None

        with (
            patch("src.db.session.get_session"),
            patch("src.llm.router.get_provider_chain", return_value=[MagicMock()]),
            patch("src.db.repo.get_or_create_user", return_value=mock_owner),
            patch("src.db.repo.get_team_by_chat", return_value=mock_team),
        ):
            result = await process_free_text(text="создай задачу", user_id=1, chat_id=1)
            assert "Доска для задач не выбрана" in result

    @pytest.mark.asyncio
    async def test_smalltalk_intent(self):
        mock_owner = MagicMock()
        mock_team = MagicMock()
        mock_team.kanban_token = "token"
        mock_team.active_board_id = None
        mock_team.kanban_board_id = "board_123456"  # len >= 10

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(return_value='{"intent": "smalltalk", "parameters": {"reply": "Привет!"}}')

        with (
            patch("src.db.session.get_session"),
            patch("src.llm.router.get_provider_chain", return_value=[mock_provider]),
            patch("src.db.repo.get_or_create_user", return_value=mock_owner),
            patch("src.db.repo.get_team_by_chat", return_value=mock_team),
        ):
            result = await process_free_text(text="привет", user_id=1, chat_id=1)
            assert "Привет" in result


# ── Системные промпты ────────────────────────────────────────────────────────

class TestSystemPrompts:
    def test_agent_system_has_kanban_intents(self):
        assert "create_task" in AGENT_SYSTEM
        assert "show_boards" in AGENT_SYSTEM
        assert "move_task" in AGENT_SYSTEM
        assert "smalltalk" in AGENT_SYSTEM

    def test_kanban_agent_system_has_all_intents(self):
        assert "create_task" in KANBAN_AGENT_SYSTEM
        assert "show_boards" in KANBAN_AGENT_SYSTEM
        assert "move_task" in KANBAN_AGENT_SYSTEM
        assert "smalltalk" in KANBAN_AGENT_SYSTEM

    def test_kanban_agent_system_expects_parameters_field(self):
        assert "parameters" in KANBAN_AGENT_SYSTEM
