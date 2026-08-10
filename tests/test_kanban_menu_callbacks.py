"""Регрессионные тесты: menu:kanban:board / menu:kanban:login callback'и.

Баг: cb_menu_kanban_* передавали cmd_kanban_board/cmd_kanban_login
callback.message (сообщение бота), из-за чего from_user.id оказывался ID бота:
- get_user_teams(бот) пуст, get_team_by_owner(бот) = None,
- can_manage_kanban(...) = False → «⛔ Доступно только администраторам»
  даже для владельца/админа команды.

Фикс: в cmd_* передаётся сам CallbackQuery, uid берётся из callback.from_user.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers.kanban import KanbanAuthStates
from src.bot.handlers.menu import cb_menu_kanban_board, cb_menu_kanban_login
from src.db.models import Team, TeamMember

TG_ID = 111
BOT_ID = 999  # ID владельца бота; НЕ должен использоваться как ID пользователя
TEAM_ID = 42
TEAM_CHAT_ID = -100_000_042


def _make_team(owner: int = TG_ID) -> Team:
    return Team(
        id=TEAM_ID,
        chat_id=TEAM_CHAT_ID,
        name="Dev Team",
        kanban_token="enc-token",
        owner_telegram_id=owner,
    )


def _make_member(role: str = "member") -> TeamMember:
    return TeamMember(
        team_id=TEAM_ID,
        telegram_id=TG_ID,
        role=role,
    )


def _make_msg() -> MagicMock:
    msg = MagicMock()
    msg.from_user.id = BOT_ID  # у сообщения бота from_user — сам бот
    msg.chat.id = 10_000  # ЛС с ботом
    msg.answer = AsyncMock()
    return msg


def _make_callback(uid: int = TG_ID) -> MagicMock:
    cb = MagicMock()
    cb.from_user.id = uid
    cb.message = _make_msg()
    cb.answer = AsyncMock()
    return cb


def _make_state() -> MagicMock:
    state = MagicMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    return state


def _session_ctx() -> MagicMock:
    session = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _client_with_board() -> tuple[MagicMock, MagicMock]:
    client = MagicMock()
    client.get_boards = AsyncMock(return_value=[{"id": "b1", "title": "Main"}])
    crypto = MagicMock()
    crypto.decrypt_data = AsyncMock(return_value="plain-token")
    return client, crypto


def _answers(msg: MagicMock) -> list[str]:
    return [call.args[0] for call in msg.answer.call_args_list]


# ── 1. Делегирование: в cmd_* должен уходить callback, а не callback.message ──


class TestMenuKanbanDelegation:
    @pytest.mark.asyncio
    async def test_board_passes_callback_not_message(self):
        cb = _make_callback()
        state = _make_state()
        with patch(
            "src.bot.handlers.kanban.cmd_kanban_board", AsyncMock()
        ) as cmd:
            await cb_menu_kanban_board(cb, state)

        cmd.assert_awaited_once_with(cb, state)

    @pytest.mark.asyncio
    async def test_login_passes_callback_not_message(self):
        cb = _make_callback()
        state = _make_state()
        with patch(
            "src.bot.handlers.kanban.cmd_kanban_login", AsyncMock()
        ) as cmd:
            await cb_menu_kanban_login(cb, state)

        cmd.assert_awaited_once_with(cb, state)


# ── 2. menu:kanban:board — RBAC по callback.from_user ───────────────────────


class TestMenuKanbanBoardCallback:
    @pytest.mark.asyncio
    async def test_owner_gets_access_in_dm(self):
        """OWNER в ЛС (владелец команды, не участник TeamMember) — доступ есть."""
        team = _make_team(owner=TG_ID)
        cb = _make_callback(uid=TG_ID)
        state = _make_state()
        client, crypto = _client_with_board()
        ctx = _session_ctx()

        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban._resolve_dm_team", AsyncMock(return_value=team)),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=None)),
            patch("src.bot.handlers.kanban.crypto_service", crypto),
            patch("src.bot.handlers.kanban.YouGileClient", MagicMock(return_value=client)),
            patch("src.bot.handlers.kanban.set_active_board", AsyncMock()) as set_board,
        ):
            await cb_menu_kanban_board(cb, state)

        assert "⛔ Доступно только администраторам" not in _answers(cb.message)
        client.get_boards.assert_awaited_once()
        set_board.assert_awaited_once()
        assert set_board.call_args[0][1] == TEAM_CHAT_ID

    @pytest.mark.asyncio
    async def test_admin_member_gets_access_in_dm(self):
        """ADMIN TeamMember в ЛС — доступ есть."""
        team = _make_team(owner=TG_ID + 1)
        cb = _make_callback(uid=TG_ID)
        state = _make_state()
        client, crypto = _client_with_board()
        ctx = _session_ctx()

        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban._resolve_dm_team", AsyncMock(return_value=team)),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member(role="admin"))),
            patch("src.bot.handlers.kanban.crypto_service", crypto),
            patch("src.bot.handlers.kanban.YouGileClient", MagicMock(return_value=client)),
            patch("src.bot.handlers.kanban.set_active_board", AsyncMock()),
        ):
            await cb_menu_kanban_board(cb, state)

        assert "⛔ Доступно только администраторам" not in _answers(cb.message)
        client.get_boards.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_member_denied_in_dm(self):
        """MEMBER в ЛС — без административных действий; получает ⛔."""
        team = _make_team(owner=TG_ID + 1)
        cb = _make_callback(uid=TG_ID)
        state = _make_state()
        client, crypto = _client_with_board()
        ctx = _session_ctx()

        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban._resolve_dm_team", AsyncMock(return_value=team)),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member(role="member"))),
            patch("src.bot.handlers.kanban.crypto_service", crypto),
            patch("src.bot.handlers.kanban.YouGileClient", MagicMock(return_value=client)),
            patch("src.bot.handlers.kanban.set_active_board", AsyncMock()),
        ):
            await cb_menu_kanban_board(cb, state)

        assert "⛔ Доступно только администраторам" in _answers(cb.message)
        client.get_boards.assert_not_awaited()


# ── 3. menu:kanban:login — RBAC по callback.from_user ────────────────────────


class TestMenuKanbanLoginCallback:
    @pytest.mark.asyncio
    async def test_owner_gets_access_in_dm(self):
        """OWNER в ЛС — доступ есть, FSM переходит к вводу логина."""
        team = _make_team(owner=TG_ID)
        cb = _make_callback(uid=TG_ID)
        state = _make_state()
        ctx = _session_ctx()

        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_user_teams", AsyncMock(return_value=[team])),
            patch("src.group_bot.permissions.get_role", AsyncMock(return_value="admin")) as get_role,
        ):
            await cb_menu_kanban_login(cb, state)

        get_role.assert_awaited_once_with(TEAM_CHAT_ID, TG_ID)
        assert "⛔ Доступно только администраторам" not in _answers(cb.message)
        state.set_state.assert_awaited_once_with(KanbanAuthStates.waiting_login)
        state.update_data.assert_awaited_once_with(setup_chat_id=TEAM_CHAT_ID)

    @pytest.mark.asyncio
    async def test_admin_member_gets_access_in_dm(self):
        """ADMIN TeamMember в ЛС — доступ есть."""
        team = _make_team(owner=TG_ID + 1)
        cb = _make_callback(uid=TG_ID)
        state = _make_state()
        ctx = _session_ctx()

        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_user_teams", AsyncMock(return_value=[team])),
            patch("src.group_bot.permissions.get_role", AsyncMock(return_value="admin")),
        ):
            await cb_menu_kanban_login(cb, state)

        assert "⛔ Доступно только администраторам" not in _answers(cb.message)
        state.set_state.assert_awaited_once_with(KanbanAuthStates.waiting_login)

    @pytest.mark.asyncio
    async def test_member_denied_in_dm(self):
        """MEMBER в ЛС — получает ⛔, FSM не запускается."""
        team = _make_team(owner=TG_ID + 1)
        member = _make_member(role="member")
        cb = _make_callback(uid=TG_ID)
        state = _make_state()
        ctx = _session_ctx()

        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_user_teams", AsyncMock(return_value=[team])),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=member)),
            patch("src.group_bot.permissions.get_role", AsyncMock(return_value="member")),
        ):
            await cb_menu_kanban_login(cb, state)

        assert "⛔ Доступно только администраторам" in _answers(cb.message)
        state.set_state.assert_not_awaited()


# ── 4. Прямой вызов cmd_* с CallbackQuery (без меню) ────────────────────────


class TestCmdKanbanCallbackDirect:
    @pytest.mark.asyncio
    async def test_cmd_kanban_board_accepts_callback(self):
        from src.bot.handlers.kanban import cmd_kanban_board

        team = _make_team(owner=TG_ID)
        cb = _make_callback(uid=TG_ID)
        state = _make_state()
        client, crypto = _client_with_board()

        with (
            patch("src.bot.handlers.kanban.get_session", return_value=_session_ctx()),
            patch("src.bot.handlers.kanban._resolve_dm_team", AsyncMock(return_value=team)),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=None)),
            patch("src.bot.handlers.kanban.crypto_service", crypto),
            patch("src.bot.handlers.kanban.YouGileClient", MagicMock(return_value=client)),
            patch("src.bot.handlers.kanban.set_active_board", AsyncMock()),
        ):
            await cmd_kanban_board(cb, state)

        assert "⛔ Доступно только администраторам" not in _answers(cb.message)
        client.get_boards.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cmd_kanban_login_accepts_callback(self):
        from src.bot.handlers.kanban import cmd_kanban_login

        team = _make_team(owner=TG_ID)
        cb = _make_callback(uid=TG_ID)
        state = _make_state()

        with (
            patch("src.bot.handlers.kanban.get_session", return_value=_session_ctx()),
            patch("src.bot.handlers.kanban.get_user_teams", AsyncMock(return_value=[team])),
            patch("src.group_bot.permissions.get_role", AsyncMock(return_value="admin")),
        ):
            await cmd_kanban_login(cb, state)

        assert "⛔ Доступно только администраторам" not in _answers(cb.message)
        state.set_state.assert_awaited_once_with(KanbanAuthStates.waiting_login)