"""Интеграция с YouGile канбан-доской."""
import logging
from datetime import datetime
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData

logger = logging.getLogger(__name__)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import get_team_for_event
from src.bot.states import KanbanAuthStates, KanbanCardStates

from src.bot.handlers.yougile import YouGileClient, _parse_deadline, get_board_id
from sqlalchemy import select
from src.config import settings
from src.db.session import get_session
from src.db.repo import (
    set_active_board, update_team_kanban,
    get_team_members, get_or_create_user, set_team_member_yougile_id,
    get_team_by_chat,
    get_user_teams, get_team_member,
)
from src.db.models import Team, TeamMember
from src.group_bot.permissions import can_manage_kanban
from src.services.crypto_service import crypto_service
from src.userbot.manager import UserbotManager


router = Router(name="kanban")


class KanbanTeamCB(CallbackData, prefix="kb_team"):
    """Callback для работы с канбан-досками команд в ЛС (строгий стейт-менеджмент)."""
    team_id: int
    action: str


async def _decrypt_kanban_token(team) -> str | None:
    """Расшифровывает токен команды (хранится зашифрованным в БД)."""
    if team is None or not team.kanban_token:
        return None
    return await crypto_service.decrypt_data(team.kanban_token, fallback_raw=True)


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


def _board_menu_markup(team_id: int, is_admin: bool) -> InlineKeyboardMarkup:
    """Клавиатура меню доски: у member — только «Мои задачи», у admin/owner — ещё настройки."""
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="📋 Мои задачи",
        callback_data=KanbanTeamCB(team_id=team_id, action="my_tasks").pack(),
    ))
    if is_admin:
        kb.row(InlineKeyboardButton(
            text="⚙️ Настройки",
            callback_data=KanbanTeamCB(team_id=team_id, action="settings").pack(),
        ))
    return kb.as_markup()


def _is_team_admin(team: Team, member: TeamMember | None, telegram_id: int) -> bool:
    """admin/owner может настраивать доску: роль в TeamMember или владелец команды."""
    return can_manage_kanban(team, member, telegram_id)


async def _resolve_dm_team(session, event: Message | CallbackQuery) -> Team | None:
    """Команда для события из ЛС: по чату → по владению → первая команда участника."""
    team = await get_team_for_event(session, event)
    if team is None and event.from_user:
        teams = await get_user_teams(session, event.from_user.id)
        if teams:
            team = teams[0]
    return team


async def _can_manage_message(message: Message, state: FSMContext | None = None) -> bool:
    """RBAC-проверка для message-хендлеров настройки доски. При отказе отвечает."""
    uid = message.from_user.id
    async with get_session() as session:
        data = await state.get_data() if state is not None else {}
        chat_id = data.get("setup_chat_id")
        team = await get_team_by_chat(session, chat_id) if chat_id else None
        if team is None:
            team = await _resolve_dm_team(session, message)
        member = await get_team_member(session, team.id, uid) if team else None
        allowed = can_manage_kanban(team, member, uid)
    if not allowed:
        await message.answer("⛔ Доступно только администраторам")
    return allowed


async def _render_board_menu(message: Message, team: Team, member: TeamMember | None) -> None:
    """Отправляет меню канбан-доски одной команды (если доска настроена)."""
    if not team.kanban_token:
        await message.answer(
            f"❌ Канбан-доска команды «{team.name or '?'}» не настроена.\n"
            "Попроси администратора выполнить /setup_yougile в групповом чате команды."
        )
        return
    is_admin = _is_team_admin(team, member, message.from_user.id)
    await message.answer(
        f"📊 <b>{team.name or 'Канбан-доска'}</b>\n\nВыбери действие:",
        reply_markup=_board_menu_markup(team.id, is_admin),
    )


