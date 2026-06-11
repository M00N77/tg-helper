"""Интеграция с YouGile канбан-доской."""
from datetime import datetime
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

from src.bot.filters import is_team_owner
from src.bot.states import KanbanStates, KanbanAuthStates, KanbanCardStates

from src.bot.handlers.yougile import YouGileClient, _parse_deadline
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.session import get_session
from src.db.repo import (
    set_active_board, update_team_kanban, get_team_by_chat,
    get_team_members, get_or_create_user, set_team_member_yougile_id,
)
from src.db.models import TeamMember
from src.userbot.manager import UserbotManager


router = Router(name="kanban")


async def build_board_text(client: YouGileClient, board_title: str) -> str:
    try:
        columns = await client.get_columns()
    except Exception as e:
        return f"❌ Не удалось получить колонки: {e}"

    text = f"📊 <b>{board_title}</b>\n\n"
    for col in columns:
        try:
            cards = await client.get_cards_in_column(col["id"])
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


def format_card_preview(task: dict, column_title: str, users_dict: dict[str, str] | None = None) -> str:
    title = task.get("title", "?")
    description = task.get("description", "")
    assigned = task.get("assigned", [])
    deadline_raw = task.get("deadline")

    assignee_str = "не назначен"
    if assigned and users_dict:
        names = [users_dict.get(uid, uid[:8]) for uid in assigned]
        assignee_str = ", ".join(names)

    deadline_str = "не установлен"
    if deadline_raw and isinstance(deadline_raw, dict) and deadline_raw.get("deadline"):
        dt = datetime.fromtimestamp(deadline_raw["deadline"] / 1000)
        deadline_str = dt.strftime("%d.%m.%Y")
    elif deadline_raw and isinstance(deadline_raw, (int, float)):
        dt = datetime.fromtimestamp(deadline_raw / 1000)
        deadline_str = dt.strftime("%d.%m.%Y")

    lines = [f"📋 <b>{title}</b>"]
    if description:
        lines.append(f"\n📝 {description}")
    lines.append(f"\n📂 Колонка: {column_title}")
    lines.append(f"👤 Исполнитель: {assignee_str}")
    lines.append(f"📅 Дедлайн: {deadline_str}")
    return "\n".join(lines)


@router.message(Command("kanban"))
async def cmd_kanban(message: Message):
    """Управление канбан-доской"""
    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
    
    is_owner = await is_team_owner(message)
    
    kb = InlineKeyboardBuilder()
    if not team or not team.kanban_token:
        if is_owner:
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
        if is_owner:
            kb.row(InlineKeyboardButton(text="⚙ Настройки", callback_data="kanban:settings"))
    
    await message.answer(
        "📊 <b>Канбан-доска</b>\n\n"
        "Бот автоматически:\n"
        "✅ Создаёт карточки из задач в чате\n"
        "✅ Назначает ответственных\n"
        "✅ Отслеживает дедлайны\n"
        "✅ Перемещает задачи по статусам\n\n"
        "Поддерживается: YouGile",
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data == "kanban:setup")
async def cb_kanban_setup(callback: CallbackQuery, state: FSMContext):
    """Настройка подключения к канбан-доске"""
    if not await is_team_owner(callback):
        await callback.answer("⛔ Только владелец команды может подключать доску", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="📦 YouGile", callback_data="kanban:provider:yougile"),
    )
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data="kanban:back"))
    kb.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="goto:main:confirm"))
    
    await callback.message.edit_text(
        "🔌 <b>Выберите канбан-доску</b>\n\n"
        "YouGile — российский сервис",
        reply_markup=kb.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("kanban:provider:"))
