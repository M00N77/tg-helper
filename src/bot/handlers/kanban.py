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

from src.bot.states import KanbanStates, KanbanAuthStates, KanbanCardStates

from src.bot.handlers.yougile import YouGileClient
from src.db.session import get_session
from src.db.repo import update_team_kanban, get_team_by_chat


router = Router(name="kanban")


async def build_board_text(client: YouGileClient, board_title: str) -> str:
    try:
        columns = await client.get_columns()
    except Exception as e:
        return f"❌ Не удалось получить колонки: {e}"

    text = f"📊 <b>{board_title}</b>\n\n"
    for col in columns:
        try:
            cards = await client.get_cards_in_column(col["id"], limit=5)
        except Exception as e:
            text += f"<b>{col.get('title', '?')}</b> (ошибка: {e})\n\n"
            continue
        col_name = col.get("title", "?")
        text += f"<b>{col_name}</b> ({len(cards)}):\n"
        for card in cards[:5]:
            title = card.get("title", "?")[:40]
            text += f"  • {title}\n"
        if len(cards) > 5:
            text += f"  ... и {len(cards) - 5} ещё\n"
        text += "\n"
    return text[:4000]


@router.message(Command("kanban"))
async def cmd_kanban(message: Message):
    """Управление канбан-доской"""
    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
    
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
    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if team:
            await update_team_kanban(session, message.chat.id, token, board_id, provider)
    
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
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)
    text = await build_board_text(client, "Канбан-доска")

    kb = InlineKeyboardBuilder()
    try:
        columns = await client.get_columns()
        for col in columns:
            kb.row(InlineKeyboardButton(
                text=f"📋 {col.get('title', '?')}",
                callback_data=f"kanban:tasks:{col['id']}"
            ))
    except Exception:
        pass
    kb.row(InlineKeyboardButton(text="🔄 Обновить", callback_data="kanban:board"))
    kb.row(InlineKeyboardButton(text="➕ Добавить задачу", callback_data="kanban:add"))

    await callback.message.edit_text(text, reply_markup=kb.as_markup())
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
        try:
            boards = await client.get_boards()
        except Exception as e:
            await message.answer(f"❌ Ошибка при получении досок: {e}")
            return
        if not boards:
            await message.answer(
                "✅ Авторизация прошла успешно, но в этой компании "
                "не найдено ни одной доски.\n"
                "Создай доску в YouGile и попробуй снова."
            )
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

    await state.clear()

    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        token = team.kanban_token
        await update_team_kanban(
            session,
            message.chat.id,
            token,
            board["id"],
            "yougile",
        )

    client = YouGileClient(token, board["id"])
    text = await build_board_text(client, board["title"])

    kb = InlineKeyboardBuilder()
    try:
        columns = await client.get_columns()
        for col in columns:
            kb.row(InlineKeyboardButton(
                text=f"📋 {col.get('title', '?')}",
                callback_data=f"kanban:tasks:{col['id']}"
            ))
    except Exception:
        pass
    kb.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data="kanban:board"),
        InlineKeyboardButton(text="➕ Задача", callback_data="kanban:add"),
    )
    await message.answer(text, reply_markup=kb.as_markup())


# ── kanban:add — создание задачи (FSM) ─────────────────────────────────────


@router.callback_query(F.data == "kanban:add")
async def cb_kanban_add(callback: CallbackQuery, state: FSMContext):
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token or not team.kanban_board_id:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return
    await state.update_data(
        kanban_token=team.kanban_token,
        kanban_board_id=team.kanban_board_id,
    )
    await state.set_state(KanbanCardStates.waiting_title)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )
    await callback.message.answer("📝 Введи название задачи:", reply_markup=kb)
    await callback.answer()


@router.message(KanbanCardStates.waiting_title)
async def process_card_title(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=ReplyKeyboardRemove())
        return
    await state.update_data(title=message.text.strip())
    await state.set_state(KanbanCardStates.waiting_description)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⏭ Пропустить"), KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )
    await message.answer("📝 Введи описание задачи (или нажми «Пропустить»):", reply_markup=kb)


