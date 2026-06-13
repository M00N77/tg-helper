"""Рейтинг закрытых задач за период — /tasks_rating.

Считает по Канбан-доске команды задачи в «готовых» колонках и строит рейтинг
по исполнителям за выбранный период (неделя / месяц / год). Это замена прежней
формулировки «покажи мою эмоцию» на продуктовую метрику результативности.

Использование (в командном чате):
  /tasks_rating          — за неделю (по умолчанию)
  /tasks_rating week     — за 7 дней
  /tasks_rating month    — за 30 дней
  /tasks_rating year     — за 365 дней
"""
import logging
import time

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src.bot.filters import OwnerOrTeamMember
from src.bot.handlers.yougile import YouGileClient, get_board_id
from src.db.repo import get_team_by_chat, get_team_members
from src.db.session import get_session

logger = logging.getLogger(__name__)
router = Router(name="tasks_rating")
router.message.filter(OwnerOrTeamMember())

NOW_MS = lambda: int(time.time() * 1000)

_DONE_KEYWORDS = ("готово", "done", "завершено", "closed", "выполнено", "complete")

_PERIODS = {
    "week": (7, "неделю"),
    "неделя": (7, "неделю"),
    "month": (30, "месяц"),
    "месяц": (30, "месяц"),
    "year": (365, "год"),
    "год": (365, "год"),
}


@router.message(Command("tasks_rating"))
async def cmd_tasks_rating(message: Message, command: CommandObject) -> None:
    if not message.from_user:
        return

    arg = (command.args or "").strip().lower()
    days, period_label = _PERIODS.get(arg, (7, "неделю"))

    wait = await message.answer(f"⏳ Считаю рейтинг закрытых задач за {period_label}...")

    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if not team:
            await wait.edit_text("❌ Это не командный чат. Сначала /team → Создать.")
            return
        members = await get_team_members(session, team.id)

    board_id = get_board_id(team)
    if not team.kanban_token or not board_id:
        await wait.edit_text("📊 Канбан команды не настроен. Подключите доску через /setup_kanban.")
        return

    # yougile_user_id → отображаемое имя участника
    name_by_yougile: dict[str, str] = {}
    for m in members:
        if m.yougile_user_id:
            name_by_yougile[m.yougile_user_id] = m.display_name or str(m.telegram_id)

    since_ms = NOW_MS() - days * 86400000
    client = YouGileClient(team.kanban_token, board_id)

    try:
        columns = await client.get_columns()
    except Exception as e:
        await wait.edit_text(f"❌ Не удалось получить колонки доски: {e}")
        return

    done_cols = [c for c in columns if any(k in c.get("title", "").lower() for k in _DONE_KEYWORDS)]
    if not done_cols:
        await wait.edit_text(
            "❓ На доске нет колонки «Готово/Done». "
            "Переименуйте финальную колонку, чтобы считать закрытые задачи."
        )
        return

    total_closed = 0
    per_assignee: dict[str, int] = {}
    unassigned = 0

    for col in done_cols:
        try:
            cards = await client.get_cards_in_column(col["id"], limit=100)
        except Exception:
            continue
        for card in cards:
            ts = card.get("timestamp", 0)
            # timestamp — последнее изменение карточки; для закрытых ≈ время закрытия.
            if ts and ts < since_ms:
                continue
            total_closed += 1
            assigned = card.get("assigned") or []
            if not assigned:
                unassigned += 1
                continue
            for uid in assigned:
                name = name_by_yougile.get(uid, f"id:{str(uid)[:6]}")
                per_assignee[name] = per_assignee.get(name, 0) + 1

    if total_closed == 0:
        await wait.edit_text(
            f"📭 За {period_label} закрытых задач не найдено.\n"
            f"(учитываются карточки в колонках: {', '.join(c.get('title','?') for c in done_cols)})"
        )
        return

    ranking = sorted(per_assignee.items(), key=lambda kv: kv[1], reverse=True)
    medals = ["🥇", "🥈", "🥉"]

    lines = [
        f"🏆 <b>Рейтинг закрытых задач за {period_label}</b>",
        f"🏢 {team.name or 'команда'} · всего закрыто: <b>{total_closed}</b>\n",
    ]
    for i, (name, count) in enumerate(ranking):
        prefix = medals[i] if i < len(medals) else f"{i + 1}."
        lines.append(f"{prefix} {name} — <b>{count}</b>")
    if unassigned:
        lines.append(f"\n👥 Без исполнителя: {unassigned}")

    await wait.edit_text("\n".join(lines), parse_mode="HTML")
