"""Интеграционные тесты для канбан-диспетчера в free_text.py.

Проверяем _exec_kanban_intent: чтение из плоского JSON (AGENT_SYSTEM),
из вложенного parameters (KanbanIntentResponse), обработку ошибок.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers.free_text import _exec_kanban_intent


@pytest.fixture
def mock_message():
    msg = MagicMock()
    msg.chat.id = 123
    msg.from_user.id = 6235799942
    msg.answer = AsyncMock()
    return msg


def _make_team(token="fake-token", board_id="board-123"):
    team = MagicMock()
    team.kanban_token = token
    team.kanban_board_id = board_id
    team.kanban_provider = "yougile"
    return team


MOCK_COLUMNS = [
    {"id": "col-1", "title": "To Do"},
    {"id": "col-2", "title": "In Progress"},
    {"id": "col-3", "title": "Done"},
]

MOCK_TASKS_COL1 = [
    {"id": "task-1", "title": "Buy milk", "columnId": "col-1"},
    {"id": "task-2", "title": "Write tests", "columnId": "col-1"},
]


class TestExecKanbanIntent:
    """Сквозные тесты _exec_kanban_intent с замоканными YouGileClient и БД.

    ВАЖНО: _exec_kanban_intent импортирует YouGileClient ЛОКАЛЬНО внутри функции,
    поэтому патчим по оригинальному пути src.bot.handlers.yougile.YouGileClient.
    """

    @pytest.mark.asyncio
    async def test_no_kanban_token(self, mock_message):
        """Если токена нет — ответ с просьбой подключить доску."""
        with patch("src.bot.handlers.free_text.get_team_by_chat", return_value=None):
            await _exec_kanban_intent({"intent": "create_task", "title": "Test"}, mock_message)
        mock_message.answer.assert_awaited_once()
        assert "подключи" in mock_message.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_create_task_success(self, mock_message):
        """Успешное создание задачи."""
        mock_client = MagicMock()
        mock_client.get_columns = AsyncMock(return_value=MOCK_COLUMNS)
        mock_client.create_card = AsyncMock(return_value={"id": "new-task"})

        with (
            patch("src.bot.handlers.free_text.get_team_by_chat", return_value=_make_team()),
            patch("src.bot.handlers.yougile.YouGileClient", return_value=mock_client),
        ):
            await _exec_kanban_intent(
                {"intent": "create_task", "title": "Test task", "description": "Some desc"},
                mock_message,
            )

        mock_client.create_card.assert_awaited_once_with("Test task", "Some desc", "col-1")
        mock_message.answer.assert_awaited_once_with("✅ Задача «Test task» создана!")

    @pytest.mark.asyncio
    async def test_create_task_to_specific_column(self, mock_message):
        """Создание задачи с указанием конкретной колонки."""
        mock_client = MagicMock()
        mock_client.get_columns = AsyncMock(return_value=MOCK_COLUMNS)
        mock_client.create_card = AsyncMock(return_value={"id": "new-task"})

        with (
            patch("src.bot.handlers.free_text.get_team_by_chat", return_value=_make_team()),
            patch("src.bot.handlers.yougile.YouGileClient", return_value=mock_client),
        ):
            await _exec_kanban_intent(
                {"intent": "create_task", "title": "Bug fix", "column": "In Progress"},
                mock_message,
            )

        mock_client.create_card.assert_awaited_once_with("Bug fix", "", "col-2")

    @pytest.mark.asyncio
    async def test_create_task_nested_parameters(self, mock_message):
        """Чтение из вложенного parameters (формат KanbanIntentResponse)."""
        mock_client = MagicMock()
        mock_client.get_columns = AsyncMock(return_value=MOCK_COLUMNS)
        mock_client.create_card = AsyncMock(return_value={"id": "new-task"})

        with (
            patch("src.bot.handlers.free_text.get_team_by_chat", return_value=_make_team()),
            patch("src.bot.handlers.yougile.YouGileClient", return_value=mock_client),
        ):
            await _exec_kanban_intent(
                {"intent": "create_task", "parameters": {"title": "Nested task", "column": "Done"}},
                mock_message,
            )

        mock_client.create_card.assert_awaited_once_with("Nested task", "", "col-3")

    @pytest.mark.asyncio
    async def test_create_task_missing_title(self, mock_message):
        """Нет названия задачи — ответ с просьбой уточнить."""
        with (
            patch("src.bot.handlers.free_text.get_team_by_chat", return_value=_make_team()),
        ):
            await _exec_kanban_intent(
                {"intent": "create_task", "title": ""},
                mock_message,
            )
        mock_message.answer.assert_awaited_once()
        assert "Не понял" in mock_message.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_create_task_column_not_found(self, mock_message):
        """Колонка не найдена — список доступных."""
        mock_client = MagicMock()
        mock_client.get_columns = AsyncMock(return_value=MOCK_COLUMNS)

        with (
            patch("src.bot.handlers.free_text.get_team_by_chat", return_value=_make_team()),
            patch("src.bot.handlers.yougile.YouGileClient", return_value=mock_client),
        ):
            await _exec_kanban_intent(
                {"intent": "create_task", "title": "X", "column": "NonExistent"},
                mock_message,
            )

        mock_message.answer.assert_awaited_once()
        text = mock_message.answer.call_args[0][0]
        assert "не найдена" in text
        assert "To Do" in text

    @pytest.mark.asyncio
    async def test_create_task_api_error(self, mock_message):
        """Ошибка API YouGile."""
        mock_client = MagicMock()
        mock_client.get_columns = AsyncMock(side_effect=RuntimeError("Connection failed"))

        with (
            patch("src.bot.handlers.free_text.get_team_by_chat", return_value=_make_team()),
            patch("src.bot.handlers.yougile.YouGileClient", return_value=mock_client),
        ):
            await _exec_kanban_intent(
                {"intent": "create_task", "title": "X"},
                mock_message,
            )

        mock_message.answer.assert_awaited_once()
        assert "Ошибка" in mock_message.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_show_boards(self, mock_message):
        """Показать доску — форматирует колонки и задачи."""
        mock_client = MagicMock()
        mock_client.get_columns = AsyncMock(return_value=MOCK_COLUMNS)
        mock_client.get_cards_in_column = AsyncMock(side_effect=[
            MOCK_TASKS_COL1,   # col-1: 2 задачи
            [],                 # col-2: пусто
            [{"id": "task-3", "title": "Done task"}],  # col-3: 1 задача
        ])

        with (
            patch("src.bot.handlers.free_text.get_team_by_chat", return_value=_make_team()),
            patch("src.bot.handlers.yougile.YouGileClient", return_value=mock_client),
        ):
            await _exec_kanban_intent({"intent": "show_boards"}, mock_message)

        mock_message.answer.assert_awaited_once()
        text = mock_message.answer.call_args[0][0]
        assert "Канбан-доска" in text
        assert "To Do" in text
        assert "In Progress" in text
        assert "Done" in text
        assert "Buy milk" in text
        assert "Write tests" in text

    @pytest.mark.asyncio
    async def test_move_task_success(self, mock_message):
        """Успешное перемещение задачи."""
        mock_client = MagicMock()
        mock_client.get_columns = AsyncMock(return_value=MOCK_COLUMNS)
        mock_client.get_cards_in_column = AsyncMock(side_effect=[
            MOCK_TASKS_COL1, [], []
        ])
        mock_client.move_card = AsyncMock(return_value={"id": "task-1"})

        with (
            patch("src.bot.handlers.free_text.get_team_by_chat", return_value=_make_team()),
            patch("src.bot.handlers.yougile.YouGileClient", return_value=mock_client),
        ):
            await _exec_kanban_intent(
                {"intent": "move_task", "task_title": "Buy milk", "target_column": "Done"},
                mock_message,
            )

        mock_client.move_card.assert_awaited_once_with("task-1", "col-3")
        mock_message.answer.assert_awaited_once()
        assert "перемещена" in mock_message.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_move_task_not_found(self, mock_message):
        """Задача не найдена на доске."""
        mock_client = MagicMock()
        mock_client.get_columns = AsyncMock(return_value=MOCK_COLUMNS)
        mock_client.get_cards_in_column = AsyncMock(return_value=[])

        with (
            patch("src.bot.handlers.free_text.get_team_by_chat", return_value=_make_team()),
            patch("src.bot.handlers.yougile.YouGileClient", return_value=mock_client),
        ):
            await _exec_kanban_intent(
                {"intent": "move_task", "task_title": "Ghost task", "target_column": "Done"},
                mock_message,
            )

        mock_message.answer.assert_awaited_once()
        assert "Не нашёл" in mock_message.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_move_task_missing_params(self, mock_message):
        """Нет названия задачи или колонки."""
        with (
            patch("src.bot.handlers.free_text.get_team_by_chat", return_value=_make_team()),
        ):
            await _exec_kanban_intent(
                {"intent": "move_task", "task_title": "", "target_column": ""},
                mock_message,
            )
        mock_message.answer.assert_awaited_once()
        assert "Укажи" in mock_message.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_smalltalk(self, mock_message):
        """Smalltalk — отвечает текстом."""
        with (
            patch("src.bot.handlers.free_text.get_team_by_chat", return_value=_make_team()),
        ):
            await _exec_kanban_intent(
                {"intent": "smalltalk", "reply": "Чем могу помочь?"},
                mock_message,
            )
        mock_message.answer.assert_awaited_once_with("Чем могу помочь?")

    @pytest.mark.asyncio
    async def test_smalltalk_nested(self, mock_message):
        """Smalltalk с вложенным parameters."""
        with (
            patch("src.bot.handlers.free_text.get_team_by_chat", return_value=_make_team()),
        ):
            await _exec_kanban_intent(
                {"intent": "smalltalk", "parameters": {"reply": "Привет!"}},
                mock_message,
            )
        mock_message.answer.assert_awaited_once_with("Привет!")

    @pytest.mark.asyncio
    async def test_create_task_no_columns(self, mock_message):
        """На доске нет колонок."""
        mock_client = MagicMock()
        mock_client.get_columns = AsyncMock(return_value=[])

        with (
            patch("src.bot.handlers.free_text.get_team_by_chat", return_value=_make_team()),
            patch("src.bot.handlers.yougile.YouGileClient", return_value=mock_client),
        ):
            await _exec_kanban_intent(
                {"intent": "create_task", "title": "X"},
                mock_message,
            )

        mock_message.answer.assert_awaited_once()
        assert "нет колонок" in mock_message.answer.call_args[0][0]
