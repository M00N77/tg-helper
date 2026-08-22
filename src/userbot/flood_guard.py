"""Централизованный wrapper для Telethon-вызовов с обработкой FloodWaitError.

Telegram может вернуть FloodWait при превышении rate limit.
Без обработки повторные запросы эскалируют ситуацию до бана сессии.
"""
import asyncio
import inspect
import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Максимальное время ожидания FloodWait по умолчанию (секунды).
DEFAULT_MAX_WAIT = 60


async def flood_safe(
    target: Any,
    *args: Any,
    max_wait: int = DEFAULT_MAX_WAIT,
    label: str = "",
    **kwargs: Any,
) -> Any:
    """Выполнить Telethon-вызов, уважая FloodWait.

    Поддерживает:
    1. Callable / async def / lambda: `await flood_safe(lambda: event.respond(reply))`
       или `await flood_safe(event.respond, reply)`.
       В этом случае на каждой попытке создаётся НОВЫЙ объект корутины, что позволяет
       корректно повторить вызов после FloodWait без ошибки `cannot reuse already awaited coroutine`.
    2. Готовую корутину (fallback): `await flood_safe(event.respond(reply))`.
       Если передан уже созданный coroutine-объект, при FloodWait выдерживается пауза,
       но повторный вызов не производится (исключение пробрасывается).
    """
    try:
        from telethon.errors import FloodWaitError
    except ImportError:
        if callable(target):
            res = target(*args, **kwargs)
            return await res if inspect.isawaitable(res) else res
        return await target

    if callable(target):
        for attempt in range(2):
            try:
                res = target(*args, **kwargs)
                return await res if inspect.isawaitable(res) else res
            except FloodWaitError as e:
                if attempt == 0 and e.seconds <= max_wait:
                    logger.warning("FloodWait %ds [%s], sleeping and retrying...", e.seconds, label)
                    await asyncio.sleep(e.seconds + 1)
                    continue
                if e.seconds > max_wait:
                    logger.error("FloodWait %ds > max %ds [%s], aborting", e.seconds, max_wait, label)
                raise
    else:
        # Передан одиночный объект корутины (нельзя перезапустить)
        try:
            return await target
        except FloodWaitError as e:
            if e.seconds <= max_wait:
                logger.warning(
                    "FloodWait %ds [%s] on single coroutine instance. "
                    "Slept %ds. Use callable `lambda: coro()` for auto-retry.",
                    e.seconds, label, e.seconds,
                )
                await asyncio.sleep(e.seconds + 1)
            raise
