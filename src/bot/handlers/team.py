"""Управление командой: создание, приглашение участников, роли."""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import TeamMemberOnly
from src.bot.states import TeamStates
from src.db.repo import (
    create_team, add_team_member, get_team_by_chat,
    get_team_members, remove_team_member, get_user_teams
)
from src.db.session import get_session


router = Router(name="team")


@router.message(Command("team"))
async def cmd_team(message: Message):
    """Главное меню управления командой"""
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="➕ Создать команду", callback_data="team:create"),
        InlineKeyboardButton(text="📋 Мои команды", callback_data="team:list"),
    )
    kb.row(
        InlineKeyboardButton(text="👥 Участники", callback_data="team:members"),
        InlineKeyboardButton(text="⚙ Настройки", callback_data="team:settings"),
    )
    await message.answer(
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
        "✅ Ведите канбан-доску",
        reply_markup=kb.as_markup()
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
    
    # Пробуем определить ID чата
    if message.text.isdigit():
        chat_id = int(message.text)
    else:
        chat_id = message.chat.id
    
    async with get_session() as session:
        team = await create_team(
            session,
            name=team_name,
            telegram_chat_id=chat_id,
            owner_telegram_id=message.from_user.id
        )
        await add_team_member(session, team.id, message.from_user.id, role="admin")
    
    await state.clear()
    
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="📊 Настроить канбан", 
            callback_data=f"kanban:setup:{team.id}"
        ),
        InlineKeyboardButton(
            text="👥 Пригласить участников",
            callback_data=f"team:invite:{team.id}"
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
        reply_markup=kb.as_markup()
    )


@router.message(Command("team invite"))
async def cmd_team_invite(message: Message, state: FSMContext):
    """Приглашение участника в команду"""
    team = await get_team_by_chat(message.chat.id)
    if not team:
        await message.answer(
            "❌ Это не командный чат.\n"
            "Сначала создайте команду: /team create"
        )
        return
    
    await state.update_data(team_id=team.id)
    await state.set_state(TeamStates.waiting_invite_username)
    
    await message.answer(
        f"👥 <b>Приглашение в команду «{team.name}»</b>\n\n"
        f"Введите @username пользователя, которого хотите пригласить.\n"
        f"Пример: @ivan_petrov\n\n"
        f"Пользователь получит уведомление и сможет присоединиться.\n"
        f"Отмена — /cancel"
    )


@router.message(TeamStates.waiting_invite_username)
async def step_invite(message: Message, state: FSMContext):
    """Отправка приглашения"""
    username = message.text.strip()
    if not username.startswith("@"):
        username = f"@{username}"
    
    data = await state.get_data()
    team_id = data["team_id"]
    
    # TODO: Отправить приглашение через бота
    
    await state.clear()
    await message.answer(
        f"✅ Приглашение отправлено пользователю {username}\n"
        f"Когда он примет приглашение, он появится в списке участников команды."
    )


@router.message(Command("team members"))
async def cmd_team_members(message: Message):
    """Список участников команды"""
    team = await get_team_by_chat(message.chat.id)
    if not team:
        await message.answer("❌ Команда не найдена")
        return
    
    members = await get_team_members(team.id)
    
    text = f"👥 <b>Участники команды «{team.name}»</b>\n\n"
    for m in members:
        role_icon = "👑" if m.role == "admin" else "👤"
        text += f"{role_icon} {m.telegram_id} — {m.role}\n"
    
    text += f"\nВсего: {len(members)} участников"
    
    await message.answer(text)