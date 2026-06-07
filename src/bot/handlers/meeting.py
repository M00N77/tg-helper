"""Обработка встреч: транскрипция аудио/видео, саммари, создание задач на YouGile."""
import json
import logging
from pathlib import Path

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from src.bot.filters import OwnerOnly
from src.core.transcription import transcription_service
from src.db.repo import get_or_create_user, get_team_by_chat, get_api_key
from src.db.session import get_session
from src.llm.router import build_provider
from src.llm.base import ChatMessage
from src.bot.handlers.yougile import YouGileClient
from src.config import settings as app_settings


logger = logging.getLogger(__name__)

router = Router(name="meeting")
router.message.filter(OwnerOnly())

MEETING_EXTRACT_SYSTEM = (
    "Ты анализируешь транскрипцию встречи.\n"
    "Верни СТРОГИЙ JSON (без markdown-обёртки):\n"
    '{\n'
    '  "summary": "саммари встречи 3-5 предложений",\n'
    '  "tasks": [\n'
    '    {"title": "название задачи", "assignee": "имя или null", "deadline": "ISO-8601 или null"},\n'
    '    ...\n'
    '  ]\n'
    '}\n'
    'Если задач нет — tasks: [].\n'
    "Опирайся только на то, что сказано в тексте."
)


@router.message(Command("meeting"))
async def cmd_meeting(message: Message):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📎 Отправить запись", callback_data="meeting:upload_hint"))
    kb.row(InlineKeyboardButton(text="❓ Как это работает", callback_data="meeting:howto"))

    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)

    if team and team.kanban_token:
        kb.row(InlineKeyboardButton(text="📊 Создать задачи на YouGile", callback_data="meeting:yougile"))

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
        "Как использовать:\n"
        "1. Запиши встречу в Яндекс Телемосте\n"
        "2. Скачай запись (кнопка в интерфейсе Телемоста)\n"
        "3. Отправь файл сюда (аудио или видео)\n"
        "4. Бот транскрибирует и создаст задачи на доске",
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

    await callback.message.edit_text(
        "🎙 Встречи\n\n"
        "Отправь запись встречи — бот транскрибирует, "
        "сделает саммари и создаст задачи на YouGile.",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.message(F.audio | F.video | F.voice | F.document)
async def handle_meeting_file(message: Message):
    media = message.audio or message.video or message.voice or message.document
    if media is None:
        return
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
        mode = owner.settings.transcription_mode
        openai_key = await get_api_key(session, owner, "openai")

    await notice.edit_text("🎙 Транскрибирую запись встречи…")
    try:
        transcript = await transcription_service.transcribe(
            target,
            file_id=str(message.message_id),
            mode=mode,
            openai_key=openai_key,
            language="ru",
        )
    except Exception as e:
        await notice.edit_text(f"❌ Ошибка транскрипции: {e}")
        return

    if not transcript or not transcript.strip():
        await notice.edit_text("❌ Не удалось распознать речь в файле.")
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        provider = await build_provider(session, owner)

    if provider is None:
        await notice.edit_text(
            f"✅ Транскрипция готова:\n\n{transcript[:1000]}…\n\n"
            "⚠️ Добавь LLM-ключ в /settings чтобы извлечь задачи."
        )
        return

    await notice.edit_text("🤖 Анализирую встречу…")
    try:
        raw = await provider.chat(
            [
                ChatMessage(role="system", content=MEETING_EXTRACT_SYSTEM),
                ChatMessage(role="user", content=f"Транскрипция:\n\n{transcript[:8000]}"),
            ],
            heavy=True,
        )
        data = json.loads(raw.strip().strip("```").lstrip("json").strip())
        summary = data.get("summary", "")
        tasks = data.get("tasks", [])
    except Exception as e:
        await notice.edit_text(
            f"✅ Транскрипция готова, но разбор не удался: {e}\n\n"
            f"{transcript[:800]}"
        )
        return

    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)

    created_count = 0
    if team and team.kanban_token and team.kanban_board_id and tasks:
        client = YouGileClient(team.kanban_token, team.kanban_board_id)
        try:
            columns = await client.get_columns()
            first_col_id = columns[0]["id"] if columns else None
        except Exception:
            first_col_id = None

        if first_col_id:
            for task in tasks[:10]:
                title = (task.get("title") or "").strip()
                if not title:
                    continue
                try:
                    await client.create_card(title, "", first_col_id)
                    created_count += 1
                except Exception:
                    pass

    lines = [f"📋 <b>Встреча — саммари</b>\n\n{summary}"]

    if tasks:
        lines.append(f"\n✅ <b>Задачи ({len(tasks)}):</b>")
        for t in tasks[:10]:
            title = t.get("title", "?")
            assignee = t.get("assignee")
            deadline = t.get("deadline")
            tail = ""
            if assignee:
                tail += f" · {assignee}"
            if deadline:
                tail += f" · {deadline[:10]}"
            lines.append(f"  • {title}{tail}")
    else:
        lines.append("\nЗадач не выявлено.")

    if created_count > 0:
        lines.append(f"\n📊 Создано на доске YouGile: <b>{created_count}</b> задач")
    elif tasks and (not team or not team.kanban_token):
        lines.append("\n💡 Подключи YouGile (/kanban) чтобы задачи создавались автоматически.")

    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Открыть доску", callback_data="meeting:yougile")
    await notice.edit_text(
        "\n".join(lines)[:4000],
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )

    try:
        target.unlink(missing_ok=True)
    except Exception:
        pass


from src.bot.handlers.kanban import build_board_text


@router.callback_query(F.data == "meeting:yougile")
async def cb_meeting_yougile(callback: CallbackQuery):
    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
    if not team or not team.kanban_token:
        await callback.answer("Сначала выполни /kanban_login", show_alert=True)
        return
    if not team.kanban_board_id:
        await callback.answer("Сначала выбери доску /kanban_board", show_alert=True)
        return
    from src.bot.handlers.yougile import YouGileClient
    client = YouGileClient(team.kanban_token, team.kanban_board_id)
    text = await build_board_text(client, "📊 Доска")
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()
