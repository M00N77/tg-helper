import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.handlers.yougile import YouGileClient
from src.db.repo import (
    ensure_team_member,
    get_team_by_chat,
    get_user_teams,
    set_team_member_yougile_id,
)
from src.db.session import get_session

logger = logging.getLogger(__name__)

_PAGE_SIZE = 20


def make_router() -> Router:
    router = Router(name="group_link")

    _register_handlers(router)
    return router


def _build_user_keyboard(users: list[dict], team_id: int, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start = page * _PAGE_SIZE
    chunk = users[start:start + _PAGE_SIZE]
    for u in chunk:
        uid = u["id"]
        name = u.get("name", uid)
        builder.button(text=name, callback_data=f"link_yougile:{team_id}:{uid}")
    builder.adjust(1)

    if len(users) > _PAGE_SIZE:
        nav = InlineKeyboardBuilder()
        total_pages = (len(users) + _PAGE_SIZE - 1) // _PAGE_SIZE
        if page > 0:
            nav.button(text="⬅️ Назад", callback_data=f"link_page:{team_id}:{page - 1}")
        if page < total_pages - 1:
            nav.button(text="Вперёд ➡️", callback_data=f"link_page:{team_id}:{page + 1}")
        builder.attach(nav)

    return builder.as_markup()


def _register_handlers(router: Router) -> None:

    @router.message(Command("link_yougile"))
    async def cmd_link_yougile(message: Message) -> None:
        chat_id = message.chat.id
        user_id = message.from_user.id

        async with get_session() as session:
            if message.chat.type in ("group", "supergroup"):
                team = await get_team_by_chat(session, chat_id)
            else:
                teams = await get_user_teams(session, user_id)
                if not teams:
                    await message.answer("❌ Вы не привязаны ни к одной команде.")
                    return
                team = teams[0]

            if not team or not team.kanban_token:
                await message.answer("📊 Канбан команды не настроен. Сначала настройте токен доски.")
                return

            client = YouGileClient(team.kanban_token)
            try:
                users = await client.get_users()
            except Exception as e:
                await message.answer(f"❌ Ошибка при получении пользователей YouGile: {e}")
                return

        if not users:
            await message.answer("❌ В компании YouGile нет пользователей для привязки.")
            return

        kb = _build_user_keyboard(users, team.id, page=0)
        await message.answer("👤 Выберите себя из списка пользователей YouGile:", reply_markup=kb)


    @router.callback_query(F.data.startswith("link_page:"))
    async def link_page_callback(callback: CallbackQuery) -> None:
        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer("Ошибка", show_alert=True)
            return
        _, team_id_str, page_str = parts
        team_id = int(team_id_str)
        page = int(page_str)
        chat_id = callback.message.chat.id

        async with get_session() as session:
            if callback.message.chat.type in ("group", "supergroup"):
                team = await get_team_by_chat(session, chat_id)
            else:
                teams = await get_user_teams(session, callback.from_user.id)
                team = teams[0] if teams else None

            if not team or not team.kanban_token:
                await callback.answer("Канбан не настроен", show_alert=True)
                return

            client = YouGileClient(team.kanban_token)
            try:
                users = await client.get_users()
            except Exception as e:
                await callback.answer(f"Ошибка: {e}", show_alert=True)
                return

        kb = _build_user_keyboard(users, team.id, page=page)
        await callback.message.edit_text("👤 Выберите себя из списка пользователей YouGile:", reply_markup=kb)
        await callback.answer()


    @router.callback_query(F.data.startswith("link_yougile:"))
    async def link_yougile_callback(callback: CallbackQuery) -> None:
        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer("Ошибка", show_alert=True)
            return
        _, team_id_str, yougile_user_id = parts
        team_id = int(team_id_str)
        user_id = callback.from_user.id

        async with get_session() as session:
            member = await set_team_member_yougile_id(session, team_id, user_id, yougile_user_id)
            if member is None:
                member = await ensure_team_member(session, team_id, user_id, callback.from_user.full_name)
                member.yougile_user_id = yougile_user_id
            await session.commit()

        await callback.message.edit_text(
            f"✅ Готово, {callback.from_user.full_name}! Теперь задачи будут назначаться на тебя."
        )
        await callback.answer()
