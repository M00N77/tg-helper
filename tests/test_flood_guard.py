"""Тесты для flood_safe: поддержка callable/lambda, single coroutines, обработка FloodWait."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from telethon.errors import FloodWaitError

from src.userbot.flood_guard import flood_safe


@pytest.mark.asyncio
async def test_flood_safe_callable_success():
    mock_func = AsyncMock(return_value="done")
    result = await flood_safe(mock_func, "arg1", key="val")
    assert result == "done"
    mock_func.assert_awaited_once_with("arg1", key="val")


def _make_flood_wait(seconds: int) -> FloodWaitError:
    err = FloodWaitError(None)
    err.seconds = seconds
    return err


@pytest.mark.asyncio
async def test_flood_safe_callable_retries_on_flood_wait(monkeypatch):
    attempts = 0

    async def flaky_call():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _make_flood_wait(1)
        return "success_on_retry"

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    result = await flood_safe(flaky_call, max_wait=5)
    assert result == "success_on_retry"
    assert attempts == 2
    asyncio.sleep.assert_awaited_once_with(2)


@pytest.mark.asyncio
async def test_flood_safe_callable_aborts_on_large_flood_wait(monkeypatch):
    async def bad_call():
        raise _make_flood_wait(120)

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    with pytest.raises(FloodWaitError):
        await flood_safe(bad_call, max_wait=10)

    asyncio.sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_flood_safe_coroutine_instance():
    async def simple_call():
        return "coro_result"

    result = await flood_safe(simple_call())
    assert result == "coro_result"
