import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.repo import add_team_member, create_team, get_team_by_chat, update_team_chat_id
from src.db.session import get_session
from src.group_bot.filters import GroupOnly

logger = logging.getLogger(__name__)
router = Router(name="group_director")
router.message.filter(GroupOnly())


@router.message(F.migrate_to_chat_id)
async def handle_group_migration(message: Message) -> None:
    """Автоматически обновляет chat_id команды при апгрейде группы до супергруппы."""
    old_id = message.chat.id
    new_id = message.migrate_to_chat_id

    async with get_session() as session:
        team = await update_team_chat_id(session, old_id, new_id)
        if team is not None:
            logger.info(
                "Group migrated: old=%s new=%s team=%s",
                old_id, new_id, team.name,
            )


@router.message(Command("i_am_director"))
async def cmd_i_am_director(message: Message) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id
    chat_title = message.chat.title or f"Чат {chat_id}"

    async with get_session() as session:
        team = await get_team_by_chat(session, chat_id)
        if team is not None:
            await message.answer(
                f"ℹ️ В этом чате уже настроена команда «{team.name}».\n"
                f"Вы — {('директор' if team.owner_telegram_id == user_id else 'участник')}.\n\n"
                f"Команды для управления: /team, /risks, /setup_kanban"
            )
            return

        team = await create_team(
            session,
            name=chat_title,
            telegram_chat_id=chat_id,
            owner_telegram_id=user_id,
        )
        await add_team_member(session, team.id, user_id, role="admin")

    await message.answer(
        f"✅ <b>Команда «{chat_title}» создана!</b>\n\n"
        f"👑 Вы назначены директором.\n"
        f"💬 Чат закреплён за командой.\n\n"
        f"Теперь участники могут:\n"
        f"• Писать в чат — бот будет анализировать риски\n"
        f"• Использовать /risks — посмотреть риски\n"
        f"• Использовать /team — управление командой\n"
        f"• /setup_kanban — подключить канбан-доску\n\n"
        f"Пригласить участников: /team invite"
    )
