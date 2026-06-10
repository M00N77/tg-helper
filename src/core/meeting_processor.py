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

        async with get_session() as session:
            await update_meeting_transcript(session, meeting_id, transcript, str(audio_path))
            if summary:
                await update_meeting_summary(session, meeting_id, summary)

            # If team has Kanban integration, create pending action for task approval
            if team and team.kanban_token and tasks:
                board_id = team.active_board_id or team.kanban_board_id
                if board_id:
                    # Get column info for payload (HTTP calls outside session)
                    first_col_id = None
                    first_col_title = None
                    board_title = None
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
                    except Exception:
                        pass

                    if first_col_id:
                        owner = await get_or_create_user(session, owner_telegram_id) if owner_telegram_id else None
                        pending_action = await create_pending_action(
                            session,
                            user_id=owner.id if owner else 0,
                            kind="meeting_tasks",
                            payload={
                                "meeting_id": meeting_id,
                                "summary": summary,
                                "tasks": tasks,
                                "team_id": team.id,
                                "chat_id": chat_id,
                                "board_id": board_id,
                                "first_col_id": first_col_id,
                                "board_title": board_title,
                                "first_col_title": first_col_title,
                            }
                        )

                        # Send message with approval buttons
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

                        from aiogram.utils.keyboard import InlineKeyboardBuilder
                        from aiogram.types import InlineKeyboardButton

                        kb = InlineKeyboardBuilder()
                        kb.row(
                            InlineKeyboardButton(text="✅ Создать все задачи", callback_data=f"mtask:confirm:{pending_action.id}"),
                        )
                        kb.row(
                            InlineKeyboardButton(text="❌ Отмена", callback_data=f"mtask:cancel:{pending_action.id}"),
                        )

                        if notice_message:
                            await notice_message.edit_text(
                                "\n".join(lines)[:4000],
                                parse_mode="HTML",
                                reply_markup=kb.as_markup()
                            )
                        else:
                            await bot.send_message(
                                chat_id,
                                "\n".join(lines)[:4000],
                                parse_mode="HTML",
                                reply_markup=kb.as_markup()
                            )

                        logger.info("Created pending action for meeting tasks: %s", pending_action.id)
                        return

        # If no Kanban or no tasks, proceed with normal flow
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

        # If we have tasks but no Kanban, show message about connecting YouGile
        if tasks and (not team or not team.kanban_token):
            lines.append("\n💡 Подключи YouGile (/kanban) чтобы задачи создавались автоматически.")

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
