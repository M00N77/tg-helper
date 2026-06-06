"""Интеграция с YouGile/Trello канбан-доской."""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.states import KanbanStates

from src.bot.handlers.yougile import YouGileClient
from src.db.session import get_session
from src.bot.states import KanbanAuthStates
from src.db.repo import update_team_kanban, get_team_by_chat


router = Router(name="kanban")


@router.message(Command("kanban"))
async def cmd_kanban(message: Message):
    """Управление канбан-доской"""
    team = await get_team_by_chat(message.chat.id)
    
    kb = InlineKeyboardBuilder()
    if not team or not team.kanban_token:
        kb.row(InlineKeyboardButton(text="🔌 Подключить доску", callback_data="kanban:setup"))
    else:
        kb.row(
            InlineKeyboardButton(text="📊 Показать доску", callback_data="kanban:board"),
            InlineKeyboardButton(text="➕ Создать задачу", callback_data="kanban:add"),
        )
        kb.row(
            InlineKeyboardButton(text="🔄 Синхронизировать", callback_data="kanban:sync"),
            InlineKeyboardButton(text="📈 Статистика", callback_data="kanban:stats"),
        )
        kb.row(InlineKeyboardButton(text="⚙ Настройки", callback_data="kanban:settings"))
    
    await message.answer(
        "📊 <b>Канбан-доска</b>\n\n"
        "Бот автоматически:\n"
        "✅ Создаёт карточки из задач в чате\n"
        "✅ Назначает ответственных\n"
        "✅ Отслеживает дедлайны\n"
        "✅ Перемещает задачи по статусам\n\n"
        "Поддерживаются: YouGile, Trello",
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data == "kanban:setup")
async def cb_kanban_setup(callback: CallbackQuery, state: FSMContext):
    """Настройка подключения к канбан-доске"""
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="📦 YouGile", callback_data="kanban:provider:yougile"),
        InlineKeyboardButton(text="📌 Trello", callback_data="kanban:provider:trello"),
    )
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data="kanban:back"))
    
    await callback.message.edit_text(
        "🔌 <b>Выберите канбан-доску</b>\n\n"
        "YouGile — российский сервис\n"
        "Trello — международный",
        reply_markup=kb.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("kanban:provider:"))
async def cb_kanban_provider(callback: CallbackQuery, state: FSMContext):
    """Выбор провайдера и ввод токена"""
    provider = callback.data.split(":")[2]
    
    await state.update_data(kanban_provider=provider)
    await state.set_state(KanbanStates.waiting_token)
    
    if provider == "yougile":
        instructions = (
            "1. Перейдите в https://yougile.com\n"
            "2. Создайте доску или откройте существующую\n"
            "3. Настройки → API → Создать токен\n"
            "4. Скопируйте токен"
        )
    else:
        instructions = (
            "1. Перейдите в https://trello.com\n"
            "2. Откройте https://trello.com/power-ups/admin\n"
            "3. Создайте API Key\n"
            "4. Скопируйте ключ и токен"
        )
    
    await callback.message.answer(
        f"🔑 <b>Введите API токен для {provider}</b>\n\n"
        f"{instructions}\n\n"
        f"Формат: TOKEN:BOARD_ID\n"
        f"Пример: your_token_here:board_id_here\n\n"
        f"Отмена — /cancel"
    )
    await callback.answer()


@router.message(KanbanStates.waiting_token)
async def step_kanban_token(message: Message, state: FSMContext):
    """Сохранение токена и настройка"""
    data = await state.get_data()
    provider = data["kanban_provider"]
    
    parts = message.text.strip().split(":")
    if len(parts) < 2:
        await message.answer(
            "❌ Неправильный формат.\n"
            "Используйте: TOKEN:BOARD_ID"
        )
        return
    
    token = parts[0]
    board_id = parts[1]
    
    # Проверяем подключение
    if provider == "yougile":
        client = YouGileClient(token, board_id)
    else:
        client = TrelloClient(token, board_id)
    
    try:
        columns = await client.get_columns()
        await message.answer(f"✅ Подключение успешно! Найдено колонок: {len(columns)}")
    except Exception as e:
        await message.answer(f"❌ Ошибка подключения: {e}")
        return
    
    # Сохраняем в БД
    team = await get_team_by_chat(message.chat.id)
    if team:
        await update_team_kanban(team.id, provider, token, board_id)
    
    await state.clear()
    
    await message.answer(
        f"🎉 <b>Канбан-доска подключена!</b>\n\n"
        f"Провайдер: {provider}\n"
        f"ID доски: {board_id}\n\n"
        f"Теперь бот будет автоматически:\n"
        f"• Создавать карточки из задач в чате\n"
        f"• Назначать ответственных\n"
        f"• Обновлять статусы"
    )