async def cb_kanban_provider(callback: CallbackQuery, state: FSMContext):
    """Выбор провайдера и ввод токена"""
    if not await is_team_owner(callback):
        await callback.answer("⛔ Только владелец команды может подключать доску", show_alert=True)
        return
    provider = callback.data.split(":")[2]
    
    await state.update_data(kanban_provider=provider)
    await state.set_state(KanbanStates.waiting_token)

    instructions = (
        "1. Перейдите в https://yougile.com\n"
        "2. Создайте доску или откройте существующую\n"
        "3. Настройки → API → Создать токен\n"
        "4. Скопируйте токен"
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
    if not await is_team_owner(message):
        await message.answer("⛔ Только владелец команды может подключать доску")
        await state.clear()
        return
    data = await state.get_data()
    provider = data["kanban_provider"]
    
    try:
        await message.delete()
    except Exception:
        pass

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
    client = YouGileClient(token, board_id)
    
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


@router.message(Command("kanban_login"), F.chat.type == "private")
async def cmd_kanban_login(message: Message, state: FSMContext):
    if not await is_team_owner(message):
        await message.answer("⛔ Только владелец команды может менять настройки доски")
        return
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
    if not await is_team_owner(message):
        await message.answer("⛔ Только владелец команды может менять настройки доски")
        await state.clear()
        return
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
    if not await is_team_owner(message):
        await message.answer("⛔ Только владелец команды может менять настройки доски")
        await state.clear()
        return
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


@router.message(Command("kanban_board"), F.chat.type == "private")
async def cmd_kanban_board(message: Message, state: FSMContext):
    if not await is_team_owner(message):
        await message.answer("⛔ Только владелец команды может менять доску")
        return

    args = message.text.split(maxsplit=1)

    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)

    if not team or not team.kanban_token:
        await message.answer("❌ Сначала выполни /kanban_login")
        return

    # Fallback: аргумент передан явно — сохраняем как активную доску
    if len(args) > 1:
        board_id = args[1].strip()
        client = YouGileClient(team.kanban_token, board_id)
        try:
            boards = await client.get_boards()
        except Exception:
            boards = []
        board_name = next((b["title"] for b in boards if b["id"] == board_id), board_id)
        async with get_session() as session:
            await set_active_board(session, message.chat.id, board_id, board_name)
        await message.answer(
            f"✅ Активная доска: <b>{board_name}</b>\n"
            "Все новые задачи будут создаваться сюда."
        )
        return

    # Нет аргумента — получаем список досок
    client = YouGileClient(team.kanban_token, board_id="")
    try:
        boards = await client.get_boards()
    except Exception as e:
        await message.answer(f"❌ Ошибка при получении досок: {e}")
        return

    if not boards:
        await message.answer("❌ Досок не найдено в YouGile")
        return

    # Одна доска — выбираем сразу
    if len(boards) == 1:
        b = boards[0]
        async with get_session() as session:
            await set_active_board(session, message.chat.id, b["id"], b["title"])
        await message.answer(
            f"✅ Активная доска: <b>{b['title']}</b>\n"
            "Все новые задачи будут создаваться сюда."
        )
        return

    # 2–8 досок — inline-кнопки
    if len(boards) <= 8:
        await state.update_data(boards=[(b["id"], b["title"]) for b in boards])
        kb = InlineKeyboardBuilder()
        for i, b in enumerate(boards):
            kb.row(InlineKeyboardButton(
                text=b["title"],
                callback_data=f"sb:{i}",
            ))
        await message.answer(
            "📋 Выбери активную доску:",
            reply_markup=kb.as_markup(),
        )
        return

    # >8 досок — нумерованный список
    text = "📋 <b>Выбери активную доску</b> (введи номер):\n\n"
    for i, b in enumerate(boards, 1):
        text += f"{i}. {b['title']}\n"
    await state.update_data(boards=[(b["id"], b["title"]) for b in boards])
    await state.set_state(KanbanAuthStates.waiting_for_board)
    await message.answer(text)


