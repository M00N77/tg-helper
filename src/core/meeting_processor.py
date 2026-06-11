import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.core.transcription import transcription_service
from src.db.repo import (
    get_or_create_user,
    get_api_key,
    update_meeting_transcript,
    update_meeting_summary,
    update_meeting_llm_raw,
    update_meeting_status,
    finish_meeting,
    create_pending_action,
)
from src.db.session import get_session
from src.llm.router import get_provider_chain, llm_with_fallback
from src.llm.base import ChatMessage
from src.bot.handlers.yougile import YouGileClient

logger = logging.getLogger(__name__)


async def create_yougile_tasks_from_meeting(
    team, tasks, meeting_id, chat_id, bot, notice_message=None
):
    """Создаёт карточки в YouGile. Возвращает (created_count, failed_count, created_titles, board_title)"""
    failed_count = 0
    created_tasks = []
    created_count = 0
    board_title = None
    first_col_title = None

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
                        logger.debug("YouGile task created: '%s' (meeting %s)", title, meeting_id)
                    except Exception as e:
                        logger.warning("YouGile task failed: '%s' error=%s", title, e)
                        failed_count += 1

    return created_count, failed_count, created_tasks, board_title


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


def build_approval_text(payload: dict) -> str:
    summary = payload.get("summary", "")
    tasks = payload.get("tasks", [])
    lines = [f"📋 <b>Саммари встречи</b>\n\n{summary}"]
    lines.append(f"\n📌 <b>Найдено задач: {len(tasks)}</b>")
    for i, t in enumerate(tasks[:10], 1):
        title = t.get("title", "?")
        assignee = t.get("assignee")
        deadline = t.get("deadline")
        tail = ""
        if assignee:
            tail += f" · {assignee}"
        if deadline:
            tail += f" · {deadline[:10]}"
        lines.append(f"{i}. {title}{tail}")
    lines.append("\nСоздать задачи на YouGile?")
    return "\n".join(lines)[:4000]


def build_approval_kb(action_id: int) -> "InlineKeyboardMarkup":
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Создать все задачи", callback_data=f"mtask:confirm:{action_id}"),
        InlineKeyboardButton(text="📋 Выбрать задачи", callback_data=f"mtask:select:{action_id}"),
    )
    kb.row(
        InlineKeyboardButton(text="✏️ Редактировать задачи", callback_data=f"mtask:editmenu:{action_id}"),
    )
    kb.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"mtask:cancel:{action_id}"),
    )
    return kb.as_markup()


def format_task_line(task: dict) -> str:
    """Однострочное представление задачи: 'название · исполнитель · дедлайн'."""
    title = (task.get("title") or "?").strip()
    assignee = task.get("assignee")
    deadline = task.get("deadline")
    tail = ""
    if assignee:
        tail += f" · {assignee}"
    if deadline:
        tail += f" · {deadline[:10]}"
    return f"{title}{tail}"


def parse_task_input(text: str) -> dict:
    """Разбирает текст пользователя в задачу.

    Поддерживаемые форматы (всё, кроме title, опционально):
      "Название"
      "Название | исполнитель"
      "Название | исполнитель | 2025-01-31"
    Разделитель — '|'. Дедлайн распознаётся как ISO-дата YYYY-MM-DD.
    """
    parts = [p.strip() for p in (text or "").split("|")]
    title = parts[0].strip()
    assignee = None
    deadline = None

    rest = parts[1:]
    for chunk in rest:
        if not chunk:
            continue
        candidate = chunk[:10]
        if len(candidate) == 10 and candidate[4] == "-" and candidate[7] == "-" and candidate.replace("-", "").isdigit():
            deadline = candidate
        elif assignee is None:
            assignee = chunk
    return {"title": title, "assignee": assignee, "deadline": deadline}


def build_edit_menu_text(payload: dict) -> str:
    summary = payload.get("summary", "")
    tasks = payload.get("tasks", [])
    lines = [f"📋 <b>Саммари встречи</b>\n\n{summary}"]
    lines.append(f"\n✏️ <b>Редактирование задач ({len(tasks)})</b>")
    if tasks:
        for i, t in enumerate(tasks[:10], 1):
            lines.append(f"{i}. {format_task_line(t)}")
    else:
        lines.append("Список пуст. Добавь задачу кнопкой ниже.")
    lines.append("\nВыбери задачу для изменения или удаления:")
    return "\n".join(lines)[:4000]


def build_edit_menu_kb(payload: dict, action_id: int) -> "InlineKeyboardMarkup":
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton

    tasks = payload.get("tasks", [])
    kb = InlineKeyboardBuilder()
    for i, t in enumerate(tasks[:10]):
        kb.row(
            InlineKeyboardButton(text=f"✏️ {i+1}", callback_data=f"mtask:edit:{action_id}:{i}"),
            InlineKeyboardButton(text=f"🗑 {i+1}", callback_data=f"mtask:del:{action_id}:{i}"),
        )
    kb.row(
        InlineKeyboardButton(text="➕ Добавить задачу", callback_data=f"mtask:add:{action_id}"),
    )
    kb.row(
        InlineKeyboardButton(text="🔙 Назад", callback_data=f"mtask:back:{action_id}"),
    )
    return kb.as_markup()


