"""Присутствие на встречах: Яндекс Телемост, распознавание речи, извлечение задач."""
import asyncio
import glob
import json
import re
from datetime import datetime
from pathlib import Path

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import OwnerOnly
from src.bot.states import MeetingStates
from src.bot.handlers.meeting_listener import MeetingListener
from src.core.transcription import transcription_service
from src.bot.handlers.yougile import YouGileClient
from src.db.repo import create_meeting, update_meeting_transcript, update_meeting_summary, get_team_by_chat
from src.db.session import get_session
from src.llm.router import build_provider


router = Router(name="meeting")

# Хранилище активных встреч (в production использовать Redis)
active_meetings = {}


@router.message(Command("meeting"))
async def cmd_meeting(message: Message, state: FSMContext):
    """Управление встречами"""
    args = message.text.split()[1:] if message.text else []
    if args and args[0] == "join":
        if len(args) > 1:
            url = " ".join(args[1:])
            if not url.startswith("http"):
                url = f"https://telemost.yandex.ru/j/{url}"
            await _join_meeting_by_url(url, message)
        else:
            await state.set_state(MeetingStates.waiting_url)
            await message.answer(
                "🔗 Отправь ссылку на Яндекс Телемост:"
            )
        return
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🎥 Подключиться", callback_data="meeting:join"),
        InlineKeyboardButton(text="⏺ Начать запись", callback_data="meeting:record"),
    )
    kb.row(
        InlineKeyboardButton(text="📝 Обработать", callback_data="meeting:process"),
        InlineKeyboardButton(text="🚪 Выйти", callback_data="meeting:leave"),
    )
    
    await message.answer(
        "🎥 <b>Присутствие на встречах</b>\n\n"
        "Бот может:\n"
        "✅ Подключаться к Яндекс Телемосту\n"
        "✅ Слышать и записывать обсуждение\n"
        "✅ Распознавать устные задачи и договорённости\n"
        "✅ Автоматически создавать карточки в канбане\n\n"
        "Просто отправьте ссылку на встречу, и бот сделает всё сам!",
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data == "meeting:join")
async def cb_meeting_join(callback: CallbackQuery, state: FSMContext):
    """Подключение к Яндекс Телемосту"""
    await callback.message.answer(
        "🔗 <b>Подключение к встрече</b>\n\n"
        "Введите ссылку на Яндекс Телемост:\n"
        "Пример: https://telemost.yandex.ru/j/1234567890\n\n"
        "Или отправьте ID встречи: 1234567890\n\n"
        "Отмена — /cancel"
    )
    await state.set_state(MeetingStates.waiting_url)
    await callback.answer()


async def _join_meeting_by_url(url: str, message: Message) -> None:
    """Подключиться к Яндекс Телемосту по ссылке."""
    wait_msg = await message.answer("⏳ Подключаюсь к встрече...")
    try:
        async with get_session() as session:
            team = await get_team_by_chat(session, message.chat.id)
            if not team:
                await wait_msg.edit_text("❌ Сначала создайте команду: /team create")
                return
            meeting = await create_meeting(
                session, team_id=team.id, telemost_url=url
            )

        kb = InlineKeyboardBuilder()
        kb.button(
            text="🎙 Начать запись",
            callback_data=f"meeting:record:{meeting.id}"
        )

        await wait_msg.edit_text(
            f"✅ Встреча создана (ID: {meeting.id})\n"
            f"🔗 {url}\n\n"
            "Нажми кнопку ниже чтобы начать запись:",
            reply_markup=kb.as_markup()
        )
    except Exception as e:
        await wait_msg.edit_text(f"❌ Ошибка: {e}")


@router.message(MeetingStates.waiting_url, F.text)
async def process_meeting_url(message: Message, state: FSMContext):
    """Получить ссылку на Яндекс Телемост через FSM"""
    url = message.text.strip()

    if url.isdigit():
        url = f"https://telemost.yandex.ru/j/{url}"

    if "telemost.yandex.ru" not in url:
        await message.answer(
            "❌ Неверная ссылка. Ожидаю:\n"
            "https://telemost.yandex.ru/j/1234567890\n\n"
            "Попробуй ещё раз или /cancel"
        )
        return

    await state.clear()
    await _join_meeting_by_url(url, message)