@router.message(KanbanCardStates.waiting_description)
async def process_card_description(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=ReplyKeyboardRemove())
        return
    desc = "" if message.text == "⏭ Пропустить" else message.text.strip()
    await state.update_data(description=desc)

    data = await state.get_data()
    token = data["kanban_token"]
    board_id = data["kanban_board_id"]
    target_column_id = data.get("target_column_id")

    if target_column_id:
        title = data["title"]
        client = YouGileClient(token, board_id)
        try:
            await client.create_card(title, desc, target_column_id)
        except Exception as e:
            await state.clear()
            await message.answer(
                f"❌ Ошибка при создании задачи: {e}",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        await state.clear()
        await message.answer(
            f"✅ Задача создана!\n\n<b>{title}</b>",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    client = YouGileClient(token, board_id)

    try:
        columns = await client.get_columns()
    except Exception as e:
        await state.clear()
        await message.answer(
            f"❌ Ошибка при получении колонок: {e}",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await state.set_state(KanbanCardStates.waiting_column)
    kb = InlineKeyboardBuilder()
    for col in columns:
        kb.row(
            InlineKeyboardButton(
                text=col.get("title", "?"),
                callback_data=f"kanban:col:{col['id']}",
            )
        )
    kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="kanban:add:cancel"))

    await message.answer(".", reply_markup=ReplyKeyboardRemove())
    await message.answer("📌 Выбери колонку:", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("kanban:col:"))
async def cb_kanban_column(callback: CallbackQuery, state: FSMContext):
    column_id = callback.data.split(":", 2)[2]
    data = await state.get_data()
    title = data.get("title", "")
    description = data.get("description", "")
    token = data.get("kanban_token", "")
    board_id = data.get("kanban_board_id", "")

    client = YouGileClient(token, board_id)

    try:
        result = await client.create_card(title, description, column_id)
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при создании задачи: {e}")
        await callback.answer()
        return

    await state.clear()

    col_name = next(
        (c.get("title", column_id) for c in (await client.get_columns()) if c["id"] == column_id),
        column_id,
    )

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📊 Открыть доску", callback_data="kanban:board"))

    await callback.message.edit_text(
        f"✅ Задача создана!\n\n<b>{title}</b>\nКолонка: {col_name}",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "kanban:add:cancel")
async def cb_kanban_add_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Создание задачи отменено.")
    await callback.answer()


# ── kanban:sync — синхронизация ────────────────────────────────────────────


@router.callback_query(F.data == "kanban:sync")
async def cb_kanban_sync(callback: CallbackQuery):
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token or not team.kanban_board_id:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)

    try:
        text = await build_board_text(client, "Канбан-доска")
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка синхронизации: {e}")
        await callback.answer()
        return

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data="kanban:board"),
        InlineKeyboardButton(text="➕ Добавить задачу", callback_data="kanban:add"),
    )

    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


# ── kanban:stats — статистика ──────────────────────────────────────────────


@router.callback_query(F.data == "kanban:stats")
async def cb_kanban_stats(callback: CallbackQuery):
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token or not team.kanban_board_id:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)

    try:
        columns = await client.get_columns()
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при получении колонок: {e}")
        await callback.answer()
        return

    total_cards = 0
    col_stats = []
    max_col = ("", 0)

    for col in columns:
        try:
            cards = await client.get_cards_in_column(col["id"], limit=100)
        except Exception:
            cards = []
        count = len(cards)
        total_cards += count
        col_stats.append((col.get("title", "?"), count))
        if count > max_col[1]:
            max_col = (col.get("title", "?"), count)

    text = f"📈 <b>Статистика доски</b>\n\n"
    text += f"Всего колонок: {len(columns)}\n"
    text += f"Всего задач: {total_cards}\n\n"
    text += "По колонкам:\n"
    for name, count in col_stats:
        text += f"• {name} — {count} задач\n"
    if max_col[1] > 0:
        text += f"\nСамая загруженная: {max_col[0]} ({max_col[1]} задач)"

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data="kanban:board"))

    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


# ── kanban:settings — настройки ────────────────────────────────────────────


@router.callback_query(F.data == "kanban:settings")
async def cb_kanban_settings(callback: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔄 Сменить доску", callback_data="kanban:change_board"))
    kb.row(InlineKeyboardButton(text="🔑 Сменить аккаунт", callback_data="kanban:relogin"))
    kb.row(InlineKeyboardButton(text="❌ Отключить", callback_data="kanban:disconnect"))
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data="kanban:back_to_menu"))

    await callback.message.edit_text(
        "⚙ <b>Настройки канбан</b>\n\nВыбери действие:",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "kanban:change_board")
