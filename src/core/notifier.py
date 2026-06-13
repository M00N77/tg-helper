import logging
from typing import TYPE_CHECKING

from src.config import settings


if TYPE_CHECKING:
    from aiogram import Bot


logger = logging.getLogger(__name__)


class Notifier:
    # шлёт сообщения владельцу через control bot — используется userbot-кодом

    def __init__(self) -> None:
        self._bot: "Bot | None" = None

    def attach(self, bot: "Bot") -> None:
        self._bot = bot

    async def notify(self, text: str, *, parse_mode: str | None = "HTML") -> None:
        if self._bot is None:
            logger.warning("Notifier not attached, dropping message: %s", text[:80])
            return
        try:
            await self._bot.send_message(
                chat_id=settings.owner_telegram_id,
                text=text,
                parse_mode=parse_mode,
            )
        except Exception:
            logger.exception("Failed to notify owner")

    async def notify_user(
        self, telegram_id: int, text: str, *, parse_mode: str | None = "HTML"
    ) -> bool:
        """Отправляет сообщение конкретному пользователю. Возвращает True при успехе."""
        if self._bot is None:
            logger.warning("Notifier not attached, cannot notify user %s", telegram_id)
            return False
        try:
            await self._bot.send_message(
                chat_id=telegram_id,
                text=text,
                parse_mode=parse_mode,
            )
            return True
        except Exception:
            logger.exception("Failed to notify user %s", telegram_id)
            return False


notifier = Notifier()
