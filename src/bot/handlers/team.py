"""Управление командой: создание, приглашение участников, роли."""
from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import OwnerOrTeamMember, is_team_owner
from src.bot.states import TeamStates
from src.db.models import Team
from src.db.repo import (
    create_pending_invite, create_team, add_team_member, get_team_by_chat,
    get_team_members, remove_team_member, get_user_teams,
)
from src.db.session import get_session


router = Router(name="team")
router.message.filter(OwnerOrTeamMember())
router.callback_query.filter(OwnerOrTeamMember())


@router.message(Command("team"))
async def cmd_team(message: Message, state: FSMContext, command: CommandObject):
    args = (command.args or "").strip().lower()

    if args == "invite":
        async with get_session() as session:
            team = await get_team_by_chat(session, message.chat.id)
        if not team:
            await message.answer("❌ Это не командный чат. Сначала /team → Создать.")
            return
        await state.update_data(team_id=team.id)
        await state.set_state(TeamStates.waiting_invite_username)
        await message.answer(
            f"👥 Введите @username для приглашения в «{team.name}».\nОтмена — /cancel"
        )
        return

    if args == "members":
        async with get_session() as session:
            team = await get_team_by_chat(session, message.chat.id)
            if not team:
                await message.answer("❌ Команда не найдена.")
                return
            members = await get_team_members(session, team.id)
        text = f"👥 <b>Участники «{team.name}»</b>\n\n"
        for m in members:
            icon = "👑" if m.role == "admin" else "👤"
            text += f"{icon} {m.telegram_id} — {m.role}\n"
        text += f"\nВсего: {len(members)}"
        await message.answer(text)
        return

    # Основное меню (без args)
    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)

    kb = InlineKeyboardBuilder()
    if team:
        kb.row(
            InlineKeyboardButton(text="👥 Участники", callback_data="team:members"),
            InlineKeyboardButton(text="➕ Пригласить", callback_data="team:invite"),
        )
        kb.row(
            InlineKeyboardButton(text="📊 Канбан", callback_data="team:kanban"),
            InlineKeyboardButton(text="⚙ Настройки", callback_data="team:settings"),
        )
    else:
        kb.row(
            InlineKeyboardButton(text="➕ Создать команду", callback_data="team:create"),
            InlineKeyboardButton(text="📋 Мои команды", callback_data="team:list"),
        )
    await message.answer(
        "🏢 <b>Управление командой</b>\n\n"
        "• /team invite — пригласить участника\n"
        "• /team members — список участников",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data == "team:create")
async def cb_team_create(callback: CallbackQuery, state: FSMContext):
    """Начало создания команды"""
    await state.set_state(TeamStates.waiting_team_name)
    await callback.message.answer(
        "🏷 <b>Создание команды</b>\n\n"
        "Введите название команды:\n"
        "Пример: «Разработка бэкенда», «Маркетинг», «AI Team»\n\n"
        "Отмена — /cancel"
    )
    await callback.answer()


@router.message(TeamStates.waiting_team_name)
async def step_team_name(message: Message, state: FSMContext):
    """Ввод названия команды"""
    name = message.text.strip()
    if len(name) < 3:
        await message.answer("❌ Название слишком короткое (минимум 3 символа)")
        return

    await state.update_data(team_name=name)
    await state.set_state(TeamStates.waiting_chat_id)

    await message.answer(
        f"✅ Название: <b>{name}</b>\n\n"
        f"📢 <b>Важно!</b>\n"
        f"Добавьте бота в <b>командный чат</b> и дайте ему права администратора.\n\n"
        f"После этого введите <b>ID чата</b>.\n"
        f"Как узнать ID чата?\n"
        f"1. Добавьте @userinfobot в чат\n"
        f"2. Отправьте /start\n"
        f"3. Скопируйте число, которое пришлёт бот\n\n"
        f"Или отправьте любое сообщение в этот чат, и я определю ID сам."
    )


@router.message(TeamStates.waiting_chat_id)
async def step_chat_id(message: Message, state: FSMContext):
    """Ввод ID чата или автоопределение"""
    data = await state.get_data()
    team_name = data["team_name"]

    try:
        chat_id = int(message.text.strip())
    except ValueError:
        chat_id = message.chat.id

    async with get_session() as session:
        team = await create_team(
            session,
            name=team_name,
            telegram_chat_id=chat_id,
            owner_telegram_id=message.from_user.id,
        )
        await add_team_member(session, team.id, message.from_user.id, role="admin")

    await state.clear()

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="📊 Настроить канбан",
            callback_data=f"kanban:setup:{team.id}",
        ),
        InlineKeyboardButton(
            text="👥 Пригласить участников",
            callback_data=f"team:invite:{team.id}",
        ),
    )

    await message.answer(
        f"🎉 <b>Команда «{team_name}» создана!</b>\n\n"
        f"🆔 ID команды: {team.id}\n"
        f"💬 Чат: {chat_id}\n\n"
        f"Теперь вы можете:\n"
        f"• Пригласить участников — /team invite\n"
        f"• Настроить канбан-доску — /kanban\n"
        f"• Начать встречу — /meeting join\n\n"
        f"<i>Бот будет автоматически отслеживать задачи из переписки!</i>",
        reply_markup=kb.as_markup(),
    )


