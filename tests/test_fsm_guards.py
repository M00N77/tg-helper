"""Тесты защиты FSM от не-текстового ввода (fix/fsm-non-text-guard)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.fsm_utils import NON_TEXT_HINT, require_text


def _msg(text=None, **kw):
    m = MagicMock()
    m.text = text
    m.from_user = SimpleNamespace(id=1)
    m.answer = AsyncMock()
    m.delete = AsyncMock()
    return m


def _state(data=None):
    s = MagicMock()
    s.get_data = AsyncMock(return_value=data or {})
    s.clear = AsyncMock()
    return s


@pytest.mark.asyncio
async def test_require_text_none_returns_none_and_state_intact():
    m, s = _msg(None), _state()
    assert await require_text(m) is None
    m.answer.assert_awaited_once_with(NON_TEXT_HINT)
    s.clear.assert_not_called()


@pytest.mark.asyncio
async def test_process_deadline_without_text_no_crash():
    from src.bot.handlers.kanban import process_deadline
    m, s = _msg(None), _state({"kanban_task_id": "t", "kanban_token": "k", "kanban_board_id": "b"})
    await process_deadline(m, s)
    m.answer.assert_awaited_once_with(NON_TEXT_HINT)
    s.clear.assert_not_called()


@pytest.mark.asyncio
async def test_process_board_without_text_no_crash():
    from src.bot.handlers.kanban import process_board
    m, s = _msg(None), _state({"boards": []})
    await process_board(m, s)
    m.answer.assert_awaited_once_with(NON_TEXT_HINT)


@pytest.mark.asyncio
async def test_process_login_without_text_no_crash():
    from src.bot.handlers.kanban import process_login
    m, s = _msg(None), _state({})
    await process_login(m, s)
    m.answer.assert_awaited_once_with(NON_TEXT_HINT)


@pytest.mark.asyncio
async def test_process_password_without_text_no_crash():
    from src.bot.handlers.kanban import process_password
    m, s = _msg(None), _state({})
    await process_password(m, s)
    m.answer.assert_awaited_once_with(NON_TEXT_HINT)


@pytest.mark.asyncio
async def test_process_card_title_without_text_no_crash():
    from src.bot.handlers.kanban import process_card_title
    m, s = _msg(None), _state({})
    await process_card_title(m, s)
    m.answer.assert_awaited_once_with(NON_TEXT_HINT)


@pytest.mark.asyncio
async def test_process_card_description_without_text_no_crash():
    from src.bot.handlers.kanban import process_card_description
    m, s = _msg(None), _state({})
    await process_card_description(m, s)
    m.answer.assert_awaited_once_with(NON_TEXT_HINT)


@pytest.mark.asyncio
async def test_step_invite_without_text_no_crash():
    from src.bot.handlers.team import step_invite
    m, s = _msg(None), _state({})
    await step_invite(m, s)
    m.answer.assert_awaited_once_with(NON_TEXT_HINT)


@pytest.mark.asyncio
async def test_process_display_name_without_text_no_crash():
    from src.bot.handlers.start import process_display_name
    m, s = _msg(None), _state({})
    await process_display_name(m, s)
    m.answer.assert_awaited_once_with(NON_TEXT_HINT)


@pytest.mark.asyncio
async def test_step_mtask_edit_without_text_no_crash():
    from src.bot.handlers.meeting import step_mtask_edit
    m, s = _msg(None), _state({"action_id": 1, "task_idx": 0})
    await step_mtask_edit(m, s)
    m.answer.assert_awaited_once_with(NON_TEXT_HINT)


@pytest.mark.asyncio
async def test_step_mtask_add_without_text_no_crash():
    from src.bot.handlers.meeting import step_mtask_add
    m, s = _msg(None), _state({"action_id": 1})
    await step_mtask_add(m, s)
    m.answer.assert_awaited_once_with(NON_TEXT_HINT)
