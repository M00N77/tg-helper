"""Обработка встреч: транскрипция аудио/видео, саммари, создание задач на YouGile."""
import json
import logging
from pathlib import Path

from src.bot.filters import OwnerOrTeamMember


def detect_platform(url: str) -> str:
    """Определить платформу видеоконференции по URL встречи."""
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

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton


from src.core.transcription import transcription_service
from src.db.repo import (
    create_meeting,
    get_or_create_user,
    get_team_by_chat,
    get_api_key,
    set_active_board,
    update_meeting_summary,
    update_meeting_transcript,
)
from src.db.session import get_session
from src.llm.router import get_provider_chain, llm_with_fallback
from src.llm.base import ChatMessage
from src.bot.handlers.yougile import YouGileClient
from src.config import settings as app_settings


logger = logging.getLogger(__name__)

router = Router(name="meeting")
router.message.filter(OwnerOrTeamMember())

MEETING_EXTRACT_SYSTEM = (
    "Ты анализируешь транскрипцию встречи.\n"
    "Верни СТРОГИЙ JSON (без markdown-обёртки):\n"
    '{\n'
    '  "summary": "саммари встречи 3-5 предложений",\n'
    '  "participants": ["имя1", "имя2"],\n'
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

    try:
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
            providers = await get_provider_chain(session, owner)

        if not providers:
            await notice.edit_text(
                f"✅ Транскрипция готова:\n\n{transcript[:1000]}…\n\n"
                "⚠️ Добавь LLM-ключ в /settings чтобы извлечь задачи."
            )
            return

        async with get_session() as session:
            team = await get_team_by_chat(session, message.chat.id)

        meeting_id: int | None = None
        if team:
            async with get_session() as session:
                meeting = await create_meeting(
                    session, team.id, f"upload:{message.message_id}",
                )
                meeting_id = meeting.id

        await notice.edit_text("🤖 Анализирую встречу…")
        try:
            raw = await llm_with_fallback(
                providers,
                [
                    ChatMessage(role="system", content=MEETING_EXTRACT_SYSTEM),
                    ChatMessage(role="user", content=f"Транскрипция:\n\n{transcript[:8000]}"),
                ],
                heavy=True,
                notify_bot=message.bot,
                notify_chat_id=message.chat.id,
            )
            data = json.loads(raw.strip().strip("```").lstrip("json").strip())
            summary = data.get("summary", "")
            tasks = data.get("tasks", [])

            if meeting_id:
                async with get_session() as session:
                    await update_meeting_transcript(session, meeting_id, transcript, str(target))
                    if summary:
                        await update_meeting_summary(session, meeting_id, summary)
        except Exception as e:
            await notice.edit_text(
                f"✅ Транскрипция готова, но разбор не удался: {e}\n\n"
                f"{transcript[:800]}"
            )
            return

        created_count = 0
        created_tasks: list[str] = []
        board_title = ""
        first_col_title = ""
        needs_board_selection = False
        all_boards: list[dict] = []

        if team and team.kanban_token and tasks:
            board_id = team.active_board_id or team.kanban_board_id
            if board_id:
                client = YouGileClient(team.kanban_token, board_id)
                try:
                    boards_data = await client.get_boards()
                    for b in boards_data:
                        if b["id"] == board_id:
                            board_title = b.get("title", "")
                            break
                    columns = await client.get_columns()
                    if columns:
                        first_col_id = columns[0]["id"]
                        first_col_title = columns[0].get("title", "")
                    else:
                        first_col_id = None
                except Exception:
                    first_col_id = None

                if first_col_id:
                    for task in tasks[:10]:
                        title = (task.get("title") or "").strip()
                        if not title:
                            continue
                        try:
                            deadline_raw = task.get("deadline") or ""
                            deadline = deadline_raw[:10] if deadline_raw else None
                            card = await client.create_card(title, "", first_col_id, deadline=deadline)
                            created_tasks.append(card.get("title", title))
                            created_count += 1
                        except Exception:
                            pass
            else:
                try:
                    tmp_client = YouGileClient(team.kanban_token)
                    all_boards = await tmp_client.get_boards()
                except Exception:
                    all_boards = []

                if len(all_boards) == 1:
                    b = all_boards[0]
                    async with get_session() as session:
                        await set_active_board(session, message.chat.id, b["id"], b["title"])
                    board_id = b["id"]
                    board_title = b["title"]
                    client = YouGileClient(team.kanban_token, board_id)
                    try:
                        columns = await client.get_columns()
                        if columns:
                            first_col_id = columns[0]["id"]
                            first_col_title = columns[0].get("title", "")
                        else:
                            first_col_id = None
                    except Exception:
                        first_col_id = None
                    if first_col_id:
                        for task in tasks[:10]:
                            title = (task.get("title") or "").strip()
                            if not title:
                                continue
                            try:
                                deadline_raw = task.get("deadline") or ""
                                deadline = deadline_raw[:10] if deadline_raw else None
                                card = await client.create_card(title, "", first_col_id, deadline=deadline)
                                created_tasks.append(card.get("title", title))
                                created_count += 1
                            except Exception:
                                pass
                elif len(all_boards) > 1:
                    await state.update_data(pending_tasks=tasks[:10])
                    needs_board_selection = True

        duration_str = ""
        if duration_sec:
            h = duration_sec // 3600
            m = (duration_sec % 3600) // 60
            s = duration_sec % 60
            if h:
                duration_str = f"{h}ч {m}мин"
            elif m:
                duration_str = f"{m}мин {s}сек"
            else:
                duration_str = f"{s}сек"
        lines = [f"📋 <b>Встреча — саммари</b>" + (f" · ⏱ {duration_str}" if duration_str else "") + f"\n\n{summary}"]

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
            board_part = f"«{board_title}»" if board_title else "YouGile"
            col_part = f" (колонка «{first_col_title}»)" if first_col_title else ""
            lines.append(f"\n📊 <b>Создано на доске {board_part}{col_part}:</b>")
            for t in created_tasks:
                lines.append(f"  • {t}")
            lines.append(f"📋 Доска: {team.active_board_name or 'по умолчанию'}")
            # Change C: свежий срез доски после создания задач
            try:
                snapshot = await build_board_text(
                    client,
                    f"📊 Текущее состояние доски «{board_title or board_id}»",
                )
                lines.append(f"\n{snapshot}")
            except Exception:
                pass
        elif tasks and (not team or not team.kanban_token):
            lines.append("\n💡 Подключи YouGile (/kanban) чтобы задачи создавались автоматически.")

        if duration_sec and duration_sec >= 120 and tasks:
            people = list({t.get("assignee") for t in tasks if t.get("assignee")})
            per_person = duration_sec // 60
            lines.append(f"\n⏱ <b>Трудозатраты встречи:</b>")
            lines.append(f"  Длительность: {duration_str}")
            if people:
                lines.append(f"  Участники: {', '.join(people)}")
                lines.append(f"  ~{per_person} мин/чел (равномерно)")
            lines.append(f"  Итого: ~{len(people) * per_person if people else per_person} чел·мин")

        if needs_board_selection:
            lines.append("\n\n📋 <b>Выбери доску для создания задач:</b>")
            kb = InlineKeyboardBuilder()
            for b in all_boards:
                kb.row(InlineKeyboardButton(
                    text=b["title"],
                    callback_data=f"sb:{b['id']}",
                ))
            await notice.edit_text(
                "\n".join(lines)[:4000],
                parse_mode="HTML",
                reply_markup=kb.as_markup(),
            )
        else:
            kb = InlineKeyboardBuilder()
            kb.button(text="📊 Открыть доску", callback_data="meeting:yougile")
            await notice.edit_text(
                "\n".join(lines)[:4000],
                parse_mode="HTML",
                reply_markup=kb.as_markup(),
            )
    finally:
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
    board_id = team.active_board_id or team.kanban_board_id
    if not board_id:
        await callback.answer("Сначала выбери доску /kanban_board", show_alert=True)
        return
    from src.bot.handlers.yougile import YouGileClient
    client = YouGileClient(team.kanban_token, board_id)
    text = await build_board_text(client, "📊 Доска")
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()
