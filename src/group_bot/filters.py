"""Фильтры aiogram для группового роутера.

GroupOnly пропускает апдейты только из групповых/супергрупповых чатов, чтобы
групповая HR-логика (создание задач, права участников) не срабатывала в личке
с ботом.
"""
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message


class GroupOnly(BaseFilter):
    """Пропускает только сообщения/колбэки из групп и супергрупп."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        message = event if isinstance(event, Message) else event.message
        if message is None:
            return False
        return message.chat.type in ("group", "supergroup")