async def cb_kanban_change_board(callback: CallbackQuery, state: FSMContext):
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    client = YouGileClient(team.kanban_token, board_id="")

    try:
        boards = await client.get_boards()
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при получении досок: {e}")
        await callback.answer()
        return

    if not boards:
        await callback.message.edit_text(
            "✅ Авторизация прошла успешно, но в этой компании "
            "не найдено ни одной доски.\n"
            "Создай доску в YouGile и попробуй снова."
        )
        await callback.answer()
        return

    kb = InlineKeyboardBuilder()
    for i, b in enumerate(boards):
        kb.row(InlineKeyboardButton(text=b["title"], callback_data=f"kanban:choose:{i}"))
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data="kanban:settings"))

    await state.update_data(boards=boards)

    await callback.message.edit_text("Выбери доску:", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("kanban:choose:"))
async def cb_kanban_choose_board(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    boards = data.get("boards", [])
    try:
        idx = int(callback.data.split(":")[2])
        board = boards[idx]
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора доски", show_alert=True)
        return

    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
        token = team.kanban_token
        await update_team_kanban(session, callback.message.chat.id, token, board["id"], "yougile")

    client = YouGileClient(token, board["id"])
    text = await build_board_text(client, board["title"])

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data="kanban:board"),
        InlineKeyboardButton(text="➕ Добавить задачу", callback_data="kanban:add"),
    )

    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data == "kanban:relogin")
async def cb_kanban_relogin(callback: CallbackQuery):
    async with get_session() as session:
        await update_team_kanban(session, callback.message.chat.id, "", "", "")

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data="kanban:back_to_menu"))

    await callback.message.edit_text(
        "🔑 Токен сброшен. Используй /kanban_login для повторной авторизации.",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "kanban:disconnect")
async def cb_kanban_disconnect(callback: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Да, отключить", callback_data="kanban:disconnect:yes"),
        InlineKeyboardButton(text="❌ Нет", callback_data="kanban:settings"),
    )

    await callback.message.edit_text(
        "❓ <b>Точно отключить канбан-доску?</b>\n\n"
        "Все настройки будут сброшены.",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "kanban:disconnect:yes")
async def cb_kanban_disconnect_yes(callback: CallbackQuery):
    async with get_session() as session:
        await update_team_kanban(session, callback.message.chat.id, "", "", "")

    await callback.message.edit_text(
        "✅ Канбан-доска отключена. Используй /kanban для повторного подключения."
    )
    await callback.answer()


@router.callback_query(F.data == "kanban:back_to_menu")
async def cb_kanban_back_to_menu(callback: CallbackQuery):
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)

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

    await callback.message.edit_text(
        "📊 <b>Канбан-доска</b>\n\n"
        "Бот автоматически:\n"
        "✅ Создаёт карточки из задач в чате\n"
        "✅ Назначает ответственных\n"
        "✅ Отслеживает дедлайны\n"
        "✅ Перемещает задачи по статусам\n\n"
        "Поддерживаются: YouGile, Trello",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "kanban:back")
async def cb_kanban_back(callback: CallbackQuery):
    await cb_kanban_back_to_menu(callback)


# ── kanban:tasks — список задач колонки ──────────────────────────────────────


