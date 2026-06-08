"""Тесты build_board_text с мокнутым YouGileClient.

Проверяет, что после исправления limit=5 счётчик задач корректен
и строка "... и N ещё" появляется только когда задач >5."""
from __future__ import annotations

from unittest.mock import AsyncMock

from src.bot.handlers.kanban import build_board_text


def _mock_client(cards_in_col: list[dict]) -> AsyncMock:
    client = AsyncMock()
    client.get_columns.return_value = [
        {"id": "col1", "title": "Backlog"},
        {"id": "col2", "title": "Готово"},
    ]

    async def get_cards(cid: str, limit: int = 50):
        return cards_in_col

    client.get_cards_in_column = get_cards
    return client


class TestBuildBoardText:
    """Проверяем отображение количества задач в build_board_text."""

    async def _build(self, cards: list[dict]) -> str:
        client = _mock_client(cards)
        return await build_board_text(client, "Тестовая доска")

    async def test_empty_board(self):
        text = await self._build([])
        assert "Backlog" in text
        assert "Готово" in text
        assert "(0)" in text

    async def test_five_cards_no_more_hint(self):
        cards = [{"id": f"t{i}", "title": f"Задача {i}"} for i in range(1, 6)]
        text = await self._build(cards)
        assert "Задача 1" in text
        assert "Задача 5" in text
        assert "ещё" not in text

    async def test_six_cards_shows_more_hint(self):
        cards = [{"id": f"t{i}", "title": f"Задача {i}"} for i in range(1, 7)]
        text = await self._build(cards)
        assert "Задача 1" in text
        assert "Задача 5" in text
        assert "Задача 6" not in text
        assert "и 1 ещё" in text

    async def test_twelve_cards_shows_correct_count(self):
        cards = [{"id": f"t{i}", "title": f"Задача {i}"} for i in range(1, 13)]
        text = await self._build(cards)
        assert "Задача 1" in text
        assert "Задача 5" in text
        assert "Задача 6" not in text
        assert "и 7 ещё" in text

    async def test_task_title_truncated_at_40(self):
        long_title = "А" * 50
        cards = [{"id": "t1", "title": long_title}]
        text = await self._build(cards)
        assert "А" * 40 in text
        assert "А" * 50 not in text