@router.message(Command("kanban"), F.chat.type == "private")
async def cmd_kanban(message: Message):
    """Управление канбан-доской (ЛС): одна команда — сразу меню, несколько — выбор."""
    uid = message.from_user.id
    async with get_session() as session:
        teams = await get_user_teams(session, uid)
        if not teams:
            await message.answer("❌ У тебя нет команд. Создай или присоединись в группе")
            return
        if len(teams) == 1:
            member = await get_team_member(session, teams[0].id, uid)
            await _render_board_menu(message, teams[0], member)
            return
        kb = InlineKeyboardBuilder()
        for team in teams:
            kb.row(InlineKeyboardButton(
                text=team.name or f"Команда #{team.id}",
                callback_data=KanbanTeamCB(team_id=team.id, action="select").pack(),
            ))
        await message.answer("Выбери команду:", reply_markup=kb.as_markup())


@router.callback_query(KanbanTeamCB.filter(F.action == "select"))
async def cb_kanban_team_select(callback: CallbackQuery, callback_data: KanbanTeamCB):
    """Меню доски выбранной команды. Guard Clause защищает от IDOR."""
    uid = callback.from_user.id
    async with get_session() as session:
        member = await get_team_member(session, callback_data.team_id, uid)
        if member is None:
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        team = await session.get(Team, callback_data.team_id)
        if team is None:
            await callback.answer("Команда не найдена", show_alert=True)
            return
        if not team.kanban_token:
            await callback.message.edit_text(
                f"❌ Канбан-доска команды «{team.name or '?'}» не настроена.\n"
                "Попроси администратора выполнить /setup_yougile в групповом чате команды."
            )
            await callback.answer()
            return
        is_admin = _is_team_admin(team, member, uid)

    await callback.message.edit_text(
        f"📊 <b>{team.name or 'Канбан-доска'}</b>\n\nВыбери действие:",
        reply_markup=_board_menu_markup(callback_data.team_id, is_admin),
    )
    await callback.answer()


