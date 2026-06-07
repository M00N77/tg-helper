from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from src.config import settings


class OwnerOnly(BaseFilter):
    """Допускает владельца + список ALLOWED_TELEGRAM_IDS."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return user is not None and user.id in settings.all_allowed_ids
