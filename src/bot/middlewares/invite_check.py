from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

from src.db.repo import (
    add_team_member,
    delete_pending_invite,
    get_pending_invite,
    get_team_by_chat,
)
from src.db.session import get_session


class InviteCheckMiddleware(BaseMiddleware):
    """
    Outer middleware: на каждом сообщении из группового чата
    молча проверяет pending_invite и добавляет юзера в team_members.
    Всегда передаёт управление дальше.
    """

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.chat.type in ("group", "supergroup"):
            username = (
                event.from_user.username or ""
            ).lower() if event.from_user else ""

            if username:
                async with get_session() as session:
                    team = await get_team_by_chat(session, event.chat.id)
                    if team:
                        invite = await get_pending_invite(session, username)
                        if invite and invite.team_id == team.id:
                            try:
                                await add_team_member(
                                    session,
                                    team_id=team.id,
                                    telegram_id=event.from_user.id,
                                    role="member",
                                )
                            except Exception:
                                pass  # UniqueConstraint — уже участник
                            await delete_pending_invite(session, invite.id)

        return await handler(event, data)