@router.callback_query(KanbanTeamCB.filter(F.action == "settings"))
async def cb_kanban_team_settings(callback: CallbackQuery, callback_data: KanbanTeamCB):
    """Настройки доски. Настройка выполняется админом в групповом чате команды."""
    uid = callback.from_user.id
    async with get_session() as session:
        member = await get_team_member(session, callback_data.team_id, uid)
        if member is None:
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        team = await session.get(Team, callback_data.team_id)
        if team is None:
            await callback.answer("Команда не найдена", show_alert=True)
            return
    if not _is_team_admin(team, member, uid):
        await callback.answer("Доступ запрещен", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="◀ К доске",
        callback_data=KanbanTeamCB(team_id=team.id, action="select").pack(),
    ))
    await callback.message.edit_text(
        "⚙️ <b>Настройки канбан</b>\n\n"
        "Привязка и смена доски YouGile выполняются администратором "
        "в групповом чате команды командой /setup_yougile.",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


def _format_my_tasks(tasks: list[dict]) -> str:
    """Компактный текстовый список задач пользователя из YouGile."""
    if not tasks:
        return "📭 У тебя нет задач на доске"
    lines = ["📋 <b>Мои задачи</b>\n"]
    for task in tasks[:50]:
        title = (task.get("title") or "").strip() or "(без названия)"
        line = f"• {title}"
        deadline_raw = task.get("deadline")
        if isinstance(deadline_raw, dict) and deadline_raw.get("deadline"):
            dt = datetime.fromtimestamp(deadline_raw["deadline"] / 1000)
            line += f" — до {dt.strftime('%d.%m.%Y')}"
        lines.append(line)
    if len(tasks) > 50:
        lines.append(f"\n… и ещё {len(tasks) - 50}")
    return "\n".join(lines)


@router.callback_query(KanbanTeamCB.filter(F.action == "my_tasks"))
async def cb_kanban_my_tasks(callback: CallbackQuery, callback_data: KanbanTeamCB):
    """Список задач YouGile, назначенных на пользователя. Guard Clause от IDOR."""
    uid = callback.from_user.id
    async with get_session() as session:
        member = await get_team_member(session, callback_data.team_id, uid)
        if member is None:
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        if not member.yougile_user_id:
            await callback.answer(
                "Твой Telegram не привязан к YouGile. Обратись к администратору",
                show_alert=True,
            )
            return
        team = await session.get(Team, callback_data.team_id)
        token = await _decrypt_kanban_token(team) if team else None

    if not token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    try:
        client = YouGileClient(token)
        tasks = await client.get_tasks_by_assignee(member.yougile_user_id)
    except Exception as e:
        await callback.answer(f"❌ Ошибка при получении задач: {e}", show_alert=True)
        return

    text = _format_my_tasks(tasks)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="◀ К доске",
        callback_data=KanbanTeamCB(team_id=callback_data.team_id, action="select").pack(),
    ))
    try:
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "kanban:board")
async def cb_kanban_board(callback: CallbackQuery):
    """Показать текущую канбан-доску"""
    chat_id = callback.message.chat.id if callback.message else 0
    async with get_session() as session:
        team = await get_team_for_event(session, callback)

    board_id = get_board_id(team) if team else None

    logger.info(
        "board: chat_id=%s team=%s board_id=%s",
        chat_id,
        team.id if team else None,
        board_id,
    )

    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    board_id = get_board_id(team)

    if not board_id:
        logger.warning("[DEBUG BOARD] board_id is None for chat_id=%s, trying auto-setup", chat_id)
        try:
            client = YouGileClient(await _decrypt_kanban_token(team))
            boards = await client.get_boards()
            if len(boards) == 0:
                await callback.answer("❌ В аккаунте YouGile нет досок. Создайте доску в YouGile.", show_alert=True)
                return
            if len(boards) == 1:
                board = boards[0]
                async with get_session() as session:
                    team_db = await get_team_for_event(session, callback)
                    if team_db:
                        await update_team_kanban(session, team_db.chat_id, team.kanban_token, board["id"])
                await callback.answer(f"✅ Автоматически привязана доска «{board['title']}»", show_alert=True)
                board_id = board["id"]
            else:
                await callback.answer("📋 Несколько досок. Используйте /kanban_board в ЛС бота.", show_alert=True)
                return
        except Exception as e:
            logger.exception("[DEBUG BOARD] Auto-setup failed for chat_id=%s", chat_id)
            await callback.answer(f"❌ Ошибка при получении досок: {e}", show_alert=True)
            return

    await callback.answer()

    client = YouGileClient(await _decrypt_kanban_token(team), board_id)
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

    try:
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("kanban:tasks:"))
async def cb_kanban_tasks(callback: CallbackQuery):
    """Показать список карточек выбранной колонки."""
    column_id = callback.data.split(":", 2)[2]
    async with get_session() as session:
        team = await get_team_for_event(session, callback)
    if not team or not team.kanban_token:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return
    board_id = get_board_id(team)
    if not board_id:
        await callback.answer("Сначала выберите доску: /kanban_board", show_alert=True)
        return

    client = YouGileClient(await _decrypt_kanban_token(team), board_id)
    try:
        columns = await client.get_columns()
        cards = await client.get_cards_in_column(column_id)
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)
        return

    col_name = next(
        (c.get("title", "?") for c in columns if c["id"] == column_id),
        "?",
    )

    lines = [f"📋 {col_name} ({len(cards)}):", ""]
    if not cards:
        lines.append("  — задач нет")
    else:
        for card in cards[:30]:
            title = (card.get("title") or "").strip() or "(без названия)"
            lines.append(f"  • {title[:80]}")
        if len(cards) > 30:
            lines.append(f"  … и ещё {len(cards) - 30}")

    text = "\n".join(lines)

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="◀ К доске", callback_data="kanban:board"))
    kb.row(InlineKeyboardButton(text="➕ Добавить задачу", callback_data="kanban:add"))

    try:
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.message(Command("kanban_login"), F.chat.type == "private")
async def cmd_kanban_login(message: Message, state: FSMContext):
    from src.group_bot.permissions import get_role

    uid = message.from_user.id
    async with get_session() as session:
        teams = await get_user_teams(session, uid)
    team = teams[0] if teams else None
    if team is None:
        await message.answer(
            "❌ У тебя нет команды, которой ты владеешь или в которой ты админ.\n"
            "Создай команду через /i_am_director в групповом чате, "
            "либо запусти настройку из группы командой /setup_yougile."
        )
        return
    role = await get_role(team.chat_id, uid)
    if role != "admin":
        await message.answer("⛔ Доступно только администраторам")
        return
    await state.set_state(KanbanAuthStates.waiting_login)
    await state.update_data(setup_chat_id=team.chat_id)
    await message.answer(
        "Введи логин (email) от аккаунта YouGile:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True,
        ),
    )


