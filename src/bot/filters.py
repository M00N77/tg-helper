from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from src.config import settings
from src.db.repo import get_team_by_chat, get_team_members
from src.db.session import get_session


class OwnerOnly(BaseFilter):
    """Допускает владельца + список ALLOWED_TELEGRAM_IDS."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        uid = event.from_user.id if event.from_user else 0
        return uid in settings.all_allowed_ids


def _get_chat_id(event: "Message | CallbackQuery") -> int:
    from aiogram.types import CallbackQuery as CQ
    if isinstance(event, CQ):
        return event.message.chat.id if event.message else 0
    return event.chat.id if event.chat else 0


class TeamAccessByChat(BaseFilter):
    """Допускает любого участника чата, если чат зарегистрирован как командный."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        chat_id = _get_chat_id(event)
        if chat_id == 0:
            return False
        async with get_session() as session:
            team = await get_team_by_chat(session, chat_id)
        return team is not None


class OwnerOrTeamMember(BaseFilter):
    """Допускает владельца ИЛИ участника командного чата."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        uid = event.from_user.id if event.from_user else 0
        if uid in settings.all_allowed_ids:
            return True
        chat_id = _get_chat_id(event)
        if chat_id == 0:
            return False
        async with get_session() as session:
            team = await get_team_by_chat(session, chat_id)
            if team is None:
                return False
            members = await get_team_members(session, team.id)
        return any(m.telegram_id == uid for m in members)


async def is_team_owner(event: Message | CallbackQuery) -> bool:
    """Проверяет, является ли пользователь владельцем команды (или глобальным владельцем)."""
    uid = event.from_user.id if event.from_user else 0
    if uid == 0:
        return False
    if uid in settings.all_allowed_ids:
        return True
    chat_id = _get_chat_id(event)
    if chat_id == 0:
        return False
    async with get_session() as session:
        team = await get_team_by_chat(session, chat_id)
        if team is None:
            return False
    return team.owner_telegram_id == uid
