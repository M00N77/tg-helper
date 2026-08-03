"""Unit-тесты для RBAC управления канбан-доской и синхронизации участников с YouGile."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers.kanban import (
    KanbanTeamCB,
    cb_kanban_sync_users,
    cb_kanban_team_settings,
    find_yougile_match,
)
from src.db.models import Team, TeamMember
from src.group_bot.permissions import can_manage_kanban

TG_ID = 111
TEAM_ID = 42


@pytest.fixture(autouse=True)
def _no_global_allowed_ids():
    with patch("src.group_bot.permissions.settings", SimpleNamespace(all_allowed_ids=set())):
        yield


def _make_team(
    team_id: int = TEAM_ID,
    name: str = "Dev Team",
    token: str | None = "enc-token",
    owner: int = TG_ID,
) -> Team:
    return Team(
        id=team_id,
        chat_id=-100_000_000 + team_id,
        name=name,
        kanban_token=token,
        owner_telegram_id=owner,
    )


def _make_member(
    role: str = "member",
    yougile_id: str | None = None,
    display_name: str | None = "Анна Петрова",
    telegram_id: int = TG_ID,
) -> TeamMember:
    return TeamMember(
        team_id=TEAM_ID,
        telegram_id=telegram_id,
        role=role,
        yougile_user_id=yougile_id,
        display_name=display_name,
    )


def _make_callback(uid: int = TG_ID) -> MagicMock:
    cb = MagicMock()
    cb.from_user.id = uid
    cb.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    return cb


def _session_ctx(team: Team) -> MagicMock:
    session = MagicMock()
    session.get = AsyncMock(return_value=team)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _buttons(markup) -> list[tuple[str, KanbanTeamCB]]:
    out = []
    for row in markup.inline_keyboard:
        for btn in row:
            out.append((btn.text, KanbanTeamCB.unpack(btn.callback_data)))
    return out


class TestCanManageKanban:
    def test_bot_owner_allowed_even_without_team(self):
        with patch("src.group_bot.permissions.settings", SimpleNamespace(all_allowed_ids={TG_ID})):
            assert can_manage_kanban(None, None, TG_ID) is True

    def test_team_owner_allowed(self):
        assert can_manage_kanban(_make_team(owner=TG_ID), None, TG_ID) is True

    def test_admin_role_allowed(self):
        team = _make_team(owner=TG_ID + 1)
        assert can_manage_kanban(team, _make_member(role="admin"), TG_ID) is True

    def test_owner_role_allowed(self):
        team = _make_team(owner=TG_ID + 1)
        assert can_manage_kanban(team, _make_member(role="owner"), TG_ID) is True

    def test_member_denied(self):
        team = _make_team(owner=TG_ID + 1)
        assert can_manage_kanban(team, _make_member(role="member"), TG_ID) is False

    def test_non_member_denied(self):
        team = _make_team(owner=TG_ID + 1)
        assert can_manage_kanban(team, None, TG_ID) is False

    def test_no_team_denied(self):
        assert can_manage_kanban(None, None, TG_ID) is False


class TestFindYougileMatch:
    USERS = [
        {"id": "u1", "name": "Анна Петрова"},
        {"id": "u2", "name": "Иван Сидоров"},
    ]

    def test_exact_match(self):
        assert find_yougile_match(self.USERS, "Анна Петрова") == "u1"

    def test_case_and_space_insensitive(self):
        assert find_yougile_match(self.USERS, "  АННА   петрова ") == "u1"

    def test_substring_match(self):
        assert find_yougile_match(self.USERS, "Иван") == "u2"
        assert find_yougile_match(self.USERS, "Сидоров") == "u2"

    def test_no_match(self):
        assert find_yougile_match(self.USERS, "Пётр") is None

    def test_empty_name(self):
        assert find_yougile_match(self.USERS, None) is None
        assert find_yougile_match(self.USERS, "   ") is None

    def test_empty_users(self):
        assert find_yougile_match([], "Анна Петрова") is None

    def test_user_without_name_skipped(self):
        assert find_yougile_match([{"id": "u3"}], "Анна") is None


class TestSettingsHandler:
    @pytest.mark.asyncio
    async def test_non_member_denied(self):
        cb = _make_callback()
        ctx = _session_ctx(_make_team())
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=None)),
        ):
            await cb_kanban_team_settings(cb, KanbanTeamCB(team_id=TEAM_ID, action="settings"))

        cb.answer.assert_awaited_once_with("Доступ запрещен", show_alert=True)
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_member_denied(self):
        cb = _make_callback()
        ctx = _session_ctx(_make_team(owner=TG_ID + 1))
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member(role="member"))),
        ):
            await cb_kanban_team_settings(cb, KanbanTeamCB(team_id=TEAM_ID, action="settings"))

        cb.answer.assert_awaited_once_with("Доступ запрещен", show_alert=True)
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_admin_sees_sync_button(self):
        cb = _make_callback()
        ctx = _session_ctx(_make_team(owner=TG_ID + 1))
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member(role="admin"))),
        ):
            await cb_kanban_team_settings(cb, KanbanTeamCB(team_id=TEAM_ID, action="settings"))

        markup = cb.message.edit_text.call_args[1]["reply_markup"]
        assert ("🔄 Синхронизировать пользователей", "sync_users") in [
            (t, c.action) for t, c in _buttons(markup)
        ]

    @pytest.mark.asyncio
    async def test_owner_role_sees_sync_button(self):
        cb = _make_callback()
        ctx = _session_ctx(_make_team(owner=TG_ID + 1))
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member(role="owner"))),
        ):
            await cb_kanban_team_settings(cb, KanbanTeamCB(team_id=TEAM_ID, action="settings"))

        markup = cb.message.edit_text.call_args[1]["reply_markup"]
        assert "🔄 Синхронизировать пользователей" in [t for t, _ in _buttons(markup)]


class TestSyncUsersHandler:
    @pytest.mark.asyncio
    async def test_team_not_found(self):
        cb = _make_callback()
        session = MagicMock()
        session.get = AsyncMock(return_value=None)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("src.bot.handlers.kanban.get_session", return_value=ctx):
            await cb_kanban_sync_users(cb, KanbanTeamCB(team_id=TEAM_ID, action="sync_users"))

        cb.answer.assert_awaited_once_with("Команда не найдена", show_alert=True)

    @pytest.mark.asyncio
    async def test_member_denied(self):
        cb = _make_callback()
        ctx = _session_ctx(_make_team(owner=TG_ID + 1))
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member(role="member"))),
            patch("src.bot.handlers.kanban.YouGileClient") as client_cls,
        ):
            await cb_kanban_sync_users(cb, KanbanTeamCB(team_id=TEAM_ID, action="sync_users"))

        cb.answer.assert_awaited_once_with("⛔ Доступно только администраторам", show_alert=True)
        client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_token(self):
        cb = _make_callback()
        ctx = _session_ctx(_make_team())
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member(role="admin"))),
            patch("src.bot.handlers.kanban.get_team_members", AsyncMock(return_value=[])),
            patch("src.bot.handlers.kanban._decrypt_kanban_token", AsyncMock(return_value=None)),
            patch("src.bot.handlers.kanban.YouGileClient") as client_cls,
        ):
            await cb_kanban_sync_users(cb, KanbanTeamCB(team_id=TEAM_ID, action="sync_users"))

        cb.answer.assert_awaited_once_with("Сначала настройте канбан-доску", show_alert=True)
        client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_api_error_shows_alert(self):
        cb = _make_callback()
        ctx = _session_ctx(_make_team())
        client = MagicMock()
        client.get_users = AsyncMock(side_effect=RuntimeError("boom"))
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member(role="admin"))),
            patch("src.bot.handlers.kanban.get_team_members", AsyncMock(return_value=[_make_member()])),
            patch("src.bot.handlers.kanban._decrypt_kanban_token", AsyncMock(return_value="plain-token")),
            patch("src.bot.handlers.kanban.YouGileClient", MagicMock(return_value=client)),
        ):
            await cb_kanban_sync_users(cb, KanbanTeamCB(team_id=TEAM_ID, action="sync_users"))

        cb.answer.assert_awaited_once()
        assert "Ошибка при получении списка YouGile" in cb.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_syncs_members_by_name(self):
        team = _make_team()
        members = [
            _make_member(telegram_id=1, display_name="Анна Петрова"),
            _make_member(telegram_id=2, display_name="Иван"),
            _make_member(telegram_id=3, display_name="Пётр Иванов"),
        ]
        client = MagicMock()
        client.get_users = AsyncMock(return_value=[
            {"id": "u1", "name": "Анна Петрова"},
            {"id": "u2", "name": "Иван Сидоров"},
        ])
        cb = _make_callback()
        ctx = _session_ctx(team)
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member(role="admin"))),
            patch("src.bot.handlers.kanban.get_team_members", AsyncMock(return_value=members)),
            patch("src.bot.handlers.kanban._decrypt_kanban_token", AsyncMock(return_value="plain-token")),
            patch("src.bot.handlers.kanban.YouGileClient", MagicMock(return_value=client)),
            patch("src.bot.handlers.kanban.set_team_member_yougile_id", AsyncMock()) as setter,
        ):
            await cb_kanban_sync_users(cb, KanbanTeamCB(team_id=TEAM_ID, action="sync_users"))

        cb.answer.assert_awaited_once_with(
            "Синхронизация завершена. Привязано 2 пользователей.",
            show_alert=True,
        )
        assert sorted(c.args[2] for c in setter.await_args_list) == [1, 2]
        assert sorted(c.args[3] for c in setter.await_args_list) == ["u1", "u2"]
        client.get_users.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_skips_already_linked(self):
        team = _make_team()
        members = [_make_member(telegram_id=1, display_name="Анна Петрова", yougile_id="u1")]
        client = MagicMock()
        client.get_users = AsyncMock(return_value=[{"id": "u1", "name": "Анна Петрова"}])
        cb = _make_callback()
        ctx = _session_ctx(team)
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member(role="admin"))),
            patch("src.bot.handlers.kanban.get_team_members", AsyncMock(return_value=members)),
            patch("src.bot.handlers.kanban._decrypt_kanban_token", AsyncMock(return_value="plain-token")),
            patch("src.bot.handlers.kanban.YouGileClient", MagicMock(return_value=client)),
            patch("src.bot.handlers.kanban.set_team_member_yougile_id", AsyncMock()) as setter,
        ):
            await cb_kanban_sync_users(cb, KanbanTeamCB(team_id=TEAM_ID, action="sync_users"))

        cb.answer.assert_awaited_once_with(
            "Синхронизация завершена. Привязано 0 пользователей.",
            show_alert=True,
        )
        setter.assert_not_awaited()
