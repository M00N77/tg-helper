"""Тесты сброса FSM при глобальной навигации (fix/fsm-clear-on-navigation)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers.kanban import cb_goto_main_yes
from src.bot.handlers.menu import cb_menu_back, cmd_menu
from src.bot.handlers.start import cmd_start


def _msg(uid: int = 1, **kw):
    m = MagicMock()
    m.text = kw.get("text", "hello")
    m.from_user = SimpleNamespace(
        id=uid,
        username="tester",
        first_name="Test",
        last_name=None,
    )
    m.answer = AsyncMock()
    m.chat = SimpleNamespace(id=uid)
    return m


def _state():
    s = MagicMock()
    s.get_state = AsyncMock(return_value="SomeState:x")
    s.clear = AsyncMock()
    s.set_state = AsyncMock()
    s.update_data = AsyncMock()
    return s


@pytest.mark.asyncio
async def test_cmd_menu_clears_fsm_state():
    with patch(
        "src.bot.handlers.menu._check_warnings",
        new=AsyncMock(return_value=[]),
    ):
        message = _msg()
        state = _state()
        await cmd_menu(message, MagicMock(), state)
        state.clear.assert_awaited_once()
        message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_menu_back_clears_fsm_state():
    with patch(
        "src.bot.handlers.menu._check_warnings",
        new=AsyncMock(return_value=[]),
    ):
        cb = MagicMock()
        cb.from_user = SimpleNamespace(id=1)
        cb.message.edit_text = AsyncMock()
        cb.answer = AsyncMock()
        state = _state()
        await cb_menu_back(cb, MagicMock(), state)
        state.clear.assert_awaited_once()
        cb.message.edit_text.assert_awaited_once()
        cb.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_goto_main_yes_clears_fsm_state():
    with patch(
        "src.bot.handlers.menu.cmd_menu",
        new=AsyncMock(),
    ):
        cb = MagicMock()
        cb.from_user = SimpleNamespace(id=1)
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        state = _state()
        await cb_goto_main_yes(cb, MagicMock(), state)
        state.clear.assert_awaited_once()
        cb.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cmd_start_clears_fsm_state_for_owner():
    owner_id = 12345
    with (
        patch("src.bot.handlers.start.settings", SimpleNamespace(owner_telegram_id=owner_id)),
        patch("src.bot.handlers.start.get_or_create_user", new=AsyncMock(return_value=SimpleNamespace(display_name="Boss"))),
        patch("src.bot.handlers.start.cmd_menu", new=AsyncMock()),
    ):
        message = _msg(owner_id)
        state = _state()
        await cmd_start(message, MagicMock(), state)
        state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_cmd_start_clears_state_before_deep_link_login():
    """Deep-link yougile_login_* не должен перетирать активный стейт без очистки."""
    owner_id = 12345
    with (
        patch("src.bot.handlers.start.settings", SimpleNamespace(owner_telegram_id=owner_id)),
        patch("src.group_bot.permissions.get_role", new=AsyncMock(return_value="owner")),
        patch("src.bot.handlers.setup_yougile.start_yougile_login_flow", new=AsyncMock()),
    ):
        message = _msg(owner_id)
        message.text = "/start yougile_login_42"
        state = _state()
        cmd = SimpleNamespace(args="yougile_login_42")
        await cmd_start(message, MagicMock(), state, command=cmd)
        state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_cmd_start_deep_link_sets_new_state_after_clear():
    """После state.clear() deep-link флоу ставит свой стейт — данные не теряются."""
    owner_id = 12345
    with (
        patch("src.bot.handlers.start.settings", SimpleNamespace(owner_telegram_id=owner_id)),
        patch("src.group_bot.permissions.get_role", new=AsyncMock(return_value="owner")),
        patch("src.bot.handlers.setup_yougile.start_yougile_login_flow", new=AsyncMock()) as login_flow,
    ):
        message = _msg(owner_id)
        message.text = "/start yougile_login_42"
        state = _state()
        cmd = SimpleNamespace(args="yougile_login_42")
        await cmd_start(message, MagicMock(), state, command=cmd)
        login_flow.assert_awaited_once_with(message, state, 42)