@router.callback_query(F.data.startswith("kanban:tasks:"))
async def cb_kanban_tasks(callback: CallbackQuery):
    parts = callback.data.split(":")
    column_id = parts[2]
    page = int(parts[3]) if len(parts) > 3 else 0
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)

    try:
        page_size = 10
        all_cards = await client.get_cards_in_column(column_id, limit=100)
        cards = all_cards[page * page_size:(page + 1) * page_size]
        total_pages = (len(all_cards) + page_size - 1) // page_size
        columns = await client.get_columns()
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при получении задач: {e}")
        await callback.answer()
        return

    col_name = next((c.get("title", "?") for c in columns if c["id"] == column_id), "?")

    boards = await client.get_boards()
    board_title = next((b["title"] for b in boards if b["id"] == team.kanban_board_id), "Канбан-доска")
    from src.bot.handlers.menu import breadcrumb

    if not cards:
        text = breadcrumb("📊", board_title, col_name) + f"📂 <b>{col_name}</b>\n\nКолонка пуста. Добавить задачу?"
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="➕ Добавить в эту колонку", callback_data=f"kanban:add_to:{column_id}"))
        kb.row(InlineKeyboardButton(text="◀ Назад к доске", callback_data="kanban:board"))
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()
        return

    text = breadcrumb("📊", board_title, col_name) + f"📂 <b>{col_name}</b> ({len(cards)}):\n\n"
    kb = InlineKeyboardBuilder()
    for card in cards:
        title = card.get("title", "?")[:30]
        kb.row(InlineKeyboardButton(
            text=f"📋 {title}",
            callback_data=f"kanban:task:{card['id']}"
        ))
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀", callback_data=f"kanban:tasks:{column_id}:{page-1}"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(text="▶", callback_data=f"kanban:tasks:{column_id}:{page+1}"))
    if nav:
        kb.row(*nav)
    kb.row(InlineKeyboardButton(text="➕ Добавить в эту колонку", callback_data=f"kanban:add_to:{column_id}"))
    kb.row(InlineKeyboardButton(text="◀ Назад к доске", callback_data="kanban:board"))

    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


# ── kanban:task — детальная карточка задачи ──────────────────────────────────


@router.callback_query(F.data.startswith("kanban:task:"))
async def cb_kanban_task(callback: CallbackQuery):
    task_id = callback.data.split(":", 2)[2]
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)

    try:
        task = await client.get_task(task_id)
        columns = await client.get_columns()
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при получении задачи: {e}")
        await callback.answer()
        return

    title = task.get("title", "?")
    description = task.get("description", "")
    column_id = task.get("columnId", "")
    col_name = next((c.get("title", "?") for c in columns if c["id"] == column_id), "?")

    boards = await client.get_boards()
    board_title = next((b["title"] for b in boards if b["id"] == team.kanban_board_id), "Канбан-доска")
    from src.bot.handlers.menu import breadcrumb

    text = breadcrumb("📊", board_title, col_name, title[:20]) + f"📋 <b>{title}</b>\n"
    if description:
        text += f"\n📝 {description}\n"
    text += f"\n📂 Колонка: {col_name}"

    kb = InlineKeyboardBuilder()
    for col in columns:
        if col["id"] == column_id:
            continue
        kb.row(InlineKeyboardButton(
            text=f"➡️ {col.get('title', '?')}",
            callback_data=f"kanban:move_to:{task_id}:{col['id']}"
        ))
    kb.row(
        InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"kanban:rename:{task_id}"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"kanban:delete:{task_id}"),
    )
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data=f"kanban:tasks:{column_id}"))

    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


# ── kanban:rename — FSM переименования ──────────────────────────────────────


@router.callback_query(F.data.startswith("kanban:rename:"))
async def cb_kanban_rename(callback: CallbackQuery, state: FSMContext):
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return
    task_id = callback.data.split(":", 2)[2]
    await state.update_data(
        kanban_task_id=task_id,
        kanban_token=team.kanban_token,
        kanban_board_id=team.kanban_board_id,
    )
    await state.set_state(KanbanCardStates.editing_title)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )
    await callback.message.answer("✏️ Введи новое название:", reply_markup=kb)
    await callback.answer()


@router.message(KanbanCardStates.editing_title)
async def process_rename(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=ReplyKeyboardRemove())
        return
    data = await state.get_data()
    task_id = data["kanban_task_id"]
    token = data["kanban_token"]
    board_id = data["kanban_board_id"]

    client = YouGileClient(token, board_id)
    try:
        await client.update_card(task_id, title=message.text.strip())
    except Exception as e:
        await state.clear()
        await message.answer(f"❌ Ошибка при переименовании: {e}", reply_markup=ReplyKeyboardRemove())
        return

    await state.clear()
    await message.answer(
        f"✅ Название обновлено: <b>{message.text.strip()}</b>",
        reply_markup=ReplyKeyboardRemove(),
    )


# ── kanban:edit_desc — FSM изменения описания ────────────────────────────────


