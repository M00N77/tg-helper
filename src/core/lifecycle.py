"""Контролируемый lifecycle: супервизия фоновых задач, таймауты, shutdown.

Используется вместо asyncio.TaskGroup, потому что нужен предсказуемый
порядок shutdown и единая обработка ошибок cleanup.

Схема:

    stop_event ──┐
    bot_task  ───┤
    webhook_task ─┼── wait_until_stopped() ──► crash -> exit 1 / normal -> exit 0
    bg_tasks   ───┘
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


async def cancel_and_await(
    tasks: list[asyncio.Task],
    *,
    timeout: float = 30.0,
) -> None:
    """Отменяет все задачи и дожидается их завершения.

    CancelledError и любые исключения внутри задач поглощаются —
    cleanup не должен падать из-за задач, которые уже сломались.
    """
    for t in tasks:
        if not t.done():
            t.cancel()

    done, pending = await asyncio.wait(tasks, timeout=timeout)
    if pending:
        logger.warning(
            "%d task(s) did not finish within %.1fs after cancel",
            len(pending), timeout,
        )
        for t in pending:
            t.cancel()

    for t in done:
        try:
            t.exception()
        except (asyncio.CancelledError, Exception):
            pass


async def wait_until_stopped(
    stop_event: asyncio.Event,
    tasks: dict[str, asyncio.Task],
) -> asyncio.Task | None:
    """Ждёт либо stop_event, либо завершения любой supervised-задачи.

    Возвращает упавшую задачу (с исключением), если какая-то задача
    неожиданно завершилась — в этом случае процесс должен уйти с кодом 1,
    чтобы Railway перезапустил приложение.

    Нормальное завершение задачи (без исключения) считается запросом
    на shutdown — так aiogram polling, завершившийся по SIGTERM,
    распознаётся как штатный останов.
    """
    stop_waiter = asyncio.create_task(stop_event.wait(), name="stop-waiter")
    pending: set[asyncio.Task] = set(tasks.values())
    try:
        while pending:
            done, pending = await asyncio.wait(
                pending | {stop_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_waiter in done:
                return None

            finished = done - {stop_waiter}
            if not finished:
                continue

            for t in finished:
                if t.cancelled():
                    continue
                exc = t.exception()
                if exc is not None:
                    return t

            # Задача завершилась без исключения — штатный shutdown.
            return None
        return None
    finally:
        stop_waiter.cancel()


async def with_timeout(
    awaitable,
    timeout: float,
    message: str,
):
    """Выполняет awaitable с таймаутом; при превышении — RuntimeError(message).

    Внутренняя задача отменяется asyncio.wait_for автоматически.
    """
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except asyncio.TimeoutError:
        raise RuntimeError(message) from None


async def shutdown_all(
    tasks: dict[str, asyncio.Task],
    *,
    cancel_timeout: float = 30.0,
) -> None:
    """Отменяет supervised-задачи и дожидается их остановки."""
    await cancel_and_await(list(tasks.values()), timeout=cancel_timeout)