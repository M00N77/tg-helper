"""Еженедельный отчёт — /weekly."""
import time
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.filters import OwnerOrTeamMember
from src.bot.handlers.yougile import YouGileClient
from src.db.repo import get_team_by_chat, get_or_create_user, list_open_commitments
from src.db.session import get_session

router = Router(name="weekly")
router.message.filter(OwnerOrTeamMember())

WEEK_MS = 7 * 24 * 3600 * 1000


@router.message(Command("weekly"))
async def cmd_weekly(message: Message) -> None:
    if message.from_user is None:
        return

    wait = await message.answer("⏳ Готовлю отчёт за неделю...")
    now = int(time.time() * 1000)
    week_ago = now - WEEK_MS

    lines = ["📅 <b>Еженедельный отчёт</b>\n"]

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        commitments = await list_open_commitments(session, owner)
        team = await get_team_by_chat(session, message.chat.id)

    # — Обязательства
    mine = [c for c in commitments if c.direction == "mine"]
    overdue = [c for c in mine if c.deadline_at is not None]
    lines.append(f"📌 <b>Мои задачи:</b> {len(mine)} открытых")
    if overdue:
        lines.append(f"⚠️ С дедлайном: {len(overdue)}")
    lines.append("")

    # — Канбан
    if team and team.kanban_token and team.kanban_board_id:
        try:
            client = YouGileClient(team.kanban_token, team.kanban_board_id)
            columns = await client.get_columns()

            new_this_week = 0
            done_this_week = 0
            in_progress = 0

            for col in columns:
                try:
                    cards = await client.get_cards_in_column(col["id"], limit=100)
                except Exception:
                    cards = []

                col_title = col.get("title", "").lower()
                for card in cards:
                    ts = card.get("timestamp", 0)
                    if ts >= week_ago:
                        new_this_week += 1
                    if "готов" in col_title or "done" in col_title or "closed" in col_title:
                        if ts >= week_ago:
                            done_this_week += 1
                    if "работ" in col_title or "progress" in col_title or "in progress" in col_title:
                        in_progress += 1

            lines.append("📊 <b>Канбан за неделю:</b>")
            lines.append(f"  🆕 Создано задач: {new_this_week}")
            lines.append(f"  ✅ Закрыто: {done_this_week}")
            lines.append(f"  🔄 В работе сейчас: {in_progress}")
            lines.append("")
        except Exception as e:
            lines.append(f"📊 Канбан: ошибка ({e})\n")

    # — Итог
    total = len(mine)
    if total == 0:
        lines.append("🎉 <b>Отличная неделя — задолженностей нет!</b>")
    elif total <= 5:
        lines.append(f"🟡 <b>Неплохо</b> — {total} открытых задач")
    else:
        lines.append(f"🔴 <b>Много открытого</b> — {total} задач требуют внимания")

    await wait.edit_text("\n".join(lines), parse_mode="HTML")
