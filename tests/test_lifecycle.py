"""Тесты lifecycle: сyпервизия задач, таймауты, shutdown."""
import asyncio

import pytest

from src.core.lifecycle import (
    cancel_and_await,
    shutdown_all,
    wait_until_stopped,
    with_timeout,
)


async def test_with_timeout_success():
    result = await with_timeout(_ok_coro(), timeout=1.0, message="boom")
    assert result == 42


async def test_with_timeout_raises_runtime_error():
    with pytest.raises(RuntimeError, match="hang"):
        await with_timeout(asyncio.sleep(10), timeout=0.05, message="hang")


async def test_cancel_and_await_cancels_running_tasks():
    started = asyncio.Event()

    async def _slow():
        started.set()
        await asyncio.sleep(30)

    task = asyncio.create_task(_slow())
    await started.wait()
    await cancel_and_await([task])
    assert task.done()
    assert task.cancelled()


async def test_cancel_and_await_swallows_task_exceptions():
    async def _boom():
        raise ValueError("boom")

    task = asyncio.create_task(_boom())
    await asyncio.sleep(0)  # дать задаче упасть
    # cancel_and_await не должен пробрасывать исключения завершившихся задач
    await cancel_and_await([task])
    assert task.done()


async def test_wait_until_stopped_returns_none_on_event():
    stop_event = asyncio.Event()

    async def _loop():
        await asyncio.sleep(30)

    task = asyncio.create_task(_loop())
    stop_event.set()
    result = await wait_until_stopped(stop_event, {"bg": task})
    assert result is None
    task.cancel()


async def test_wait_until_stopped_returns_crashed_task():
    stop_event = asyncio.Event()

    async def _boom():
        raise RuntimeError("worker crashed")

    task = asyncio.create_task(_boom())
    crashed = await wait_until_stopped(stop_event, {"bg": task})
    assert crashed is task
    assert isinstance(crashed.exception(), RuntimeError)


async def test_wait_until_stopped_normal_completion_is_shutdown():
    stop_event = asyncio.Event()

    async def _finishes_normally():
        return None

    task = asyncio.create_task(_finishes_normally())
    result = await wait_until_stopped(stop_event, {"bot": task})
    assert result is None


async def test_shutdown_all_cancels_everything():
    stop_event = asyncio.Event()
    tasks = {
        "one": asyncio.create_task(asyncio.sleep(30)),
        "two": asyncio.create_task(asyncio.sleep(30)),
    }
    await shutdown_all(tasks)
    assert all(t.done() for t in tasks.values())
    assert all(t.cancelled() for t in tasks.values())


async def test_background_loops_include_webhook_server():
    """P0-фикс: webhook-сервер должен быть supervised-задачей lifecycle."""
    from src.main import _background_loops

    loops = _background_loops()
    try:
        assert "webhook-server" in loops
    finally:
        for coro in loops.values():
            coro.close()


async def _ok_coro():
    return 42