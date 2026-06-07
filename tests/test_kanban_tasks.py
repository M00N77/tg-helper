"""Тесты для пагинации cb_kanban_tasks."""
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
    return team


def _make_client(cards):
    client = MagicMock()
    client.get_cards_in_column = AsyncMock(return_value=cards)
    client.get_columns = AsyncMock(return_value=MOCK_COLUMNS)
    client.get_boards = AsyncMock(return_value=MOCK_BOARDS)
    return client


def _nav_buttons(keyboard):
    """Return list of (text, callback_data) for the nav row (◀ or ▶)."""
    for row in keyboard.inline_keyboard:
        for btn in row:
            if btn.text in ("◀", "▶"):
                return [(btn.text, btn.callback_data)]
    return []


def _card_titles(markup):
    """Return list of card titles from the inline keyboard."""
    titles = []
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data.startswith("kanban:task:"):
                titles.append(btn.text.removeprefix("📋 "))
    return titles


class TestKanbanTasksPagination:
    @pytest.mark.asyncio
    async def test_page0_shows_first_10_and_has_next(self):
        """page=0: первые 10 карточек, есть ▶, нет ◀."""
        cb = _make_callback("kanban:tasks:col-1:0")
        client = _make_client(MOCK_25_CARDS)

        with (
            patch("src.bot.handlers.kanban.get_session"),
            patch("src.bot.handlers.kanban.get_team_by_chat", return_value=_make_team()),
            patch("src.bot.handlers.kanban.YouGileClient", return_value=client),
        ):
            await cb_kanban_tasks(cb)

        text = cb.message.edit_text.call_args[0][0]
        assert "(10)" in text

        markup = cb.message.edit_text.call_args[1]["reply_markup"]
        titles = _card_titles(markup)
        assert titles == ["Task 0", "Task 1", "Task 2", "Task 3", "Task 4",
                          "Task 5", "Task 6", "Task 7", "Task 8", "Task 9"]

        nav = _nav_buttons(markup)
        assert nav == [("▶", "kanban:tasks:col-1:1")]

    @pytest.mark.asyncio
    async def test_page2_shows_last_5_and_has_prev(self):
        """page=2: последние 5 карточек, есть ◀, нет ▶."""
        cb = _make_callback("kanban:tasks:col-1:2")
        client = _make_client(MOCK_25_CARDS)

        with (
            patch("src.bot.handlers.kanban.get_session"),
            patch("src.bot.handlers.kanban.get_team_by_chat", return_value=_make_team()),
            patch("src.bot.handlers.kanban.YouGileClient", return_value=client),
        ):
            await cb_kanban_tasks(cb)

        text = cb.message.edit_text.call_args[0][0]
        assert "(5)" in text

        markup = cb.message.edit_text.call_args[1]["reply_markup"]
        titles = _card_titles(markup)
        assert titles == ["Task 20", "Task 21", "Task 22", "Task 23", "Task 24"]

        nav = _nav_buttons(markup)
        assert nav == [("◀", "kanban:tasks:col-1:1")]
