"""Тесты для cb_kanban_tasks."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers.kanban import cb_kanban_tasks


MOCK_COLUMNS = [
    {"id": "col-1", "title": "To Do"},
    {"id": "col-2", "title": "In Progress"},
    {"id": "col-3", "title": "Done"},
]

MOCK_BOARDS = [
    {"id": "board-1", "title": "My Board"},
]

MOCK_25_CARDS = [
    {"id": f"task-{i}", "title": f"Task {i}"} for i in range(25)
]


def _make_callback(data: str):
    cb = MagicMock()
    cb.data = data
    cb.message.chat.id = 123
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def _make_team():
    team = MagicMock()
    team.kanban_token = "token"
    team.kanban_board_id = "board-1"
    team.active_board_id = "board-1"
    return team


def _make_client(cards):
    client = MagicMock()
    client.get_cards_in_column = AsyncMock(return_value=cards)
    client.get_columns = AsyncMock(return_value=MOCK_COLUMNS)
    client.get_boards = AsyncMock(return_value=MOCK_BOARDS)
    return client


def _nav_buttons(keyboard):
    """Return list of (text, callback_data) pagination buttons (◀ or ▶)."""
    for row in keyboard.inline_keyboard:
        for btn in row:
            if btn.text in ("◀", "▶"):
                return [(btn.text, btn.callback_data)]
    return []


class TestKanbanTasksList:
    @pytest.mark.asyncio
    async def test_shows_all_column_cards(self):
        """Показывает все карточки колонки (до 30) и без пагинации."""
        cb = _make_callback("kanban:tasks:col-1")
        client = _make_client(MOCK_25_CARDS)

        with (
            patch("src.bot.handlers.kanban.get_session"),
            patch(
                "src.bot.handlers.kanban.get_team_for_event",
                new_callable=AsyncMock,
                return_value=_make_team(),
            ),
            patch("src.bot.handlers.kanban.YouGileClient", return_value=client),
        ):
            await cb_kanban_tasks(cb)

        text = cb.message.edit_text.call_args[0][0]
        assert "To Do (25)" in text
        for i in range(25):
            assert f"Task {i}" in text

        nav = _nav_buttons(cb.message.edit_text.call_args[1]["reply_markup"])
        assert nav == []

    @pytest.mark.asyncio
    async def test_shows_first_30_cards_and_notes_rest(self):
        """Карточек больше 30 — показываем первые 30 и упоминаем остаток."""
        cards_40 = [{"id": f"task-{i}", "title": f"Task {i}"} for i in range(40)]
        cb = _make_callback("kanban:tasks:col-1")
        client = _make_client(cards_40)

        with (
            patch("src.bot.handlers.kanban.get_session"),
            patch(
                "src.bot.handlers.kanban.get_team_for_event",
                new_callable=AsyncMock,
                return_value=_make_team(),
            ),
            patch("src.bot.handlers.kanban.YouGileClient", return_value=client),
        ):
            await cb_kanban_tasks(cb)

        text = cb.message.edit_text.call_args[0][0]
        assert "To Do (40)" in text
        assert "Task 29" in text
        assert "Task 30" not in text
        assert "и ещё 10" in text

        nav = _nav_buttons(cb.message.edit_text.call_args[1]["reply_markup"])
        assert nav == []