@router.message(TeamStates.waiting_invite_username)
async def step_invite(message: Message, state: FSMContext):
    username = message.text.strip().lstrip("@").lower()
    if not username:
        await message.answer("❌ Введите @username. Отмена — /cancel")
        return

    data = await state.get_data()
    team_id = data.get("team_id")

    team_name = None
    async with get_session() as session:
        team = await session.get(Team, team_id)
        if team:
            team_name = team.name
            await create_pending_invite(
                session,
                team_id=team_id,
                username=username,
                invited_by=message.from_user.id,
            )

    await state.clear()

    if not team_name:
        await message.answer("❌ Команда не найдена.")
        return

    await message.answer(
        f"✅ Приглашение для @{username} сохранено.\n"
        f"Когда он напишет боту /start — автоматически попадёт в «{team_name}»."
    )


@router.callback_query(F.data == "team:members")
async def cb_team_members(callback: CallbackQuery):
    """Список участников команды"""
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
        if not team:
            await callback.message.edit_text("❌ Команда не найдена")
            await callback.answer()
            return
        members = await get_team_members(session, team.id)

    text = f"👥 <b>Участники команды «{team.name}»</b>\n\n"
    for m in members:
        role_icon = "👑" if m.role == "admin" else "👤"
        text += f"{role_icon} {m.telegram_id} — {m.role}\n"

    text += f"\nВсего: {len(members)} участников"

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data="team:back"))

    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data == "team:invite")
async def cb_team_invite(callback: CallbackQuery, state: FSMContext):
    """Приглашение участника из callback"""
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team:
        await callback.message.edit_text("❌ Команда не найдена")
        await callback.answer()
        return

    await state.update_data(team_id=team.id)
    await state.set_state(TeamStates.waiting_invite_username)

    await callback.message.answer(
        f"👥 <b>Приглашение в команду «{team.name}»</b>\n\n"
        f"Введите @username пользователя, которого хотите пригласить.\n"
        f"Пример: @ivan_petrov\n\n"
        f"Пользователь получит ссылку для вступления.\n"
        f"Отмена — /cancel"
    )
    await callback.answer()


@router.callback_query(F.data == "team:settings")
async def cb_team_settings(callback: CallbackQuery):
    """Настройки команды"""
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)

    if not team:
        await callback.message.edit_text("❌ Команда не найдена")
        await callback.answer()
        return

    is_owner = await is_team_owner(callback)

    text = (
        f"⚙ <b>Настройки команды «{team.name}»</b>\n\n"
        f"🆔 ID: {team.id}\n"
        f"💬 Чат: {team.chat_id}\n"
        f"📊 Канбан: {'✅ Подключён' if team.kanban_token else '❌ Не подключён'}\n"
    )

    kb = InlineKeyboardBuilder()
    if team.kanban_token and is_owner:
        kb.row(InlineKeyboardButton(text="📊 Настроить канбан", callback_data="team:kanban"))
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data="team:back"))

    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data == "team:list")
async def cb_team_list(callback: CallbackQuery):
    """Список команд пользователя"""
    async with get_session() as session:
        teams = await get_user_teams(session, callback.from_user.id)

    if not teams:
        await callback.message.edit_text(
            "❌ Вы не состоите ни в одной команде.\n"
            "Создайте её через /team"
        )
        await callback.answer()
        return

    text = "📋 <b>Мои команды</b>\n\n"
    for t in teams:
        text += f"• {t.name} (чат: {t.chat_id})\n"

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data="team:back"))

    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data == "team:kanban")
async def cb_team_kanban(callback: CallbackQuery):
    """Подключение канбана из меню команды"""
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)

    if not team:
        await callback.message.edit_text("❌ Команда не найдена")
        await callback.answer()
        return

    is_owner = await is_team_owner(callback)

    text = f"📊 <b>Канбан команды «{team.name}»</b>\n\n"

    kb = InlineKeyboardBuilder()
    if team.kanban_token:
        kb.row(InlineKeyboardButton(text="📊 Показать доску", callback_data="kanban:board"))
        if is_owner:
            kb.row(InlineKeyboardButton(text="📋 Выбрать доску", callback_data="kanban:change_board"))
            kb.row(InlineKeyboardButton(text="🔄 Сменить токен", callback_data="kanban:relogin"))
            kb.row(InlineKeyboardButton(text="🔗 Отключить канбан", callback_data="kanban:disconnect"))
    else:
        text += "Канбан не подключён."
        if is_owner:
            text += " Нажмите кнопку ниже, чтобы подключить YouGile."
            kb.row(InlineKeyboardButton(text="🔑 Войти в YouGile", callback_data="kanban:setup"))
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data="team:back"))

    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data == "team:back")
async def cb_team_back(callback: CallbackQuery):
    """Назад в главное меню команды"""
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)

    kb = InlineKeyboardBuilder()
    if team:
        kb.row(
            InlineKeyboardButton(text="👥 Участники", callback_data="team:members"),
            InlineKeyboardButton(text="➕ Пригласить", callback_data="team:invite"),
        )
        kb.row(
            InlineKeyboardButton(text="📊 Канбан", callback_data="team:kanban"),
            InlineKeyboardButton(text="⚙ Настройки", callback_data="team:settings"),
        )
    else:
        kb.row(
            InlineKeyboardButton(text="➕ Создать команду", callback_data="team:create"),
            InlineKeyboardButton(text="📋 Мои команды", callback_data="team:list"),
        )

    await callback.message.edit_text(
        "🏢 <b>Управление командой</b>\n\n"
        "Здесь вы можете:\n"
        "• Создать новую команду\n"
        "• Пригласить участников\n"
        "• Настроить роли и права\n"
        "• Подключить канбан-доску\n\n"
        "Команда — это группа людей, для которых бот будет:\n"
        "✅ Отслеживать задачи из чата\n"
        "✅ Напоминать о дедлайнах\n"
        "✅ Участвовать в встречах\n"
        "✅ Вести канбан-доску",
        reply_markup=kb.as_markup()
    )
    await callback.answer()