@router.callback_query(F.data.startswith("kanban:edit_desc:"))
async def cb_kanban_edit_desc(callback: CallbackQuery, state: FSMContext):
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return
    task_id = callback.data.split(":", 2)[2]
    await state.update_data(
        kanban_task_id=task_id,
        kanban_token=team.kanban_token,
        kanban_board_id=team.kanban_board_id,
    )
    await state.set_state(KanbanCardStates.editing_desc)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )
    await callback.message.answer(
        "📝 Введи новое описание (или «-» чтобы очистить):",
        reply_markup=kb,
    )
    await callback.answer()


@router.message(KanbanCardStates.editing_desc)
async def process_edit_desc(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=ReplyKeyboardRemove())
        return
    desc = "" if message.text == "-" else message.text.strip()
    data = await state.get_data()
    task_id = data["kanban_task_id"]
    token = data["kanban_token"]
    board_id = data["kanban_board_id"]

    client = YouGileClient(token, board_id)
    try:
        await client.update_card(task_id, description=desc)
    except Exception as e:
        await state.clear()
        await message.answer(f"❌ Ошибка при обновлении описания: {e}", reply_markup=ReplyKeyboardRemove())
        return

    await state.clear()
    await message.answer("✅ Описание обновлено.", reply_markup=ReplyKeyboardRemove())


# ── kanban:move — перемещение задачи ─────────────────────────────────────────


@router.callback_query(F.data.startswith("kanban:move:"))
async def cb_kanban_move(callback: CallbackQuery):
    task_id = callback.data.split(":", 2)[2]
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)

    try:
        task = await client.get_task(task_id)
        columns = await client.get_columns()
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {e}")
        await callback.answer()
        return

    current_column_id = task.get("columnId", "")
    kb = InlineKeyboardBuilder()
    for col in columns:
        if col["id"] == current_column_id:
            continue
        kb.row(InlineKeyboardButton(
            text=f"➡️ {col.get('title', '?')}",
            callback_data=f"kanban:move_to:{task_id}:{col['id']}"
        ))
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data=f"kanban:task:{task_id}"))

    await callback.message.edit_text("Выбери новую колонку:", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("kanban:move_to:"))
async def cb_kanban_move_to(callback: CallbackQuery):
    parts = callback.data.split(":")
    task_id = parts[2]
    column_id = parts[3]

    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)

    try:
        await client.move_card(task_id, column_id)
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при перемещении: {e}")
        await callback.answer()
        return

    columns = await client.get_columns()
    col_name = next((c.get("title", column_id) for c in columns if c["id"] == column_id), column_id)

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📊 Открыть доску", callback_data="kanban:board"))

    await callback.message.edit_text(
        f"✅ Задача перемещена в <b>{col_name}</b>",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


# ── kanban:delete — удаление задачи ──────────────────────────────────────────


@router.callback_query(F.data.startswith("kanban:delete:"))
async def cb_kanban_delete_confirm(callback: CallbackQuery):
    task_id = callback.data.split(":", 2)[2]
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"kanban:delete_ok:{task_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"kanban:task:{task_id}"),
    )
    await callback.message.edit_text(
        "🗑 <b>Удалить задачу?</b>\n\nЭто действие необратимо.",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("kanban:delete_ok:"))
async def cb_kanban_delete_ok(callback: CallbackQuery):
    task_id = callback.data.split(":", 2)[2]
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)

    try:
        await client.delete_task(task_id)
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при удалении: {e}")
        await callback.answer()
        return

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📊 Вернуться к доске", callback_data="kanban:board"))

    await callback.message.edit_text("✅ Задача удалена.", reply_markup=kb.as_markup())
    await callback.answer()


# ── kanban:add_to — быстрое добавление в колонку ─────────────────────────────


@router.callback_query(F.data.startswith("kanban:add_to:"))
async def cb_kanban_add_to(callback: CallbackQuery, state: FSMContext):
    column_id = callback.data.split(":", 2)[2]
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token or not team.kanban_board_id:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return
    await state.update_data(
        kanban_token=team.kanban_token,
        kanban_board_id=team.kanban_board_id,
        target_column_id=column_id,
    )
    await state.set_state(KanbanCardStates.waiting_title)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )
    await callback.message.answer("📝 Введи название задачи:", reply_markup=kb)
    await callback.answer()