"""Тесты защиты входа в FSM от stale-колбэков (fix/fsm-stale-callback-guard)."""
from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest

from src.bot.fsm_utils import enter_state
from src.bot.states import MenuStates, KanbanCardStates


def _cb(data="x"):
    c = MagicMock()
    c.data = data
    c.from_user = SimpleNamespace(id=1)
    c.answer = AsyncMock()
    return c


def _state(cur=None):
    s = MagicMock()
    s.get_state = AsyncMock(return_value=cur)
    s.set_state = AsyncMock()
    s.update_data = AsyncMock()
    return s


@pytest.mark.asyncio
async def test_enter_state_idle():
    s = _state(None)
    assert await enter_state(MenuStates.waiting_chat_name, s, _cb()) is True
    s.set_state.assert_awaited_once_with(MenuStates.waiting_chat_name)


@pytest.mark.asyncio
async def test_enter_state_blocked():
    s = _state("Other:x")
    assert await enter_state(MenuStates.waiting_chat_name, s, _cb()) is False
    s.set_state.assert_not_called()
    # Для проверки alert нужен новый объект callback
    c = _cb()
    s2 = _state("Other:x")
    await enter_state(MenuStates.waiting_chat_name, s2, c)
    c.answer.assert_awaited_once()
    assert "Сначала завершите" in c.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_enter_state_with_extra_data():
    s = _state(None)
    assert await enter_state(KanbanCardStates.setting_deadline, s, _cb(), extra_data={"a": 1}) is True
    s.update_data.assert_awaited_once_with(a=1)