def build_selection_text(payload: dict) -> str:
    summary = payload.get("summary", "")
    tasks = payload.get("tasks", [])
    selected = payload.get("selected_indices", [])
    all_selected = len(selected) == 0

    lines = [f"📋 <b>Саммари встречи</b>\n\n{summary}"]
    lines.append(f"\n📌 <b>Задачи</b>")
    for i, t in enumerate(tasks[:10], 1):
        idx = i - 1
        checked = all_selected or idx in selected
        mark = "✅" if checked else "⬜"
        title = t.get("title", "?")
        assignee = t.get("assignee")
        deadline = t.get("deadline")
        tail = ""
        if assignee:
            tail += f" · {assignee}"
        if deadline:
            tail += f" · {deadline[:10]}"
        lines.append(f"{mark} {i}. {title}{tail}")
    lines.append("\nВыбери задачи для создания:")
    return "\n".join(lines)[:4000]


def build_selection_kb(payload: dict, action_id: int) -> "InlineKeyboardMarkup":
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton

    tasks = payload.get("tasks", [])
    selected = payload.get("selected_indices", [])
    all_selected = len(selected) == 0

    kb = InlineKeyboardBuilder()
    task_btns = []
    for i, t in enumerate(tasks[:10]):
        idx = i
        checked = all_selected or idx in selected
        label = f"{i+1}️⃣ {'✅' if checked else '⬜'}"
        task_btns.append(InlineKeyboardButton(text=label, callback_data=f"mtask:toggle:{action_id}:{idx}"))

    for chunk_start in range(0, len(task_btns), 5):
        kb.row(*task_btns[chunk_start:chunk_start + 5])

    if all_selected:
        count = len(tasks)
    else:
        count = len(selected)

    kb.row(
        InlineKeyboardButton(
            text=f"✅ Создать выбранные ({count})",
            callback_data=f"mtask:create_sel:{action_id}",
        )
    )
    kb.row(
        InlineKeyboardButton(text="🔙 Назад", callback_data=f"mtask:back:{action_id}"),
    )
    return kb.as_markup()