@router.callback_query(F.data.startswith("sb:"))
async def cb_set_board(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    if not await is_team_owner(callback):
        await callback.answer("⛔ Только владелец команды может менять доску", show_alert=True)
        return
    idx = int(callback.data.split(":")[1])

    data = await state.get_data()
    boards = data.get("boards", [])
    if idx < 0 or idx >= len(boards):
        await callback.answer("Ошибка: доска не найдена", show_alert=True)
        return
    board_id, board_name = boards[idx]

    team = await get_team_by_chat(session, callback.message.chat.id)

    await set_active_board(session, callback.message.chat.id, board_id, board_name)

    pending_tasks = data.get("pending_tasks")

    if pending_tasks:
        c = YouGileClient(team.kanban_token, board_id)
        cols = await c.get_columns()
        first_col_id = cols[0]["id"] if cols else None
        created = 0
        if first_col_id:
            for task in pending_tasks:
                title = (task.get("title") or "").strip()
                if not title:
                    continue
                try:
                    deadline_raw = task.get("deadline") or ""
                    deadline = deadline_raw[:10] if deadline_raw else None
                    await c.create_card(title, "", first_col_id, deadline=deadline)
                    created += 1
                except Exception:
                    pass
        await state.update_data(pending_tasks=None)
        new_text = callback.message.html_text + f"\n\n✅ Задачи созданы на доске «{board_name}»: {created} шт."
        await callback.message.edit_text(new_text, parse_mode="HTML")
    else:
        await callback.message.edit_text(
            f"✅ Активная доска: <b>{board_name}</b>\n"
            "Все новые задачи будут создаваться сюда."
        )
    await callback.answer()


@router.message(KanbanAuthStates.waiting_for_board)
async def process_board(message: Message, state: FSMContext):
    if not await is_team_owner(message):
        await message.answer("⛔ Только владелец команды может менять доску")
        await state.clear()
        return
    data = await state.get_data()
    boards = data.get("boards", [])
    try:
        idx = int(message.text.strip()) - 1
        board_id, board_name = boards[idx]
    except (ValueError, IndexError):
        await message.answer("❌ Введи номер из списка")
        return

    await state.clear()

    async with get_session() as session:
        await set_active_board(session, message.chat.id, board_id, board_name)

    await message.answer(
        f"✅ Активная доска: <b>{board_name}</b>\n"
        "Все новые задачи будут создаваться сюда."
    )


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


@router.callback_query(F.data == "goto:main:confirm")
async def cb_goto_main_confirm(callback: CallbackQuery) -> None:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Да, в главное меню", callback_data="goto:main:yes"),
        InlineKeyboardButton(text="❌ Остаться", callback_data="goto:main:no"),
    )
    await callback.message.edit_text(
        "🏠 Перейти в главное меню?\nТекущий экран закроется.",
        reply_markup=kb.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data == "goto:main:yes")
async def cb_goto_main_yes(callback: CallbackQuery, userbot_manager: UserbotManager) -> None:
    from src.bot.handlers.menu import cmd_menu
    from aiogram.types import Message
    await callback.answer()
    await cmd_menu(callback.message, userbot_manager)

@router.callback_query(F.data == "goto:main:no")
async def cb_goto_main_no(callback: CallbackQuery) -> None:
    await callback.answer("Остаёмся здесь")


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
    kb.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="goto:main:confirm"))

    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


# ── kanban:settings — настройки ────────────────────────────────────────────


@router.callback_query(F.data == "kanban:settings")
async def cb_kanban_settings(callback: CallbackQuery):
    if not await is_team_owner(callback):
        await callback.answer("⛔ Только владелец команды может менять настройки доски", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔄 Сменить доску", callback_data="kanban:change_board"))
    kb.row(InlineKeyboardButton(text="🔑 Сменить аккаунт", callback_data="kanban:relogin"))
    kb.row(InlineKeyboardButton(text="❌ Отключить", callback_data="kanban:disconnect"))
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data="kanban:back_to_menu"))
    kb.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="goto:main:confirm"))

    await callback.message.edit_text(
        "⚙ <b>Настройки канбан</b>\n\nВыбери действие:",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "kanban:change_board")
async def cb_kanban_change_board(callback: CallbackQuery, state: FSMContext):
    if not await is_team_owner(callback):
        await callback.answer("⛔ Только владелец команды может менять доску", show_alert=True)
        return
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
            "❌ Ошибка: В вашей компании не найдено досок "
            "или у токена нет прав."
        )
        await callback.answer()
        return

    kb = InlineKeyboardBuilder()
    for i, b in enumerate(boards):
        kb.row(InlineKeyboardButton(text=b["title"], callback_data=f"kanban:choose:{i}"))
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data="kanban:settings"))
    kb.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="goto:main:confirm"))

    await state.update_data(boards=boards)

    await callback.message.edit_text("Выбери доску:", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("kanban:choose:"))
