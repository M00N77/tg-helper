from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove

from src.config import settings
from src.bot.handlers.menu import cmd_menu
from src.bot.lexicon import L
from src.bot.states import OnboardingStates, YouGileSetupStates
from src.db.models import User, PendingInvite, TeamMember, Team
from src.db.repo import (
    get_or_create_user,
    get_team_by_chat,
)
from src.db.session import get_session
from src.userbot.manager import UserbotManager


router = Router(name="start")


@router.message(Command("start", "help"))
async def cmd_start(message: Message, userbot_manager: UserbotManager, state: FSMContext, command: CommandObject | None = None) -> None:
    uid = message.from_user.id
    username = (message.from_user.username or "").lower()
    is_owner = uid == settings.owner_telegram_id

    args = command.args.strip() if command and command.args else ""

    if args.startswith("link_team_"):
        chat_id_str = args.removeprefix("link_team_")
        try:
            chat_id = int(chat_id_str)
        except (ValueError, TypeError):
            await message.answer("❌ Некорректная ссылка.")
            return
        async with get_session() as session:
            team = await get_team_by_chat(session, chat_id)
        if team is None:
            await message.answer("❌ Команда не найдена. Убедитесь, что группа зарегистрирована.")
            return
        await state.set_state(YouGileSetupStates.waiting_token)
        await state.update_data(setup_chat_id=chat_id)
        await message.answer(
            f"🔗 Настройка канбана для команды «{team.name or chat_id}».\n\n"
            "Отправьте ваш API-токен YouGile:\n"
            "(YouGile → Настройки → API → создать ключ)",
        )
        return

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
            from sqlalchemy import select
            invites = await session.execute(
                select(PendingInvite).where(PendingInvite.username == username)
            )
            pending = invites.scalars().all()

            team_names = []
            for invite in pending:
                existing = await session.execute(
                    select(TeamMember).where(
                        TeamMember.team_id == invite.team_id,
                        TeamMember.telegram_id == uid,
                    )
                )
                if not existing.scalar_one_or_none():
                    session.add(TeamMember(
                        team_id=invite.team_id,
                        telegram_id=uid,
                        role="member",
                    ))
                team = await session.get(Team, invite.team_id)
                if team:
                    team_names.append(team.name or f"команду {invite.team_id}")
                await session.delete(invite)

            if pending:
                await session.commit()
                await message.answer(
                    f"👋 Добро пожаловать!\n"
                    f"Ты добавлен в: {', '.join(team_names)}"
                )
                return

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