@router.callback_query(F.data == "kanban:board")
async def cb_kanban_board(callback: CallbackQuery):
    """Показать текущую канбан-доску"""
    team = await get_team_by_chat(callback.message.chat.id)
    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return
    
    if team.kanban_provider == "yougile":
        client = YouGileClient(team.kanban_token, team.kanban_board_id)
    else:
        client = TrelloClient(team.kanban_token, team.kanban_board_id)
    
    columns = await client.get_columns()
    cards_by_column = {}
    
    for col in columns:
        cards = await client.get_cards_in_column(col["id"])
        cards_by_column[col["name"]] = cards
    
    text = "📊 <b>Канбан-доска</b>\n\n"
    for col_name, cards in cards_by_column.items():
        text += f"<b>{col_name}</b> ({len(cards)}):\n"
        for card in cards[:5]:
            text += f"  • {card['title'][:40]}\n"
        if len(cards) > 5:
            text += f"  ... и {len(cards) - 5} ещё\n"
        text += "\n"
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔄 Обновить", callback_data="kanban:board"))
    kb.row(InlineKeyboardButton(text="➕ Добавить задачу", callback_data="kanban:add"))
    
    await callback.message.edit_text(text[:4000], reply_markup=kb.as_markup())
    await callback.answer()


@router.message(Command("kanban_login"))
async def cmd_kanban_login(message: Message, state: FSMContext):
    await state.set_state(KanbanAuthStates.waiting_login)
    await message.answer(
        "Введи логин от аккаунта YouGile:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True,
        ),
    )


@router.message(KanbanAuthStates.waiting_login)
async def process_login(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Отменено.", reply_markup=ReplyKeyboardRemove()
        )
        return
    await state.update_data(login=message.text)
    await state.set_state(KanbanAuthStates.waiting_password)
    await message.answer("Введи пароль:")


@router.message(KanbanAuthStates.waiting_password)
async def process_password(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Отменено.", reply_markup=ReplyKeyboardRemove()
        )
        return
    try:
        await message.delete()
    except Exception:
        pass
    await state.update_data(password=message.text)

    data = await state.get_data()
    login = data["login"]
    password = data["password"]

    await state.clear()
    await message.answer(".", reply_markup=ReplyKeyboardRemove())
    wait_msg = await message.answer("⏳ Получаю токен...")

    try:
        client = YouGileClient(api_token="", board_id="")
        token = await client.generate_token(login, password, "")
        async with get_session() as session:
            await update_team_kanban(
                session, message.chat.id, token
            )
        await wait_msg.edit_text(
            f"✅ Авторизация успешна!\n"
            f"Токен: <code>{token}</code>\n\n"
            f"Теперь укажи ID доски командой /kanban_board"
        )
    except ValueError as e:
        await wait_msg.edit_text(f"❌ {e}")
    except RuntimeError as e:
        await wait_msg.edit_text(
            f"⚠️ Что-то пошло не так, попробуй позже.\n{e}"
        )


@router.message(Command("kanban_board"))
async def cmd_kanban_board(message: Message, state: FSMContext):
    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if not team or not team.kanban_token:
            await message.answer("❌ Сначала выполни /kanban_login")
            return
        client = YouGileClient(team.kanban_token, board_id="")
        boards = await client.get_boards()
        if not boards:
            await message.answer("❌ Досок не найдено")
            return
        text = "Выбери доску (введи номер):\n"
        for i, b in enumerate(boards, 1):
            text += f"{i}. {b['title']} (id: {b['id']})\n"
        await state.update_data(boards=boards)
        await state.set_state(KanbanAuthStates.waiting_for_board)
        await message.answer(text)


@router.message(KanbanAuthStates.waiting_for_board)
async def process_board(message: Message, state: FSMContext):
    data = await state.get_data()
    boards = data.get("boards", [])
    try:
        idx = int(message.text.strip()) - 1
        board = boards[idx]
    except (ValueError, IndexError):
        await message.answer("❌ Введи номер из списка")
        return
    async with get_session() as session:
        await update_team_kanban(
            session,
            message.chat.id,
            (await get_team_by_chat(session, message.chat.id)).kanban_token,
            board["id"],
            "yougile",
        )
    await state.clear()
    await message.answer(f"✅ Доска '{board['title']}' сохранена")