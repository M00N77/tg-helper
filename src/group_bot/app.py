import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.group_bot.handlers import setup as setup_handlers


logger = logging.getLogger(__name__)


def build_group_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(setup_handlers.router)
    return dp


async def run_group_bot(token: str) -> None:
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_group_dispatcher()
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
