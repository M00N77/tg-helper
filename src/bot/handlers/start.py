from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.config import settings
from src.bot.handlers.menu import cmd_menu
from src.bot.lexicon import L
from src.db.repo import (
    add_team_member, get_or_create_user,
    get_pending_invite, delete_pending_invite,
)
from src.db.session import get_session
from src.userbot.manager import UserbotManager


router = Router(name="start")


@router.message(Command("start", "help"))
async def cmd_start(message: Message, userbot_manager: UserbotManager) -> None:
    uid = message.from_user.id
    username = (message.from_user.username or "").lower()
    is_owner = uid == settings.owner_telegram_id

    async with get_session() as session:
        await get_or_create_user(session, uid)

        if username and not is_owner:
            invite = await get_pending_invite(session, username)
            if invite:
                await add_team_member(
                    session,
                    team_id=invite.team_id,
                    telegram_id=uid,
                    role="member",
                )
                await delete_pending_invite(session, invite.id)
                await message.answer(L.INVITE_ACCEPTED)

    if is_owner:
        await cmd_menu(message, userbot_manager)
    elif not is_owner:
        await message.answer(L.ONBOARDING)
