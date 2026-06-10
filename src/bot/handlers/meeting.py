"""Обработка встреч: транскрипция аудио/видео, саммари, создание задач на YouGile."""
import logging
from pathlib import Path

from aiogram import Router, F


def detect_platform(url: str) -> str:
    url_lower = url.lower()
    if "telemost.yandex" in url_lower:
        return "yandex"
    if "tolk" in url_lower:
        return "kontur"
    if "mts-link" in url_lower:
        return "mts"
    if "jazz.sber" in url_lower or "sberjazz" in url_lower:
        return "sber"
    if "zoom.us" in url_lower or "zoom.com" in url_lower:
        return "zoom"
    return "unknown"
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from src.bot.filters import OwnerOrTeamMember
from src.db.repo import (
    create_meeting,
    get_or_create_user,
    get_team_by_chat,
    get_pending_action,
    delete_pending_action,
)
from src.db.session import get_session
from src.core.meeting_processor import process_meeting_audio, create_yougile_tasks_from_meeting
from src.config import settings as app_settings
from src.db.models import Team


logger = logging.getLogger(__name__)

router = Router(name="meeting")
router.message.filter(OwnerOrTeamMember())
router.callback_query.filter(OwnerOrTeamMember())


@router.message(Command("meeting"))
async def cmd_meeting(message: Message):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📎 Отправить запись", callback_data="meeting:upload_hint"))
    kb.row(InlineKeyboardButton(text="❓ Как это работает", callback_data="meeting:howto"))

    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)

    if team and team.kanban_token:
        kb.row(InlineKeyboardButton(text="📊 Создать задачи на YouGile", callback_data="meeting:yougile"))
    kb.row(InlineKeyboardButton(text="🔑 API МТС Линк", callback_data="meeting:mtslink_token"))

    await message.answer(
        "🎙 Встречи\n\n"
        "Отправь запись встречи — бот транскрибирует, "
        "сделает саммари и создаст задачи на YouGile.",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data == "meeting:upload_hint")
async def cb_meeting_upload_hint(callback: CallbackQuery):
    await callback.answer("📎 Просто отправь аудио или видео файл встречи в этот чат.", show_alert=True)


@router.callback_query(F.data == "meeting:howto")
async def cb_meeting_howto(callback: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data="meeting:back"))
    await callback.message.edit_text(
        "Как использовать:\n\n"
        "1. Запиши встречу (Контур Толк, Яндекс Телемост, "
        "СберДжаз, МТС Линк и др.)\n"
        "2. Скачай запись на устройство\n"
        "3. Отправь файл сюда (аудио или видео)\n"
        "4. Бот транскрибирует речь и создаст задачи на доске",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "meeting:mtslink_token")
async def cb_meeting_mtslink_token(callback: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="◀ Назад", callback_data="meeting:back"))
    await callback.message.edit_text(
        "🔑 <b>API МТС Линк</b>\n\n"
        "Чтобы бот мог создавать встречи через МТС Линк, "
        "нужен API-токен.\n\n"
        "<b>Где взять:</b>\n"
        "1. Зайди в <a href='https://mts-link.ru'>mts-link.ru</a>\n"
        "2. «Бизнес» → «API / Webhooks» → вкладка «API»\n"
        "3. Нажми «Добавить», скопируй ключ\n\n"
        "<b>Как сохранить в боте:</b>\n"
        "Напиши в чат:\n"
        "<code>сохрани mtslink token ТВОЙ_КЛЮЧ</code>\n\n"
        "После этого команда «запланируй встречу» "
        "будет создавать встречи через МТС Линк.",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "meeting:back")