@router.message(KanbanAuthStates.waiting_login)
async def process_login(message: Message, state: FSMContext):
    if not await _can_manage_message(message, state):
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
    if not await _can_manage_message(message, state):
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
    target_chat_id = data.get("setup_chat_id")

    await state.clear()
    await message.answer(".", reply_markup=ReplyKeyboardRemove())
    wait_msg = await message.answer("⏳ Получаю токен...")

    try:
        client = YouGileClient(api_token="", board_id="")
        token = await client.generate_token(login, password, "")
        async with get_session() as session:
            if target_chat_id:
                team = await get_team_by_chat(session, target_chat_id)
            else:
                team = await get_team_for_event(session, message)
            save_chat_id = (
                team.chat_id if team else (target_chat_id or message.chat.id)
            )
            encrypted_token = await crypto_service.encrypt_data(token)
            await update_team_kanban(session, save_chat_id, encrypted_token)
        await wait_msg.edit_text(
            "✅ Авторизация успешна.\n\n"
            "Теперь выбери доску: /kanban_board"
        )
    except ValueError as e:
        await wait_msg.edit_text(f"❌ {e}")
    except RuntimeError as e:
        await wait_msg.edit_text(
            f"⚠️ Что-то пошло не так, попробуй позже.\n{e}"
        )


