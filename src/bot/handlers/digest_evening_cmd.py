"""Ручной запуск вечернего дайджеста — /test_evening_digest."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.filters import OwnerOnly
from src.core.evening_digest import send_evening_digest


router = Router(name="digest_evening_cmd")
router.message.filter(OwnerOnly())


@router.message(Command("test_evening_digest"))
async def cmd_test_evening_digest(message: Message) -> None:
    await message.answer("⏳ Формирую вечерний дайджест...")
    await send_evening_digest(message.from_user.id)
    await message.answer("✅ Готово (проверь уведомление)")
