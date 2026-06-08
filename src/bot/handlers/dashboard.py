"""Manager dashboard — /dashboard."""
import time
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.filters import OwnerOrTeamMember
from src.bot.handlers.yougile import YouGileClient
from src.db.repo import get_team_by_chat, list_open_commitments, get_or_create_user
from src.db.session import get_session

router = Router(name="dashboard")
router.message.filter(OwnerOrTeamMember())

NOW_MS = lambda: int(time.time() * 1000)


@router.message(Command("dashboard"))
async def cmd_dashboard(message: Message) -> None:
    if message.from_user is None:
        return

    wait = await message.answer("⏳ Собираю данные...")

    lines = ["📋 <b>Manager Dashboard</b>\n"]

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        commitments = await list_open_commitments(session, owner)
        team = await get_team_by_chat(session, message.chat.id)

    # — Обязательства из переписки
    mine = [c for c in commitments if c.direction == "mine"]
    theirs = [c for c in commitments if c.direction == "theirs"]
    lines.append(f"📌 <b>Обязательства</b>")
    lines.append(f"  Мои открытые: {len(mine)}")
    lines.append(f"  Ждут от других: {len(theirs)}\n")

    # — Канбан
    if team and team.kanban_token and team.kanban_board_id:
        try:
            client = YouGileClient(team.kanban_token, team.kanban_board_id)
            columns = await client.get_columns()
            now = NOW_MS()

            total = 0
            risk = 0
            col_lines = []

            for col in columns:
                try:
                    cards = await client.get_cards_in_column(col["id"], limit=100)
                except Exception:
                    cards = []
                count = len(cards)
                total += count

                ages = [(now - c["timestamp"]) / 86400000
                        for c in cards if c.get("timestamp")]
                avg = sum(ages) / len(ages) if ages else 0
                threshold = avg * 2 if avg > 1 else 7
                col_risk = sum(1 for a in ages if a >= threshold)
                risk += col_risk

                flag = " ⚠️" if col_risk else ""
                col_lines.append(
                    f"  {col.get('title','?')}: {count} задач"
                    + (f", avg {avg:.1f}дн{flag}" if avg else "")
                )

            lines.append("📊 <b>Канбан-доска</b>")
            lines.extend(col_lines)
            lines.append(f"  Всего: {total} | Под риском: {risk}\n")
        except Exception as e:
            lines.append(f"📊 Канбан: ошибка ({e})\n")
    else:
        lines.append("📊 Канбан: не подключён\n")

    # — Итог
    total_issues = len(mine) + (risk if team and team.kanban_token else 0)
    if total_issues == 0:
        lines.append("✅ <b>Всё в порядке — рисков нет</b>")
    elif total_issues <= 3:
        lines.append(f"🟡 <b>Требует внимания:</b> {total_issues} пункт(а)")
    else:
        lines.append(f"🔴 <b>Критично:</b> {total_issues} пунктов требуют внимания")

    await wait.edit_text("\n".join(lines), parse_mode="HTML")