@router.message(Command("kanban_board"), F.chat.type == "private")
async def cmd_kanban_board(message: Message, state: FSMContext):
    uid = message.from_user.id
    async with get_session() as session:
        team = await _resolve_dm_team(session, message)
        member = await get_team_member(session, team.id, uid) if team else None
    if not can_manage_kanban(team, member, uid):
        await message.answer("⛔ Доступно только администраторам")
        return

    args = message.text.split(maxsplit=1)

    if not team or not team.kanban_token:
        await message.answer("❌ Сначала выполни /kanban_login")
        return

    # Fallback: аргумент передан явно — сохраняем как активную доску
    if len(args) > 1:
        board_id = args[1].strip()
        if len(board_id) < 10:
            await message.answer("⚠️ Некорректный ID доски. Используй /kanban_board без аргумента для выбора из списка.")
            return
        client = YouGileClient(await _decrypt_kanban_token(team), board_id)
        try:
            boards = await client.get_boards()
        except Exception:
            boards = []
        board_name = next((b["title"] for b in boards if b["id"] == board_id), board_id)
        async with get_session() as session:
            await set_active_board(session, team.chat_id, board_id, board_name)
        await message.answer(
            f"✅ Активная доска: <b>{board_name}</b>\n"
            "Все новые задачи будут создаваться сюда."
        )
        return

    # Нет аргумента — получаем список досок
    client = YouGileClient(await _decrypt_kanban_token(team), board_id="")
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
            await set_active_board(session, team.chat_id, b["id"], b["title"])
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
async def cb_set_board(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    async with get_session() as session:
        team = await _resolve_dm_team(session, callback)
        member = await get_team_member(session, team.id, uid) if team else None
    if not can_manage_kanban(team, member, uid):
        await callback.answer("⛔ Доступно только администраторам", show_alert=True)
        return
    idx = int(callback.data.split(":")[1])

    data = await state.get_data()
    boards = data.get("boards", [])
    if idx < 0 or idx >= len(boards):
        await callback.answer("Ошибка: доска не найдена", show_alert=True)
        return
    board_id, board_name = boards[idx]
    logger.info("Board selected via callback: chat=%s board_id=%s board_name=%s", callback.message.chat.id, board_id, board_name)

    async with get_session() as session:
        token = await _decrypt_kanban_token(team)
        await set_active_board(session, team.chat_id if team else callback.message.chat.id, board_id, board_name)

    pending_tasks = data.get("pending_tasks")

    if pending_tasks:
        c = YouGileClient(token, board_id)
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
    uid = message.from_user.id
    async with get_session() as session:
        team = await _resolve_dm_team(session, message)
        member = await get_team_member(session, team.id, uid) if team else None
    if not can_manage_kanban(team, member, uid):
        await message.answer("⛔ Доступно только администраторам")
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
        await set_active_board(session, team.chat_id if team else message.chat.id, board_id, board_name)

    await message.answer(
        f"✅ Активная доска: <b>{board_name}</b>\n"
        "Все новые задачи будут создаваться сюда."
    )


# ── kanban:add — создание задачи (FSM) ─────────────────────────────────────


@router.callback_query(F.data == "kanban:add")
async def cb_kanban_add(callback: CallbackQuery, state: FSMContext):
    async with get_session() as session:
        team = await get_team_for_event(session, callback)
    board_id = get_board_id(team)
    if not team or not team.kanban_token or not board_id:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return
    await state.update_data(
        kanban_token=await _decrypt_kanban_token(team),
        kanban_board_id=board_id,
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
        team = await get_team_for_event(session, callback)
    board_id = get_board_id(team)
    if not team or not team.kanban_token or not board_id:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    client = YouGileClient(await _decrypt_kanban_token(team), board_id)

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
        team = await get_team_for_event(session, callback)
    board_id = get_board_id(team)
    if not team or not team.kanban_token or not board_id:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    client = YouGileClient(await _decrypt_kanban_token(team), board_id)

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
    uid = callback.from_user.id
    async with get_session() as session:
        team = await get_team_for_event(session, callback)
        member = await get_team_member(session, team.id, uid) if team else None
    if not can_manage_kanban(team, member, uid):
        await callback.answer("⛔ Доступно только администраторам", show_alert=True)
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


@router.callback_query(F.data.startswith("kanban:change_board:"))
async def cb_kanban_change_board(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    async with get_session() as session:
        team = await get_team_for_event(session, callback)
        member = await get_team_member(session, team.id, uid) if team else None
    if not can_manage_kanban(team, member, uid):
        await callback.answer("⛔ Доступно только администраторам", show_alert=True)
        return
    board_id = get_board_id(team)
    if not team or not team.kanban_token or not board_id:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    client = YouGileClient(await _decrypt_kanban_token(team), board_id)

    parts = callback.data.split(":")
    task_id = parts[2]
    tg_raw = parts[3]

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
        team = await get_team_for_event(session, callback)
    board_id = get_board_id(team)
    if not team or not team.kanban_token or not board_id:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return
    client = YouGileClient(await _decrypt_kanban_token(team), board_id)

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
        team = await get_team_for_event(session, callback)
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
        team = await get_team_for_event(session, callback)
    board_id = get_board_id(team)
    if not team or not team.kanban_token or not board_id:
        await callback.answer("Сначала настройте канбан-доску", show_alert=True)
        return

    await state.update_data(
        kanban_task_id=task_id,
        kanban_token=await _decrypt_kanban_token(team),
        kanban_board_id=board_id,
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