async def cb_kanban_choose_board(callback: CallbackQuery, state: FSMContext):
    if not await is_team_owner(callback):
        await callback.answer("⛔ Только владелец команды может менять доску", show_alert=True)
        return
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
    if not await is_team_owner(callback):
        await callback.answer("⛔ Только владелец команды может менять настройки доски", show_alert=True)
        return
    async with get_session() as session:
        await update_team_kanban(session, callback.message.chat.id, "", "", "")

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data="kanban:back_to_menu"))
    kb.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="goto:main:confirm"))

    await callback.message.edit_text(
        "🔑 Токен сброшен. Используй /kanban_login для повторной авторизации.",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "kanban:disconnect")
async def cb_kanban_disconnect(callback: CallbackQuery):
    if not await is_team_owner(callback):
        await callback.answer("⛔ Только владелец команды может отключать доску", show_alert=True)
        return
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
    if not await is_team_owner(callback):
        await callback.answer("⛔ Только владелец команды может отключать доску", show_alert=True)
        return
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

    is_owner = await is_team_owner(callback)

    kb = InlineKeyboardBuilder()
    if not team or not team.kanban_token:
        if is_owner:
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
        if is_owner:
            kb.row(InlineKeyboardButton(text="⚙ Настройки", callback_data="kanban:settings"))

    await callback.message.edit_text(
        "📊 <b>Канбан-доска</b>\n\n"
        "Бот автоматически:\n"
        "✅ Создаёт карточки из задач в чате\n"
        "✅ Назначает ответственных\n"
        "✅ Отслеживает дедлайны\n"
        "✅ Перемещает задачи по статусам\n\n"
        "Поддерживается: YouGile",
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
        kb.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="goto:main:confirm"))
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
    kb.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="goto:main:confirm"))

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
        users_raw = await client.get_users()
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при получении задачи: {e}")
        await callback.answer()
        return

    users_dict = {u["id"]: u.get("name", u["id"][:8]) for u in users_raw}
    column_id = task.get("columnId", "")
    col_name = next((c.get("title", "?") for c in columns if c["id"] == column_id), "?")

    boards = await client.get_boards()
    board_title = next((b["title"] for b in boards if b["id"] == team.kanban_board_id), "Канбан-доска")
    from src.bot.handlers.menu import breadcrumb

    text = breadcrumb("📊", board_title, col_name, task.get("title", "?")[:20])
    text += format_card_preview(task, col_name, users_dict)

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"kanban:rename:{task_id}"),
        InlineKeyboardButton(text="📝 Описание", callback_data=f"kanban:edit_desc:{task_id}"),
    )
    kb.row(
        InlineKeyboardButton(text="👤 Назначить", callback_data=f"kanban:assign:{task_id}"),
        InlineKeyboardButton(text="📅 Дедлайн", callback_data=f"kanban:deadline:{task_id}"),
    )
    kb.row(
        InlineKeyboardButton(text="➡️ Переместить", callback_data=f"kanban:move:{task_id}"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"kanban:delete:{task_id}"),
    )
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data=f"kanban:tasks:{column_id}"))
    kb.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="goto:main:confirm"))

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
async def cb_kanban_move(callback: CallbackQuery, state: FSMContext):
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

    await state.update_data(
        kanban_task_id=task_id,
        kanban_token=team.kanban_token,
        kanban_board_id=team.kanban_board_id,
    )
    await state.set_state(KanbanCardStates.moving_task)

    current_column_id = task.get("columnId", "")
    kb = InlineKeyboardBuilder()
    for col in columns:
        if col["id"] == current_column_id:
            continue
        kb.row(InlineKeyboardButton(
            text=f"➡️ {col.get('title', '?')}",
            callback_data=f"kanban:mvcol:{col['id']}"
        ))
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data=f"kanban:task:{task_id}"))
    kb.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="goto:main:confirm"))

    await callback.message.edit_text("Выбери новую колонку:", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("kanban:mvcol:"))
