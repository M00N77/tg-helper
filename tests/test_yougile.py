"""Unit-тесты для YouGileClient с замокаными HTTP-запросами."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers.yougile import YouGileClient


@pytest.fixture
def client():
    return YouGileClient(api_token="fake-token", board_id="board-123")


MOCK_COLUMNS = {
    "content": [
        {"id": "col-1", "title": "To Do"},
        {"id": "col-2", "title": "In Progress"},
        {"id": "col-3", "title": "Done"},
    ]
}

MOCK_TASKS = {
    "content": [
        {"id": "task-1", "title": "Buy milk", "columnId": "col-1"},
        {"id": "task-2", "title": "Write tests", "columnId": "col-1"},
    ]
}

MOCK_BOARDS = {
    "content": [
        {"id": "board-123", "title": "Project Alpha"},
        {"id": "board-456", "title": "Project Beta"},
    ]
}


def _mock_response(json_data: dict, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data)
    resp.text = str(json_data)
    resp.raise_for_status = MagicMock()
    return resp


def _mock_failed_response(status_code: int, text: str = "error"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


class TestGetColumns:
    @pytest.mark.asyncio
    async def test_success(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=_mock_response(MOCK_COLUMNS)
            )
            result = await client.get_columns()
            assert len(result) == 3
            assert result[0]["title"] == "To Do"
            assert result[2]["title"] == "Done"

    @pytest.mark.asyncio
    async def test_empty_columns(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=_mock_response({"content": []})
            )
            result = await client.get_columns()
            assert result == []

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_resp = _mock_failed_response(500)
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
            with pytest.raises(Exception):
                await client.get_columns()


class TestCreateCard:
    @pytest.mark.asyncio
    async def test_success(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=_mock_response({"id": "new-task-1", "title": "Test"}, 201)
            )
            result = await client.create_card("Test", "Description", "col-1")
            assert result["id"] == "new-task-1"
            assert result["title"] == "Test"

    @pytest.mark.asyncio
    async def test_without_description(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_post = AsyncMock(return_value=_mock_response({"id": "new-task-2"}, 201))
            mock_httpx.return_value.__aenter__.return_value.post = mock_post
            result = await client.create_card("Test", "", "col-1")
            assert result["id"] == "new-task-2"

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_resp = _mock_failed_response(400, "Bad Request")
            mock_httpx.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
            with pytest.raises(RuntimeError, match="400"):
                await client.create_card("Test", "Desc", "col-1")


class TestMoveCard:
    @pytest.mark.asyncio
    async def test_success(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.put = AsyncMock(
                return_value=_mock_response({"id": "task-1"})
            )
            result = await client.move_card("task-1", "col-2")
            assert result["id"] == "task-1"

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_resp = _mock_failed_response(404, "Not Found")
            mock_httpx.return_value.__aenter__.return_value.put = AsyncMock(return_value=mock_resp)
            with pytest.raises(RuntimeError, match="404"):
                await client.move_card("invalid-id", "col-2")


class TestGetBoards:
    @pytest.mark.asyncio
    async def test_success(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=_mock_response(MOCK_BOARDS)
            )
            result = await client.get_boards()
            assert len(result) == 2
            assert result[0]["title"] == "Project Alpha"

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_resp = _mock_failed_response(500)
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
            with pytest.raises(RuntimeError):
                await client.get_boards()


class TestGetCardsInColumn:
    @pytest.mark.asyncio
    async def test_success(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=_mock_response(MOCK_TASKS)
            )
            result = await client.get_cards_in_column("col-1")
            assert len(result) == 2
            assert result[0]["title"] == "Buy milk"

    @pytest.mark.asyncio
    async def test_empty_column(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=_mock_response({"content": []})
            )
            result = await client.get_cards_in_column("col-empty")
            assert result == []


class TestGetTask:
    @pytest.mark.asyncio
    async def test_success(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=_mock_response({"id": "task-1", "title": "Buy milk", "columnId": "col-1"})
            )
            result = await client.get_task("task-1")
            assert result["title"] == "Buy milk"
            assert result["columnId"] == "col-1"


class TestDeleteTask:
    @pytest.mark.asyncio
    async def test_success(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.delete = AsyncMock(
                return_value=_mock_response({}, 204)
            )
            await client.delete_task("task-1")

    @pytest.mark.asyncio
    async def test_raises_on_error(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_resp = _mock_failed_response(500, "Server Error")
            mock_httpx.return_value.__aenter__.return_value.delete = AsyncMock(return_value=mock_resp)
            with pytest.raises(RuntimeError, match="500"):
                await client.delete_task("task-1")


class TestUpdateCard:
    @pytest.mark.asyncio
    async def test_success(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.put = AsyncMock(
                return_value=_mock_response({"id": "task-1"})
            )
            result = await client.update_card("task-1", title="New Title")
            assert result["id"] == "task-1"

    @pytest.mark.asyncio
    async def test_with_description(self, client):
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.put = AsyncMock(
                return_value=_mock_response({"id": "task-1"})
            )
            result = await client.update_card("task-1", description="New Desc")
            assert result["id"] == "task-1"
