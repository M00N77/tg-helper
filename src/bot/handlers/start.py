from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.filters import OwnerOnly
from src.bot.handlers.menu import cmd_menu
from src.db.repo import get_or_create_user
from src.db.session import get_session
from src.userbot.manager import UserbotManager


router = Router(name="start")
router.message.filter(OwnerOnly())


@router.message(Command("start", "help"))
async def cmd_start(message: Message, userbot_manager: UserbotManager) -> None:
    async with get_session() as session:
        await get_or_create_user(session, message.from_user.id)
    await cmd_menu(message, userbot_manager)