@router.callback_query(F.data == "meeting:record")
async def cb_meeting_record(callback: CallbackQuery):
    """Начать запись встречи"""
    meeting_data = active_meetings.get(callback.message.chat.id)
    if not meeting_data:
        await callback.answer("Нет активной встречи. Сначала подключитесь.", show_alert=True)
        return
    
    await callback.message.answer("🎙 <b>Начинаю запись встречи...</b>\nЭто может занять несколько минут.")
    
    listener = meeting_data["listener"]
    
    # Запись 5 минут (можно настроить)
    audio_data = await listener.capture_audio_duration(300)
    
    # Сохраняем аудио
    media_dir = Path("data/media")
    media_dir.mkdir(parents=True, exist_ok=True)
    audio_path = media_dir / f"meeting_{meeting_data['meeting_id']}.wav"
    
    with open(audio_path, "wb") as f:
        f.write(audio_data)
    
    await callback.message.answer(
        f"✅ Запись сохранена: {audio_path.name}\n"
        f"📊 Размер: {len(audio_data) / 1024 / 1024:.1f} MB\n\n"
        f"🔄 <b>Расшифровываю разговор...</b>"
    )
    
    # Транскрипция
    transcript = await transcription_service.transcribe(
        audio_path,
        mode="api",  # для качества используем API
        language="ru"
    )
    
    # Сохраняем транскрипт
    await update_meeting_summary(meeting_data["meeting_id"], transcript=transcript[:5000])
    
    await callback.message.answer(
        f"📝 <b>Расшифровка встречи</b>\n\n"
        f"{transcript[:1500]}...\n\n"
        f"<i>Полный текст сохранён в БД</i>"
    )
    
    await callback.answer()


@router.callback_query(F.data.startswith("meeting:record:"))
async def cb_meeting_record_by_id(callback: CallbackQuery):
    """Начать запись встречи через UI Телемоста и скачать файл."""
    meeting_id = int(callback.data.split(":")[2])
    wait_msg = await callback.message.answer(
        "🎙 Подключаюсь к встрече и начинаю запись..."
    )
    await callback.answer()

    async with get_session() as session:
        from src.db.models import Meeting
        meeting = await session.get(Meeting, meeting_id)
        if not meeting:
            await wait_msg.edit_text("❌ Встреча не найдена в БД")
            return

    listener = MeetingListener(meeting.telemost_url)
    joined = await listener.join_meeting()

    if not joined:
        await wait_msg.edit_text(
            "❌ Не удалось подключиться к Телемосту."
        )
        return

    # Запускаем запись через UI
    await wait_msg.edit_text("⏺ Запускаю запись через Телемост...")
    recording_started = await listener.start_recording_via_ui()

    if not recording_started:
        await wait_msg.edit_text(
            "❌ Не удалось найти кнопку записи в Телемосте.\n"
            "Возможно интерфейс изменился."
        )
        return

    duration = 300
    await wait_msg.edit_text(
        f"🔴 Идёт запись встречи ({duration // 60} мин)...\n"
        "Бот сидит на встрече и пишет разговор."
    )
    await asyncio.sleep(duration)

    # Останавливаем запись
    await listener.stop_recording_via_ui()
    await wait_msg.edit_text(
        "✅ Запись остановлена.\n"
        "⏳ Файл скачивается в папку загрузок браузера...\n"
        "Подожди 10 секунд."
    )
    await asyncio.sleep(10)

    # Ищем последний скачанный файл
    downloads = sorted(
        glob.glob(
            str(Path.home() / "Downloads" / "*.webm")
        ) + glob.glob(
            str(Path.home() / "Downloads" / "*.mp4")
        ),
        key=lambda f: Path(f).stat().st_mtime,
        reverse=True
    )

    if not downloads:
        await wait_msg.edit_text(
            "⚠️ Файл записи не найден в папке Downloads.\n"
            "Попробуй скачать вручную и отправь боту."
        )
        return

    audio_path = Path(downloads[0])
    async with get_session() as session:
        await update_meeting_transcript(
            session, meeting_id, "", str(audio_path)
        )

    await listener.leave_meeting()

    kb = InlineKeyboardBuilder()
    kb.button(
        text="📝 Расшифровать и извлечь задачи",
        callback_data=f"meeting:transcribe:{meeting_id}"
    )
    await wait_msg.edit_text(
        f"✅ Запись готова: {audio_path.name}\n"
        f"📁 {audio_path}\n\n"
        "Нажми чтобы расшифровать и создать задачи в YouGile:",
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.startswith("meeting:process:"))
async def cb_meeting_process_by_id(callback: CallbackQuery):
    """Извлечь задачи из расшифровки встречи по ID."""
    meeting_id = int(callback.data.split(":")[2])
    wait_msg = await callback.message.answer("🔄 Анализирую встречу...")
    await callback.answer()

    async with get_session() as session:
        from src.db.models import Meeting
        meeting = await session.get(Meeting, meeting_id)
        if not meeting or not meeting.transcript:
            await wait_msg.edit_text(
                "❌ Нет расшифровки. Сначала запиши встречу."
            )
            return

        team = await get_team_by_chat(session, callback.message.chat.id)
        if not team:
            await wait_msg.edit_text("❌ Команда не найдена")
            return

    async with get_session() as session:
        from src.db.repo import get_or_create_user
        owner = await get_or_create_user(session, callback.from_user.id)
        provider = await build_provider(session, owner)

    from src.llm.base import ChatMessage
    prompt = (
        "Из расшифровки встречи выдели все задачи и договорённости. "
        "Верни ТОЛЬКО JSON массив без пояснений:\n"
        '[{"title":"...","description":"...","assignee":"...","deadline":"..."}]\n\n'
        f"Расшифровка:\n{meeting.transcript[:3000]}"
    )
    response = await provider.chat(
        [ChatMessage(role="user", content=prompt)]
    )

    try:
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        tasks = json.loads(json_match.group()) if json_match else []
    except Exception:
        tasks = []

    if not tasks:
        await wait_msg.edit_text("🤷 Задач не найдено в расшифровке")
        return

    created = []
    if team.kanban_token and team.kanban_board_id:
        client = YouGileClient(team.kanban_token, team.kanban_board_id)
        columns = await client.get_columns()
        if columns:
            first_col = columns[0]
            for task in tasks:
                card = await client.create_card(
                    title=task.get("title", "Задача"),
                    description=task.get("description", ""),
                    column_id=first_col["id"],
                )
                created.append(card)

    async with get_session() as session:
        await update_meeting_summary(session, meeting_id, response[:2000])
    await wait_msg.edit_text(
        f"✅ Готово!\n\n"
        f"📋 Найдено задач: {len(tasks)}\n"
        f"🎯 Создано в канбане: {len(created)}\n\n"
        + "\n".join(f"• {t.get('title','?')}" for t in tasks[:10])
    )