async def cb_meeting_back(callback: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📎 Отправить запись", callback_data="meeting:upload_hint"))
    kb.row(InlineKeyboardButton(text="❓ Как это работает", callback_data="meeting:howto"))

    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)

    if team and team.kanban_token:
        kb.row(InlineKeyboardButton(text="📊 Создать задачи на YouGile", callback_data="meeting:yougile"))
    kb.row(InlineKeyboardButton(text="🔑 API МТС Линк", callback_data="meeting:mtslink_token"))

    await callback.message.edit_text(
        "🎙 Встречи\n\n"
        "Отправь запись встречи — бот транскрибирует, "
        "сделает саммари и создаст задачи на YouGile.",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.message(F.audio | F.video | F.document)
async def handle_meeting_file(message: Message, state: FSMContext):
    media = message.audio or message.video or message.voice or message.document
    if media is None:
        return
    duration_sec = getattr(media, "duration", None) or 0
    if message.document and media.mime_type:
        if not media.mime_type.startswith("audio/") and not media.mime_type.startswith("video/"):
            return

    media_dir = app_settings.data_dir / "media" / "meetings"
    media_dir.mkdir(parents=True, exist_ok=True)

    if message.voice:
        suffix = ".ogg"
    elif message.audio:
        suffix = ".mp3"
    elif message.video:
        suffix = ".mp4"
    elif message.document:
        suffix = Path(media.file_name).suffix or ".bin"
    else:
        suffix = ".ogg"

    target = media_dir / f"meeting_{message.message_id}{suffix}"

    notice = await message.answer("⏳ Получаю файл…")
    try:
        await message.bot.download(media.file_id, destination=str(target))
    except Exception as e:
        await notice.edit_text(f"❌ Не удалось скачать файл: {e}")
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        team = await get_team_by_chat(session, message.chat.id)

    meeting_id = None
    if team:
        async with get_session() as session:
            meeting = await create_meeting(
                session, team.id, f"upload:{message.message_id}",
            )
            meeting_id = meeting.id

    if not meeting_id:
        await notice.edit_text("❌ Команда не найдена.")
        return

    await process_meeting_audio(
        audio_path=target,
        meeting_id=meeting_id,
        chat_id=message.chat.id,
        bot=message.bot,
        team=team,
        owner_telegram_id=message.from_user.id,
        notice_message=notice,
    )


from src.bot.handlers.kanban import build_board_text


@router.callback_query(F.data == "meeting:yougile")
async def cb_meeting_yougile(callback: CallbackQuery):
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token:
        await callback.answer("Сначала выполни /kanban_login", show_alert=True)
        return
    board_id = team.active_board_id or team.kanban_board_id
    if not board_id:
        await callback.answer("Сначала выбери доску /kanban_board", show_alert=True)
        return
    from src.bot.handlers.yougile import YouGileClient
    client = YouGileClient(team.kanban_token, board_id)
    text = await build_board_text(client, "📊 Доска")
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("mtask:confirm:"))
async def cb_mtask_confirm(callback: CallbackQuery) -> None:
    action_id = int(callback.data.split(":")[2])
    
    async with get_session() as session:
        action = await get_pending_action(session, action_id)
        if action is None or action.kind != "meeting_tasks":
            await callback.answer("Действие устарело или уже обработано", show_alert=True)
            return
        payload = action.payload
        tasks = payload["tasks"]
        meeting_id = payload["meeting_id"]
        team = await session.get(Team, payload["team_id"])
        await delete_pending_action(session, action_id)
    
    if not team or not team.kanban_token:
        await callback.message.edit_text("❌ Канбан больше не подключён. Задачи не созданы.")
        await callback.answer()
        return
    
    # Create tasks in YouGile
    created, failed, titles, board_name = await create_yougile_tasks_from_meeting(
        team, tasks, meeting_id, callback.message.chat.id, callback.bot
    )
    
    result = "✅ Задачи созданы!" if failed == 0 else f"✅ {created} создано, {failed} с ошибками"
    await callback.message.edit_text(f"{result}\n\n📋 Доска: {board_name or 'по умолчанию'}")
    await callback.answer()


@router.callback_query(F.data.startswith("mtask:cancel:"))
async def cb_mtask_cancel(callback: CallbackQuery) -> None:
    action_id = int(callback.data.split(":")[2])
    async with get_session() as session:
        await delete_pending_action(session, action_id)
    await callback.message.edit_text("❌ Создание задач отменено.")
    await callback.answer()