async def process_meeting_audio(
    audio_path: Path,
    meeting_id: int,
    chat_id: int,
    bot,
    team,
    owner_telegram_id: int | None = None,
    notice_message=None,
) -> None:
    file_saved = False
    try:
        logger.info("process_meeting_audio: start meeting_id=%s audio_path=%s", meeting_id, audio_path)

        if notice_message:
            await notice_message.edit_text("🎙 Транскрибирую запись встречи…")

        async with get_session() as session:
            if owner_telegram_id:
                owner = await get_or_create_user(session, owner_telegram_id)
                mode = owner.settings.transcription_mode
                openai_key = await get_api_key(session, owner, "openai")
            else:
                owner = None
                mode = "local"
                openai_key = None

        logger.info("TRANSCRIPTION STARTED: meeting=%s mode=%s", meeting_id, mode)
        try:
            transcript = await transcription_service.transcribe(
                audio_path,
                file_id=None,
                mode=mode,
                openai_key=openai_key,
                language="ru",
            )
        except Exception as e:
            logger.error("PIPELINE FAILED (transcription): meeting=%s error=%s", meeting_id, e)
            async with get_session() as session:
                await update_meeting_status(session, meeting_id, "failed")
            if notice_message:
                await notice_message.edit_text(f"❌ Ошибка транскрипции: {e}")
            return

        if not transcript or not transcript.strip():
            logger.warning("PIPELINE FAILED (empty transcript): meeting=%s", meeting_id)
            async with get_session() as session:
                await update_meeting_status(session, meeting_id, "failed")
            if notice_message:
                await notice_message.edit_text("❌ Не удалось распознать речь в файле.")
            return

        logger.info("TRANSCRIPTION DONE: meeting=%s chars=%d", meeting_id, len(transcript))
        file_saved = True

        if owner_telegram_id:
            async with get_session() as session:
                owner = await get_or_create_user(session, owner_telegram_id)
                providers = await get_provider_chain(session, owner)
        else:
            providers = []

        if not providers:
            async with get_session() as session:
                await update_meeting_transcript(session, meeting_id, transcript, str(audio_path))
                await finish_meeting(session, meeting_id)
            msg = f"✅ Транскрипция готова:\n\n{transcript[:1000]}…\n\n"
            msg += "⚠️ Добавь LLM-ключ в /settings чтобы извлечь задачи."
            await bot.send_message(chat_id, msg)
            logger.info("Meeting %s finished (transcript only, no LLM)", meeting_id)
            return

        if notice_message:
            await notice_message.edit_text("🤖 Анализирую встречу…")

        logger.info("LLM STARTED: meeting=%s", meeting_id)
        try:
            raw = await llm_with_fallback(
                providers,
                [
                    ChatMessage(role="system", content=MEETING_EXTRACT_SYSTEM),
                    ChatMessage(role="user", content=f"Транскрипция:\n\n{transcript[:8000]}"),
                ],
                heavy=True,
                notify_bot=bot,
                notify_chat_id=chat_id,
            )
        except Exception as e:
            logger.error("PIPELINE FAILED (LLM): meeting=%s error=%s", meeting_id, e)
            async with get_session() as session:
                await update_meeting_transcript(session, meeting_id, transcript, str(audio_path))
                await update_meeting_status(session, meeting_id, "failed")
            if notice_message:
                await notice_message.edit_text(
                    f"✅ Транскрипция готова, но разбор не удался: {e}\n\n"
                    f"{transcript[:800]}"
                )
            return

        async with get_session() as session:
            await update_meeting_llm_raw(session, meeting_id, raw)

        logger.info("LLM DONE: meeting=%s", meeting_id)
        try:
            data = json.loads(raw.strip().strip("```").lstrip("json").strip())
            summary = data.get("summary", "")
            tasks = data.get("tasks", [])
        except Exception as e:
            logger.error("PIPELINE FAILED (parse LLM JSON): meeting=%s error=%s", meeting_id, e)
            async with get_session() as session:
                await update_meeting_transcript(session, meeting_id, transcript, str(audio_path))
                await update_meeting_status(session, meeting_id, "failed")
            if notice_message:
                await notice_message.edit_text(
                    f"✅ Транскрипция готова, но разбор не удался: {e}\n\n"
                    f"{transcript[:800]}"
                )
            return

        # Resolve YouGile board/column info if integration is available.
        # This is best-effort: even if it fails, we still offer task editing.
        board_id = None
        first_col_id = None
        first_col_title = None
        board_title = None
        if team and team.kanban_token:
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
                except Exception as e:
                    logger.warning("YouGile column/board fetch failed for meeting %s: %s", meeting_id, e)

        async with get_session() as session:
            await update_meeting_transcript(session, meeting_id, transcript, str(audio_path))
            if summary:
                await update_meeting_summary(session, meeting_id, summary)

            # Whenever tasks were extracted, create a pending action so the user
            # always gets the approval/edit keyboard — regardless of whether the
            # Kanban board/column could be resolved.
            if tasks:
                owner = await get_or_create_user(session, owner_telegram_id) if owner_telegram_id else None
                payload = {
                    "meeting_id": meeting_id,
                    "summary": summary,
                    "tasks": tasks,
                    "team_id": team.id if team else None,
                    "chat_id": chat_id,
                    "board_id": board_id,
                    "first_col_id": first_col_id,
                    "board_title": board_title,
                    "first_col_title": first_col_title,
                    "selected_indices": [],
                }
                pending_action = await create_pending_action(
                    session,
                    user_id=owner.id if owner else 0,
                    kind="meeting_tasks",
                    payload=payload,
                )

                text = build_approval_text(payload)
                # If YouGile isn't ready, hint the user; editing still works.
                if not (team and team.kanban_token):
                    text += "\n\n💡 Подключи YouGile (/kanban), чтобы создавать задачи на доске."
                elif not first_col_id:
                    text += "\n\n⚠️ Не удалось получить доску YouGile. Выбери доску в /kanban_board."
                reply_markup = build_approval_kb(pending_action.id)

                if notice_message:
                    await notice_message.edit_text(text[:4000], parse_mode="HTML", reply_markup=reply_markup)
                else:
                    await bot.send_message(chat_id, text[:4000], parse_mode="HTML", reply_markup=reply_markup)

                await finish_meeting(session, meeting_id)
                logger.info("Created pending action for meeting tasks: %s", pending_action.id)
                return

        # No tasks extracted — just show the summary.
        lines = [f"📋 <b>Встреча — саммари</b>\n\n{summary}"]
        lines.append("\nЗадач не выявлено.")

        async with get_session() as session:
            await finish_meeting(session, meeting_id)

        if notice_message:
            await notice_message.edit_text(
                "\n".join(lines)[:4000],
                parse_mode="HTML",
            )
        else:
            await bot.send_message(chat_id, "\n".join(lines)[:4000], parse_mode="HTML")

        logger.info("PIPELINE FINISHED: meeting=%s status=%s", meeting_id, "processed")
    finally:
        if file_saved:
            try:
                audio_path.unlink(missing_ok=True)
                logger.debug("Deleted temp file %s", audio_path)
            except Exception:
                pass
