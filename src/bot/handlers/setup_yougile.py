import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import is_team_owner
from src.bot.states import KanbanAuthStates
from src.group_bot.permissions import get_role

logger = logging.getLogger(__name__)
router = Router(name="setup_yougile")


@router.message(Command("setup_yougile"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_setup_yougile(message: Message) -> None:
    """В группе: выдаёт deep-link на приватный чат с ботом для входа по логину/паролю."""
    if not await is_team_owner(message):
        await message.answer("⛔ Только руководитель команды может настраивать канбан.")
        return
    bot_user = await message.bot.get_me()
    deep_link = f"https://t.me/{bot_user.username}?start=yougile_login_{message.chat.id}"
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔑 Войти в YouGile в ЛС", url=deep_link))
    await message.answer(
        "🔐 Настройка YouGile\n\n"
        "Логин и пароль запрашиваются в личных сообщениях с ботом, "
        "чтобы они не попали в общий чат.",
        reply_markup=kb.as_markup(),
    )


async def start_yougile_login_flow(message: Message, state: FSMContext, chat_id: int) -> None:
    """Вызывается из /start yougile_login_{chat_id} (см. start.py) и запускает FSM."""
    await state.set_state(KanbanAuthStates.waiting_login)
    await state.update_data(setup_chat_id=chat_id)
    await message.answer(
        "Введи логин (email) от аккаунта YouGile:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True,
        ),
    )