@router.callback_query(F.data == "meeting:process")
async def cb_meeting_process(callback: CallbackQuery):
    """Обработка встречи: извлечение задач и создание карточек"""
    meeting_data = active_meetings.get(callback.message.chat.id)
    if not meeting_data:
        await callback.answer("Нет активной встречи", show_alert=True)
        return
    
    await callback.message.answer("🔄 <b>Анализирую обсуждение...</b>")
    
    # Получаем транскрипт
    async with get_session() as session:
        from src.db.models import Meeting
        from sqlalchemy import select
        
        result = await session.execute(
            select(Meeting).where(Meeting.id == meeting_data["meeting_id"])
        )
        meeting = result.scalar_one_or_none()
    
    if not meeting or not meeting.transcript:
        await callback.message.answer(
            "❌ Нет расшифровки встречи.\n"
            "Сначала используйте «Записать»."
        )
        return
    
    # Извлекаем задачи через LLM
    team = await get_team_by_chat(callback.message.chat.id)
    
    async with get_session() as session:
        from src.db.repo import get_or_create_user
        owner = await get_or_create_user(session, callback.from_user.id)
        provider = await build_provider(session, owner)
    
    prompt = f"""
    Из расшифровки встречи выдели все задачи и договорённости.
    Верни JSON массив: [{{"title": "название", "description": "описание", "assignee": "имя", "deadline": "дата если есть"}}]
    
    Расшифровка:
    {meeting.transcript[:3000]}
    """
    
    response = await provider.chat([...])  # сокращено для примера
    tasks = json.loads(response) if response else []
    
    # Создаём карточки в канбане
    if team and team.kanban_token and team.kanban_provider == "yougile":
        client = YouGileClient(team.kanban_token, team.kanban_board_id)
        columns = await client.get_columns()
        todo_column = next((c for c in columns if "todo" in c["name"].lower()), columns[0])
        created = []
        for task in tasks:
            card = await client.create_card(
                title=task["title"],
                description=task["description"],
                column_id=todo_column["id"]
            )
            created.append(card)

        await callback.message.answer(
            f"✅ <b>Обработка завершена!</b>\n\n"
            f"📊 Выделено задач: {len(tasks)}\n"
            f"🎯 Создано карточек: {len(created)}\n\n"
            f"Все задачи добавлены в канбан-доску!"
        )
    else:
        await callback.message.answer(
            f"✅ <b>Выделено задач: {len(tasks)}</b>\n\n"
            + "\n".join([f"• {t['title']}" for t in tasks[:10]])
        )
    
    await callback.answer()


@router.callback_query(F.data.startswith("meeting:transcribe:"))
async def cb_meeting_transcribe(callback: CallbackQuery):
    """Расшифровать запись встречи."""
    meeting_id = int(callback.data.split(":")[2])
    wait_msg = await callback.message.answer("🔄 Расшифровываю запись...")
    await callback.answer()

    async with get_session() as session:
        from src.db.models import Meeting
        meeting = await session.get(Meeting, meeting_id)
        if not meeting or not meeting.audio_path:
            await wait_msg.edit_text("❌ Файл записи не найден")
            return

        audio_path = Path(meeting.audio_path)
        if not audio_path.exists():
            await wait_msg.edit_text(
                f"❌ Файл не найден: {audio_path}\n"
                "Возможно был перемещён или удалён."
            )
            return

        try:
            transcript = await transcription_service.transcribe(
                audio_path, mode="api", language="ru"
            )
            await update_meeting_transcript(
                session, meeting_id, transcript, str(audio_path)
            )

            kb = InlineKeyboardBuilder()
            kb.button(
                text="🎯 Извлечь задачи в YouGile",
                callback_data=f"meeting:process:{meeting_id}"
            )
            await wait_msg.edit_text(
                f"✅ Расшифровка готова!\n\n"
                f"📝 Первые 500 символов:\n{transcript[:500]}...\n\n"
                "Создать задачи в YouGile?",
                reply_markup=kb.as_markup()
            )
        except Exception as e:
            await wait_msg.edit_text(f"❌ Ошибка расшифровки: {e}")