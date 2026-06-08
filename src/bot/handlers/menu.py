"""Единое меню навигации — /menu и все menu:* callback'и."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import OwnerOnly
from src.bot.states import MenuStates
from src.core.news import build_news_digest
from src.core.timeutil import fmt_local
from src.db.repo import (
    add_news_topic,
    get_api_key,
    get_or_create_user,
    get_team_by_chat,
    list_contacts,
    list_news_topics,
    list_open_commitments,
    toggle_news_topic,
)
from src.db.session import get_session
from src.userbot.manager import UserbotManager


router = Router(name="menu")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())


def breadcrumb(*parts: str) -> str:
    return " › ".join(parts) + "\n\n"


async def _check_warnings(telegram_id: int, userbot_manager: UserbotManager) -> list[str]:
    warnings: list[str] = []
    if userbot_manager.get_client(telegram_id) is None:
        warnings.append("⚠️ Userbot не подключён — /login")
    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        openai_key = await get_api_key(session, owner, "openai")
        gemini_key = await get_api_key(session, owner, "gemini")
        if not openai_key and not gemini_key:
            warnings.append("⚠️ LLM-ключ не задан — добавь в Настройки")
    return warnings


# ── /menu ───────────────────────────────────────────────────────────────────


@router.message(Command("menu"))
async def cmd_menu(message: Message, userbot_manager: UserbotManager) -> None:
    warnings = await _check_warnings(message.from_user.id, userbot_manager)
    text = "👋 Привет! Выбери раздел:"
    if warnings:
        text = "\n".join(warnings) + "\n\n" + text

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="💬 Чаты", callback_data="menu:chats"),
        InlineKeyboardButton(text="📊 Канбан", callback_data="menu:kanban"),
    )
    kb.row(
        InlineKeyboardButton(text="🎥 Встречи", callback_data="menu:meetings"),
        InlineKeyboardButton(text="📰 Новости", callback_data="menu:news"),
    )
    kb.row(
        InlineKeyboardButton(text="⚙ Настройки", callback_data="menu:settings"),
    )
    await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "menu:back")
async def cb_menu_back(callback: CallbackQuery, userbot_manager: UserbotManager) -> None:
    warnings = await _check_warnings(callback.from_user.id, userbot_manager)
    text = "👋 Привет! Выбери раздел:"
    if warnings:
        text = "\n".join(warnings) + "\n\n" + text

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="💬 Чаты", callback_data="menu:chats"),
        InlineKeyboardButton(text="📊 Канбан", callback_data="menu:kanban"),
    )
    kb.row(
        InlineKeyboardButton(text="🎥 Встречи", callback_data="menu:meetings"),
        InlineKeyboardButton(text="📰 Новости", callback_data="menu:news"),
    )
    kb.row(
        InlineKeyboardButton(text="⚙ Настройки", callback_data="menu:settings"),
    )
    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


# ── menu:chats ──────────────────────────────────────────────────────────────


@router.callback_query(F.data == "menu:chats")
async def cb_menu_chats(callback: CallbackQuery) -> None:
    text = breadcrumb("💬 Чаты") + "💬 Чаты — что хочешь сделать?"
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔍 Найти и проанализировать чат", callback_data="menu:chats:find"))
    kb.row(InlineKeyboardButton(text="✉️ Написать кому-нибудь", callback_data="menu:chats:send"))
    kb.row(InlineKeyboardButton(text="✅ Мои задачи и обещания", callback_data="menu:chats:todos"))
    kb.row(InlineKeyboardButton(text="🔄 Синхронизировать контакты", callback_data="menu:chats:sync"))
    kb.row(InlineKeyboardButton(text="◀ Главное меню", callback_data="goto:main:confirm"))
    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data == "menu:chats:find")
async def cb_menu_chats_find(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MenuStates.waiting_chat_name)
    await callback.message.edit_text("Введи имя контакта:")
    await callback.answer()


@router.callback_query(F.data == "menu:chats:send")
async def cb_menu_chats_send(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MenuStates.waiting_send_query)
    await callback.message.edit_text("Кому и что написать?")
    await callback.answer()


@router.callback_query(F.data == "menu:chats:todos")
async def cb_menu_chats_todos(callback: CallbackQuery) -> None:
    await callback.answer()
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        items = await list_open_commitments(session, owner)
        tz_name = owner.settings.timezone
    if not items:
        await callback.message.answer("Открытых обязательств нет 🎉")
        return
    await callback.message.answer(f"📋 Открытых обязательств: <b>{len(items)}</b>")
    for c in items[:30]:
        who = "Я" if c.direction == "mine" else (c.peer_name or "Они")
        deadline = fmt_local(c.deadline_at, tz_name)
        kb = InlineKeyboardBuilder()
        kb.row(
            InlineKeyboardButton(text="✅ Выполнено", callback_data=f"todo:done:{c.id}"),
            InlineKeyboardButton(text="🚫 Отменить", callback_data=f"todo:cancel:{c.id}"),
        )
        await callback.message.answer(
            f"<b>{who}</b> · {c.text} (до {deadline})",
            reply_markup=kb.as_markup(),
        )


@router.callback_query(F.data == "menu:chats:sync")
async def cb_menu_chats_sync(callback: CallbackQuery, userbot_manager: UserbotManager) -> None:
    await callback.answer()
    client = userbot_manager.get_client(callback.from_user.id)
    if client is None:
        await callback.message.answer("Сначала /login.")
        return
    from src.userbot.dialogs import sync_dialogs, prefetch_recent_messages
    import asyncio
    import logging

    logger = logging.getLogger(__name__)

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
    await callback.message.edit_text("🔄 Синхронизирую контакты…")
    stats = await sync_dialogs(client, owner, limit=500)
    total = sum(stats.values())
    await callback.message.answer(
        f"✅ Синхронизировано {total} диалогов:\n"
        f"  👤 Люди: {stats['users']}\n"
        f"  🤖 Боты: {stats['bots']}\n"
        f"  👥 Группы: {stats['chats']}\n"
        f"  📰 Каналы: {stats['channels']}\n"
        f"  🗂 Архивных: {stats['archived']}"
    )

    async def _bg_prefetch() -> None:
        try:
            ps = await prefetch_recent_messages(
                client, callback.from_user.id, top_n=30, per_chat=50, skip_channels=False,
            )
            await callback.message.answer(
                f"📥 Prefetch готов: {ps['chats']} чатов, {ps['messages']} сообщений в БД."
            )
        except Exception:
            logger.exception("prefetch failed")
            await callback.message.answer("⚠ Prefetch завершился с ошибкой — см. логи.")

    asyncio.create_task(_bg_prefetch())


# ── FSM: приём текста из меню чатов ────────────────────────────────────────


@router.message(MenuStates.waiting_chat_name)
async def handle_chat_name(message: Message, state: FSMContext, userbot_manager: UserbotManager) -> None:
    await state.clear()
    query = (message.text or "").strip()
    if not query:
        await message.answer("Имя не может быть пустым. Попробуй ещё раз через /chat.")
        return
    from src.bot.handlers.chat_cmd import cmd_chat
    from aiogram.filters import CommandObject
    await cmd_chat(message, CommandObject(prefix="/", command="chat", args=query), userbot_manager)


@router.message(MenuStates.waiting_send_query)
async def handle_send_query(message: Message, state: FSMContext, userbot_manager: UserbotManager) -> None:
    await state.clear()
    query = (message.text or "").strip()
    if not query:
        await message.answer("Запрос не может быть пустым. Попробуй ещё раз через /send.")
        return
    from src.bot.handlers.send import cmd_send
    from aiogram.filters import CommandObject
    await cmd_send(message, CommandObject(prefix="/", command="send", args=query), state, userbot_manager)


# ── menu:kanban ─────────────────────────────────────────────────────────────


@router.callback_query(F.data == "menu:kanban")
async def cb_menu_kanban(callback: CallbackQuery, state: FSMContext) -> None:
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)

    if not team or not team.kanban_token:
        text = breadcrumb("📊 Канбан") + "📊 Канбан — подключи YouGile чтобы начать"
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="🔑 Войти в YouGile", callback_data="menu:kanban:login"))
        kb.row(InlineKeyboardButton(text="◀ Главное меню", callback_data="goto:main:confirm"))
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()
        return

    if not team.kanban_board_id:
        text = breadcrumb("📊 Канбан") + "📊 Канбан — выбери доску для работы"
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="📋 Выбрать доску", callback_data="menu:kanban:board"))
        kb.row(InlineKeyboardButton(text="◀ Главное меню", callback_data="goto:main:confirm"))
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()
        return

    from src.bot.handlers.yougile import YouGileClient
    client = YouGileClient(team.kanban_token, team.kanban_board_id)
    try:
        boards = await client.get_boards()
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {e}")
        await callback.answer()
        return

    if not boards:
        await callback.message.edit_text(
            "❌ Ошибка: В вашей компании не найдено досок "
            "или у токена нет прав."
        )
        await callback.answer()
        return

    text = breadcrumb("📊 Канбан") + "📊 Выбери доску:"
    kb = InlineKeyboardBuilder()
    for b in boards:
        kb.row(InlineKeyboardButton(
            text=f"📋 {b.get('title', b['id'])}",
            callback_data=f"menu:kanban:open:{b['id']}"
        ))
    kb.row(InlineKeyboardButton(text="◀ Главное меню", callback_data="goto:main:confirm"))
    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data == "menu:kanban:login")
async def cb_menu_kanban_login(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    from src.bot.handlers.kanban import cmd_kanban_login
    await cmd_kanban_login(callback.message, state)


@router.callback_query(F.data == "menu:kanban:board")
async def cb_menu_kanban_board(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    from src.bot.handlers.kanban import cmd_kanban_board
    await cmd_kanban_board(callback.message, state)


@router.callback_query(F.data.startswith("menu:kanban:open:"))
async def cb_menu_kanban_open(callback: CallbackQuery) -> None:
    board_id = callback.data.split(":", 3)[3]
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    from src.bot.handlers.kanban import build_board_text
    from src.bot.handlers.yougile import YouGileClient
    client = YouGileClient(team.kanban_token, board_id)
    try:
        columns = await client.get_columns()
        text = await build_board_text(client, "📊 Доска")
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {e}")
        await callback.answer()
        return
    kb = InlineKeyboardBuilder()
    for col in columns:
        kb.row(InlineKeyboardButton(
            text=f"📋 {col.get('title', '?')}",
            callback_data=f"kanban:tasks:{col['id']}"
        ))
    kb.row(InlineKeyboardButton(text="◀ К доскам", callback_data="menu:kanban"))
    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


# ── menu:news ───────────────────────────────────────────────────────────────


@router.callback_query(F.data == "menu:news")
async def cb_menu_news(callback: CallbackQuery) -> None:
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        topics = await list_news_topics(session, owner)
        sources = await list_contacts(session, owner, kinds=("channel",), only_news_sources=True)

    topic_lines = []
    for t in topics:
        icon = "✅" if t.enabled else "▫"
        topic_lines.append(f"  {icon} {t.topic}")
    topics_text = "\n".join(topic_lines) if topic_lines else "  <i>нет тем</i>"

    header = breadcrumb("📰 Новости")
    text = (
        f"{header}📰 Новости\n\n"
        f"Темы ({len(topics)}):\n{topics_text}\n"
        f"Источники: {len(sources)} каналов"
    )

    kb = InlineKeyboardBuilder()
    for t in topics:
        icon = "✅" if t.enabled else "▫"
        kb.row(InlineKeyboardButton(
            text=f"{icon} {t.topic[:40]}",
            callback_data=f"menu:news:tog:{t.id}"
        ))
    kb.row(InlineKeyboardButton(text="➕ Добавить тему", callback_data="menu:news:add"))
    kb.row(InlineKeyboardButton(text="📡 Настроить источники", callback_data="menu:news:sources"))
    kb.row(InlineKeyboardButton(text="📨 Дайджест сейчас", callback_data="menu:news:now"))
    kb.row(InlineKeyboardButton(text="◀ Главное меню", callback_data="goto:main:confirm"))

    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("menu:news:tog:"))
async def cb_menu_news_tog(callback: CallbackQuery) -> None:
    topic_id = int(callback.data.split(":")[3])
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        await toggle_news_topic(session, owner, topic_id)
    await cb_menu_news(callback)
    await callback.answer()


@router.callback_query(F.data == "menu:news:add")
async def cb_menu_news_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MenuStates.waiting_news_topic)
    await callback.message.edit_text("Введи тему для мониторинга:")
    await callback.answer()


@router.message(MenuStates.waiting_news_topic)
async def handle_news_topic(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Пустая тема. Повтори или /cancel.")
        return
    parts = raw.rsplit(" ", 1)
    hours = 24
    topic = raw
    if len(parts) == 2 and parts[1].isdigit():
        hours = max(1, min(168, int(parts[1])))
        topic = parts[0].strip()
    if not topic:
        await message.answer("Не похоже на тему. Повтори или /cancel.")
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        await add_news_topic(session, owner, topic, hours=hours)

    await state.clear()
    await message.answer(f"✅ Добавил: <b>{topic}</b> (окно {hours}ч)")
    await _send_news_menu(message)


async def _send_news_menu(message: Message) -> None:
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        topics = await list_news_topics(session, owner)
        sources = await list_contacts(session, owner, kinds=("channel",), only_news_sources=True)

    topic_lines = []
    for t in topics:
        icon = "✅" if t.enabled else "▫"
        topic_lines.append(f"  {icon} {t.topic}")
    topics_text = "\n".join(topic_lines) if topic_lines else "  <i>нет тем</i>"

    header = breadcrumb("📰 Новости")
    text = (
        f"{header}📰 Новости\n\n"
        f"Темы ({len(topics)}):\n{topics_text}\n"
        f"Источники: {len(sources)} каналов"
    )

    kb = InlineKeyboardBuilder()
    for t in topics:
        icon = "✅" if t.enabled else "▫"
        kb.row(InlineKeyboardButton(
            text=f"{icon} {t.topic[:40]}",
            callback_data=f"menu:news:tog:{t.id}"
        ))
    kb.row(InlineKeyboardButton(text="➕ Добавить тему", callback_data="menu:news:add"))
    kb.row(InlineKeyboardButton(text="📡 Настроить источники", callback_data="menu:news:sources"))
    kb.row(InlineKeyboardButton(text="📨 Дайджест сейчас", callback_data="menu:news:now"))
    kb.row(InlineKeyboardButton(text="◀ Главное меню", callback_data="goto:main:confirm"))

    await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "menu:news:sources")
async def cb_menu_news_sources(callback: CallbackQuery, userbot_manager: UserbotManager) -> None:
    await callback.answer()
    client = userbot_manager.get_client(callback.from_user.id)
    if client is None:
        await callback.message.answer("Сначала /login.")
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        channels = await list_contacts(session, owner, kinds=("channel",))

    if not channels:
        await callback.message.answer("Каналов в БД нет. Запусти /sync.")
        return

    marked = sum(1 for c in channels if c.is_news_source)
    await callback.message.answer(
        f"📰 <b>Каналы для новостей</b>\n\n"
        f"Всего каналов: {len(channels)}\n"
        f"Помечено как источники: <b>{marked}</b>\n\n"
        "Тапни по каналу, чтобы переключить статус."
    )

    chunk = 20
    for i in range(0, len(channels), chunk):
        kb = InlineKeyboardBuilder()
        for c in channels[i:i + chunk]:
            mark = "✅" if c.is_news_source else "▫"
            label = f"{mark} {c.display_name[:40]}"
            kb.row(InlineKeyboardButton(text=label, callback_data=f"news:tog:{c.peer_id}"))
        kb.row(InlineKeyboardButton(text="◀ Назад к новостям", callback_data="menu:news"))
        await callback.message.answer(
            f"Список ({i + 1}–{i + len(channels[i:i+chunk])}):",
            reply_markup=kb.as_markup(),
        )


@router.callback_query(F.data == "menu:news:now")
async def cb_menu_news_now(callback: CallbackQuery, userbot_manager: UserbotManager) -> None:
    await callback.answer()
    client = userbot_manager.get_client(callback.from_user.id)
    if client is None:
        await callback.message.answer("Сначала /login.")
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        topics = await list_news_topics(session, owner)

    if not topics:
        await callback.message.answer("Сначала добавь хотя бы одну тему")
        return

    for t in topics:
        msg = await callback.message.answer(f"📰 Готовлю дайджест по «<i>{t.topic}</i>» (окно {t.hours}ч)…")
        text = await build_news_digest(client, callback.from_user.id, t.topic, hours=t.hours)
        await msg.edit_text(text, disable_web_page_preview=True)


# ── menu:settings ───────────────────────────────────────────────────────────


@router.callback_query(F.data == "menu:settings")
async def cb_menu_settings(callback: CallbackQuery) -> None:
    await callback.answer()
    from src.bot.handlers.settings import _render_menu, _safe_edit
    text, kb = await _render_menu(callback.from_user.id)
    await _safe_edit(callback.message, text, kb)


@router.callback_query(F.data == "menu:meetings")
async def cb_menu_meetings(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    from src.bot.handlers.meeting import cmd_meeting
    await cmd_meeting(callback.message)
