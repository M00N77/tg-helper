from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove

from src.config import settings
from src.bot.handlers.menu import cmd_menu
from src.bot.lexicon import L
from src.bot.states import OnboardingStates
from src.db.models import User
from src.db.repo import (
    add_team_member, get_or_create_user,
    get_pending_invite, delete_pending_invite,
)
from src.db.session import get_session
from src.userbot.manager import UserbotManager


router = Router(name="start")


@router.message(Command("start", "help"))
async def cmd_start(message: Message, userbot_manager: UserbotManager, state: FSMContext) -> None:
    uid = message.from_user.id
    username = (message.from_user.username or "").lower()
    is_owner = uid == settings.owner_telegram_id

    async with get_session() as session:
        user = await get_or_create_user(session, uid)

        chat_member = message.from_user
        display_name = chat_member.first_name or ""
        if chat_member.last_name:
            display_name += " " + chat_member.last_name

        if not user.display_name:
            if is_owner:
                user.display_name = display_name or "Владелец"
            else:
                await state.set_state(OnboardingStates.waiting_display_name)
                await message.answer(L.ONBOARDING_NAME_PROMPT)
                return

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
    else:
        await message.answer(L.ONBOARDING_DONE.format(name=user.display_name))


@router.message(OnboardingStates.waiting_display_name)
async def process_display_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if len(name) < 2:
        await message.answer("❌ Слишком коротко. Напиши имя (минимум 2 символа).")
        return
    if len(name) > 64:
        await message.answer("❌ Слишком длинно. Максимум 64 символа.")
        return

    uid = message.from_user.id
    async with get_session() as session:
        user = await get_or_create_user(session, uid)
        user.display_name = name

    await state.clear()
    await message.answer(
        L.ONBOARDING_DONE.format(name=name),
        reply_markup=ReplyKeyboardRemove(),
    )