async def cb_kanban_mvcol(callback: CallbackQuery, state: FSMContext):
    column_id = callback.data.split(":", 2)[2]

    data = await state.get_data()
    task_id = data.get("kanban_task_id")
    token = data.get("kanban_token")
    board_id = data.get("kanban_board_id")
    if not task_id or not token or not board_id:
        await callback.answer("❌ Данные утеряны, открой задачу заново", show_alert=True)
        return

    await state.clear()

    client = YouGileClient(token, board_id)

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


# ── kanban:assign — назначение исполнителя ──────────────────────────────────


@router.callback_query(F.data.startswith("kanban:assign:"))
async def cb_kanban_assign(callback: CallbackQuery):
    task_id = callback.data.split(":", 2)[2]
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    async with get_session() as session:
        members = await get_team_members(session, team.id)
    if not members:
        await callback.message.edit_text(
            "👤 <b>Назначение исполнителя</b>\n\n"
            "В команде нет участников. Используй /team invite",
        )
        await callback.answer()
        return

    async with get_session() as session:
        member_list = []
        for m in members:
            user = await get_or_create_user(session, m.telegram_id)
            name = user.display_name or str(m.telegram_id)
            member_list.append((m.telegram_id, name, m.role, m.yougile_user_id))

    kb = InlineKeyboardBuilder()
    for tg_id, name, role, yg_id in member_list:
        icon = "👑 " if role == "admin" else ""
        label = f"{icon}{name[:20]}"
        if yg_id:
            label += " ✅"
        kb.row(InlineKeyboardButton(
            text=label,
            callback_data=f"kanban:assign_to:{task_id}:{tg_id}",
        ))
    kb.row(InlineKeyboardButton(
        text="❌ Снять назначение",
        callback_data=f"kanban:assign_to:{task_id}:none",
    ))
    kb.row(InlineKeyboardButton(
        text="◀ Назад", callback_data=f"kanban:task:{task_id}",
    ))

    await callback.message.edit_text(
        "👤 <b>Назначить исполнителя</b>\n\n"
        "Выбери участника команды:"
        "\n✅ — привязан к YouGile",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("kanban:assign_to:"))
async def cb_kanban_assign_to(callback: CallbackQuery):
    parts = callback.data.split(":")
    task_id = parts[2]
    tg_raw = parts[3]

    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)

    try:
        if tg_raw == "none":
            await client.update_card(task_id, assigned=[])
            assignee_name = "не назначен"
        else:
            tg_id = int(tg_raw)

            async with get_session() as session:
                user = await get_or_create_user(session, tg_id)
                assignee_name = user.display_name or str(tg_id)

                result = await session.execute(
                    select(TeamMember).where(
                        TeamMember.team_id == team.id,
                        TeamMember.telegram_id == tg_id,
                    )
                )
                tm = result.scalar_one_or_none()

            if tm and tm.yougile_user_id:
                yg_user_id = tm.yougile_user_id
            else:
                yg_user_id = await client.resolve_user_by_name(assignee_name)
                if not yg_user_id:
                    await callback.message.edit_text(
                        f"❌ Участник «{assignee_name}» не привязан к YouGile.\n"
                        "Администратор может привязать: нажми кнопку ниже.",
                    )
                    kb = InlineKeyboardBuilder()
                    kb.row(InlineKeyboardButton(
                        text="🔗 Привязать к YouGile",
                        callback_data=f"kanban:link_user:{task_id}:{tg_id}",
                    ))
                    kb.row(InlineKeyboardButton(
                        text="◀ Назад", callback_data=f"kanban:assign:{task_id}",
                    ))
                    await callback.message.edit_text(
                        "⚠️ <b>Участник не привязан к YouGile</b>\n\n"
                        f"Пользователь «{assignee_name}» не найден в проекте YouGile. "
                        "Нажми «Привязать» и выбери его из списка участников YouGile.",
                        reply_markup=kb.as_markup(),
                    )
                    await callback.answer()
                    return

            await client.update_card(task_id, assigned=[yg_user_id])

    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при назначении: {e}")
        await callback.answer()
        return

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="◀ К задаче", callback_data=f"kanban:task:{task_id}",
    ))
    await callback.message.edit_text(
        f"✅ Исполнитель назначен: {assignee_name}",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("kanban:link_user:"))
