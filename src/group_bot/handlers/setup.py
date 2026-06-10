from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select

from src.db.models import Team, TeamMember
from src.db.repo import get_team_by_chat
from src.db.session import get_session
from src.group_bot.filters import GroupOnly
from src.group_bot.permissions import is_admin, get_role

router = Router(name="group_setup")
router.message.filter(GroupOnly())


@router.message(Command("i_am_director"))
async def cmd_i_am_director(message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    async with get_session() as session:
        team = await get_team_by_chat(session, chat_id)
        if team is None:
            team = Team(chat_id=chat_id, name=message.chat.title or "Команда")
            session.add(team)
            await session.flush()

        existing = await session.execute(
            select(TeamMember).where(
                TeamMember.team_id == team.id,
                TeamMember.role == "admin",
            )
        )
        if existing.scalar_one_or_none() is not None:
            await message.answer(
                "❌ В этой команде уже есть директор. "
                "Если это ошибка — обратитесь к текущему директору за /grant_admin."
            )
            return

        member_result = await session.execute(
            select(TeamMember).where(
                TeamMember.team_id == team.id,
                TeamMember.telegram_id == user_id,
            )
        )
        member = member_result.scalar_one_or_none()
        if member is None:
            member = TeamMember(team_id=team.id, telegram_id=user_id, role="admin")
            session.add(member)
        else:
            member.role = "admin"

        await session.commit()

    name = message.from_user.full_name
    await message.answer(
        f"✅ {name} назначен(а) директором команды «{team.name}».\n"
        f"Используйте /grant_admin (ответом на сообщение) чтобы назначить других админов."
    )


@router.message(Command("grant_admin"))
async def cmd_grant_admin(message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not await is_admin(chat_id, user_id):
        await message.answer("❌ Только директор может назначать админов.")
        return

    if not message.reply_to_message:
        await message.answer(
            "Ответьте этой командой на сообщение пользователя, "
            "которого хотите назначить админом."
        )
        return

    target = message.reply_to_message.from_user
    if target.is_bot:
        await message.answer("❌ Нельзя назначить бота админом.")
        return

    async with get_session() as session:
        team = await get_team_by_chat(session, chat_id)
        result = await session.execute(
            select(TeamMember).where(
                TeamMember.team_id == team.id,
                TeamMember.telegram_id == target.id,
            )
        )
        member = result.scalar_one_or_none()
        if member is None:
            member = TeamMember(team_id=team.id, telegram_id=target.id, role="admin")
            session.add(member)
        else:
            member.role = "admin"
        await session.commit()

    await message.answer(f"✅ {target.full_name} теперь админ команды.")


@router.message(Command("team_status"))
async def cmd_team_status(message: Message):
    chat_id = message.chat.id
    role = await get_role(chat_id, message.from_user.id)

    async with get_session() as session:
        team = await get_team_by_chat(session, chat_id)

    if team is None:
        await message.answer(
            "Команда ещё не настроена. Используйте /i_am_director "
            "чтобы стать директором этой команды."
        )
        return

    role_label = {"admin": "👑 Директор/Админ", "member": "👤 Участник"}.get(role, "— (не в команде)")
    await message.answer(
        f"📋 <b>Команда:</b> {team.name}\n"
        f"<b>Ваша роль:</b> {role_label}\n"
        f"📊 Канбан: {'подключён' if team.kanban_token else 'не подключён'}"
    )


@router.message(Command("start", "help"))
async def cmd_group_start(message: Message):
    await message.answer(
        "👋 Я командный помощник.\n\n"
        "/i_am_director — стать директором этой команды (один раз)\n"
        "/grant_admin — назначить админа (ответом на сообщение, для директора)\n"
        "/team_status — статус команды и ваша роль"
    )
