"""Unit-тесты для /kanban в ЛС: выбор команды, меню доски, мои задачи."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers.kanban import (
    KanbanTeamCB,
    _board_menu_markup,
    _format_my_tasks,
    cb_kanban_my_tasks,
    cb_kanban_team_select,
    cmd_kanban,
)
from src.db.models import Team, TeamMember

TG_ID = 111
TEAM_ID = 42


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


def _make_member(role: str = "member", yougile_id: str | None = "yg-1") -> TeamMember:
    return TeamMember(
        team_id=TEAM_ID,
        telegram_id=TG_ID,
        role=role,
        yougile_user_id=yougile_id,
    )


def _make_message() -> MagicMock:
    msg = MagicMock()
    msg.from_user.id = TG_ID
    msg.answer = AsyncMock()
    return msg


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


def _button_texts(markup) -> list[str]:
    return [t for t, _ in _buttons(markup)]


class TestKanbanTeamCB:
    def test_pack_unpack_roundtrip(self):
        packed = KanbanTeamCB(team_id=TEAM_ID, action="select").pack()
        assert packed.startswith("kb_team:")
        cb = KanbanTeamCB.unpack(packed)
        assert cb.team_id == TEAM_ID
        assert cb.action == "select"


class TestCmdKanban:
    @pytest.mark.asyncio
    async def test_no_teams_message(self):
        msg = _make_message()
        ctx = _session_ctx(_make_team())
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_user_teams", AsyncMock(return_value=[])),
        ):
            await cmd_kanban(msg)

        msg.answer.assert_awaited_once_with(
            "❌ У тебя нет команд. Создай или присоединись в группе"
        )

    @pytest.mark.asyncio
    async def test_single_team_renders_menu(self):
        team = _make_team()
        msg = _make_message()
        ctx = _session_ctx(team)
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_user_teams", AsyncMock(return_value=[team])),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member())),
        ):
            await cmd_kanban(msg)

        text = msg.answer.call_args[0][0]
        assert "Dev Team" in text
        markup = msg.answer.call_args[1]["reply_markup"]
        btns = _buttons(markup)
        assert ("📋 Мои задачи", "my_tasks") in [
            (t, cb.action) for t, cb in btns
        ]
        assert all(cb.team_id == TEAM_ID for _, cb in btns)

    @pytest.mark.asyncio
    async def test_single_team_without_token(self):
        team = _make_team(token=None)
        msg = _make_message()
        ctx = _session_ctx(team)
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_user_teams", AsyncMock(return_value=[team])),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member())),
        ):
            await cmd_kanban(msg)

        text = msg.answer.call_args[0][0]
        assert "не настроена" in text

    @pytest.mark.asyncio
    async def test_multiple_teams_show_picker(self):
        team_a = _make_team(team_id=1, name="Team A")
        team_b = _make_team(team_id=2, name="Team B")
        msg = _make_message()
        ctx = _session_ctx(team_a)
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_user_teams", AsyncMock(return_value=[team_a, team_b])),
        ):
            await cmd_kanban(msg)

        assert msg.answer.call_args[0][0] == "Выбери команду:"
        btns = _buttons(msg.answer.call_args[1]["reply_markup"])
        assert btns == [
            ("Team A", KanbanTeamCB(team_id=1, action="select")),
            ("Team B", KanbanTeamCB(team_id=2, action="select")),
        ]


class TestSelectHandler:
    @pytest.mark.asyncio
    async def test_idor_guard_denies_non_member(self):
        cb = _make_callback()
        ctx = _session_ctx(_make_team())
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=None)),
        ):
            await cb_kanban_team_select(cb, KanbanTeamCB(team_id=TEAM_ID, action="select"))

        cb.answer.assert_awaited_once_with("Доступ запрещен", show_alert=True)
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_member_sees_only_my_tasks(self):
        cb = _make_callback()
        ctx = _session_ctx(_make_team(owner=TG_ID + 1))
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member(role="member"))),
        ):
            await cb_kanban_team_select(cb, KanbanTeamCB(team_id=TEAM_ID, action="select"))

        markup = cb.message.edit_text.call_args[1]["reply_markup"]
        assert _button_texts(markup) == ["📋 Мои задачи"]

    @pytest.mark.asyncio
    async def test_admin_sees_settings_button(self):
        cb = _make_callback()
        ctx = _session_ctx(_make_team(owner=TG_ID + 1))
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member(role="admin"))),
        ):
            await cb_kanban_team_select(cb, KanbanTeamCB(team_id=TEAM_ID, action="select"))

        markup = cb.message.edit_text.call_args[1]["reply_markup"]
        assert _button_texts(markup) == ["📋 Мои задачи", "⚙️ Настройки"]

    @pytest.mark.asyncio
    async def test_owner_role_gets_settings(self):
        cb = _make_callback()
        ctx = _session_ctx(_make_team(owner=TG_ID))
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member(role="member"))),
        ):
            await cb_kanban_team_select(cb, KanbanTeamCB(team_id=TEAM_ID, action="select"))

        markup = cb.message.edit_text.call_args[1]["reply_markup"]
        assert "⚙️ Настройки" in _button_texts(markup)

    @pytest.mark.asyncio
    async def test_board_not_configured(self):
        cb = _make_callback()
        ctx = _session_ctx(_make_team(token=None))
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member())),
        ):
            await cb_kanban_team_select(cb, KanbanTeamCB(team_id=TEAM_ID, action="select"))

        text = cb.message.edit_text.call_args[0][0]
        assert "не настроена" in text


class TestMyTasksHandler:
    @pytest.mark.asyncio
    async def test_idor_guard_denies_non_member(self):
        cb = _make_callback()
        ctx = _session_ctx(_make_team())
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=None)),
        ):
            await cb_kanban_my_tasks(cb, KanbanTeamCB(team_id=TEAM_ID, action="my_tasks"))

        cb.answer.assert_awaited_once_with("Доступ запрещен", show_alert=True)

    @pytest.mark.asyncio
    async def test_user_not_linked_to_yougile(self):
        cb = _make_callback()
        ctx = _session_ctx(_make_team())
        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member(yougile_id=None))),
        ):
            await cb_kanban_my_tasks(cb, KanbanTeamCB(team_id=TEAM_ID, action="my_tasks"))

        cb.answer.assert_awaited_once_with(
            "Твой Telegram не привязан к YouGile. Обратись к администратору",
            show_alert=True,
        )

    @pytest.mark.asyncio
    async def test_fetches_tasks_by_assignee(self):
        team = _make_team()
        member = _make_member(yougile_id="yg-123")
        tasks = [
            {"id": "t1", "title": "Fix bug", "deadline": {"deadline": 1780000000000, "withTime": False}},
            {"id": "t2", "title": "Write docs"},
        ]
        cb = _make_callback()
        client = MagicMock()
        client.get_tasks_by_assignee = AsyncMock(return_value=tasks)
        crypto = MagicMock()
        crypto.decrypt_data = AsyncMock(return_value="plain-token")
        ctx = _session_ctx(team)

        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=member)),
            patch("src.bot.handlers.kanban.crypto_service", crypto),
            patch("src.bot.handlers.kanban.YouGileClient", MagicMock(return_value=client)),
        ):
            await cb_kanban_my_tasks(cb, KanbanTeamCB(team_id=TEAM_ID, action="my_tasks"))

        crypto.decrypt_data.assert_awaited_once_with("enc-token", fallback_raw=True)
        client.get_tasks_by_assignee.assert_awaited_once_with("yg-123")

        text = cb.message.edit_text.call_args[0][0]
        assert "Fix bug" in text
        assert "Write docs" in text
        expected_date = datetime.fromtimestamp(1780000000000 / 1000).strftime("%d.%m.%Y")
        assert f"до {expected_date}" in text

        markup = cb.message.edit_text.call_args[1]["reply_markup"]
        btns = _buttons(markup)
        assert ("◀ К доске", "select") in [(t, c.action) for t, c in btns]

    @pytest.mark.asyncio
    async def test_api_error_shows_alert(self):
        team = _make_team()
        cb = _make_callback()
        client = MagicMock()
        client.get_tasks_by_assignee = AsyncMock(side_effect=RuntimeError("boom"))
        crypto = MagicMock()
        crypto.decrypt_data = AsyncMock(return_value="plain-token")
        ctx = _session_ctx(team)

        with (
            patch("src.bot.handlers.kanban.get_session", return_value=ctx),
            patch("src.bot.handlers.kanban.get_team_member", AsyncMock(return_value=_make_member())),
            patch("src.bot.handlers.kanban.crypto_service", crypto),
            patch("src.bot.handlers.kanban.YouGileClient", MagicMock(return_value=client)),
        ):
            await cb_kanban_my_tasks(cb, KanbanTeamCB(team_id=TEAM_ID, action="my_tasks"))

        cb.answer.assert_awaited_once()
        assert "boom" in cb.answer.call_args[0][0]


class TestFormatHelpers:
    def test_format_my_tasks_empty(self):
        assert _format_my_tasks([]) == "📭 У тебя нет задач на доске"

    def test_board_menu_markup_member_only_my_tasks(self):
        markup = _board_menu_markup(TEAM_ID, is_admin=False)
        assert _button_texts(markup) == ["📋 Мои задачи"]

    def test_board_menu_markup_admin_has_settings(self):
        markup = _board_menu_markup(TEAM_ID, is_admin=True)
        assert _button_texts(markup) == ["📋 Мои задачи", "⚙️ Настройки"]