async def cb_kanban_link_user(callback: CallbackQuery):
    parts = callback.data.split(":")
    task_id = parts[2]
    tg_id = int(parts[3])

    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)
    try:
        yg_users = await client.get_users()
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при получении списка YouGile: {e}")
        await callback.answer()
        return

    if not yg_users:
        await callback.message.edit_text(
            "❌ В проекте YouGile нет пользователей.\n"
            "Сначала добавь участников в YouGile.",
        )
        await callback.answer()
        return

    kb = InlineKeyboardBuilder()
    for u in yg_users:
        uid = u["id"]
        uname = u.get("name", uid[:8])
        kb.row(InlineKeyboardButton(
            text=uname[:25],
            callback_data=f"kanban:link_confirm:{task_id}:{tg_id}:{uid}",
        ))
    kb.row(InlineKeyboardButton(
        text="◀ Назад", callback_data=f"kanban:assign:{task_id}",
    ))

    await callback.message.edit_text(
        "🔗 <b>Привязка к YouGile</b>\n\n"
        "Выбери пользователя YouGile, которому соответствует этот участник:",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("kanban:link_confirm:"))
async def cb_kanban_link_confirm(callback: CallbackQuery):
    parts = callback.data.split(":")
    task_id = parts[2]
    tg_id = int(parts[3])
    yg_user_id = parts[4]

    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
        if team:
            await set_team_member_yougile_id(session, team.id, tg_id, yg_user_id)

    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="✅ Назначить эту задачу",
        callback_data=f"kanban:assign_to:{task_id}:{tg_id}",
    ))
    kb.row(InlineKeyboardButton(
        text="◀ К задаче", callback_data=f"kanban:task:{task_id}",
    ))

    await callback.message.edit_text(
        "✅ Участник привязан к YouGile!",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


# ── kanban:deadline — изменение дедлайна ────────────────────────────────────


@router.callback_query(F.data.startswith("kanban:deadline:"))
async def cb_kanban_deadline(callback: CallbackQuery, state: FSMContext):
    task_id = callback.data.split(":", 2)[2]
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    await state.update_data(
        kanban_task_id=task_id,
        kanban_token=team.kanban_token,
        kanban_board_id=team.kanban_board_id,
    )
    await state.set_state(KanbanCardStates.setting_deadline)
    await callback.message.answer(
        "📅 <b>Введи дедлайн</b>\n\n"
        "Формат: ДД.ММ.ГГГГ (например 25.12.2026)\n"
        "Или «-» чтобы убрать дедлайн.\n\n"
        "Отмена — /cancel",
    )
    await callback.answer()


@router.message(KanbanCardStates.setting_deadline)
async def process_deadline(message: Message, state: FSMContext):
    text = message.text.strip()
    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    task_id = data.get("kanban_task_id")
    token = data.get("kanban_token")
    board_id = data.get("kanban_board_id")

    if not task_id or not token or not board_id:
        await state.clear()
        await message.answer("❌ Ошибка: данные задачи утеряны. Начни заново.")
        return

    client = YouGileClient(token, board_id)

    if text == "-":
        try:
            await client.update_card(task_id, deadline={"deadline": None, "withTime": False})
        except Exception as e:
            await message.answer(f"❌ Ошибка при удалении дедлайна: {e}")
            await state.clear()
            return
        await state.clear()
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(
            text="◀ К задаче", callback_data=f"kanban:task:{task_id}",
        ))
        await message.answer("✅ Дедлайн убран", reply_markup=kb.as_markup())
        return

    try:
        dt = datetime.strptime(text, "%d.%m.%Y")
    except ValueError:
        await message.answer(
            "❌ Неверный формат. Используй ДД.ММ.ГГГГ, например 25.12.2026",
        )
        return

    try:
        iso_str = dt.strftime("%Y-%m-%d")
        deadline_data = _parse_deadline(iso_str)
        await client.update_card(task_id, deadline=deadline_data)
    except Exception as e:
        await message.answer(f"❌ Ошибка при установке дедлайна: {e}")
        await state.clear()
        return

    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="◀ К задаче", callback_data=f"kanban:task:{task_id}",
    ))
    await message.answer(
        f"✅ Дедлайн установлен: {text}",
        reply_markup=kb.as_markup(),
    )