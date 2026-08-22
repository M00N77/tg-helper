"""Централизованный wrapper для Telethon-вызовов с обработкой FloodWaitError.

Telegram может вернуть FloodWait при превышении rate limit.
Без обработки повторные запросы эскалируют ситуацию до бана сессии.
"""
import asyncio
import logging
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Максимальное время ожидания FloodWait по умолчанию (секунды).
# При превышении — исключение пробрасывается наверх.
DEFAULT_MAX_WAIT = 60


async def flood_safe(coro, *, max_wait: int = DEFAULT_MAX_WAIT, label: str = ""):
    """Выполнить Telethon-вызов, уважая FloodWait.

    Если Telegram возвращает FloodWait <= max_wait секунд — ждём и повторяем.
    Если FloodWait > max_wait — пробрасываем исключение (лучше пропустить,
    чем заблокировать event loop на часы).

    Args:
        coro: awaitable Telethon-вызов
        max_wait: максимально допустимое время ожидания (сек)
        label: метка для логов (имя вызывающей функции)
    """
    try:
        from telethon.errors import FloodWaitError
    except ImportError:
        # Telethon не установлен — просто выполняем
        return await coro

    try:
        return await coro
    except FloodWaitError as e:
        if e.seconds > max_wait:
            logger.error(
                "FloodWait %ds > max %ds [%s], aborting to prevent ban",
                e.seconds, max_wait, label,
            )
            raise
        logger.warning(
            "FloodWait %ds [%s], sleeping...", e.seconds, label,
        )
        await asyncio.sleep(e.seconds + 1)
        # Повторная попытка. Если снова FloodWait — пробрасываем.
        return await coro
