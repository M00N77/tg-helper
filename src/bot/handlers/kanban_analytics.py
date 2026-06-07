"""Аналитика сроков YouGile-доски — /kanban_analytics."""
import time
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.filters import OwnerOnly
from src.bot.handlers.yougile import YouGileClient
from src.db.repo import get_team_by_chat
from src.db.session import get_session

router = Router(name="kanban_analytics")
router.message.filter(OwnerOnly())

NOW_MS = lambda: int(time.time() * 1000)


@router.message(Command("kanban_analytics"))
async def cmd_kanban_analytics(message: Message) -> None:
    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)

    if not team or not team.kanban_token:
        await message.answer("❌ Сначала выполни /kanban_login")
        return
    if not team.kanban_board_id:
        await message.answer("❌ Сначала выбери доску /kanban_board")
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)
    try:
        columns = await client.get_columns()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        return

    now = NOW_MS()
    lines = ["📊 <b>Аналитика доски</b>\n"]

    total_cards = 0
    risk_cards = []

    for col in columns:
        col_id = col["id"]
        col_title = col.get("title", "?")
        try:
            cards = await client.get_cards_in_column(col_id)
        except Exception:
            cards = []

        if not cards:
            lines.append(f"<b>{col_title}</b>: пусто")
            continue

        ages = []
        for card in cards:
            ts = card.get("timestamp")
            if ts:
                age_days = (now - ts) / (1000 * 86400)
                ages.append((age_days, card.get("title", "?")))

        total_cards += len(cards)

        if ages:
            avg_days = sum(a for a, _ in ages) / len(ages)
            lines.append(
                f"<b>{col_title}</b>: {len(cards)} задач, "
                f"среднее время {avg_days:.1f} дн."
            )
            # риск: задачи старше 2x среднего
            threshold = avg_days * 2 if avg_days > 1 else 7
            for age_days, title in ages:
                if age_days >= threshold:
                    risk_cards.append((col_title, title, age_days))
        else:
            lines.append(f"<b>{col_title}</b>: {len(cards)} задач")

    lines.append(f"\n<b>Всего задач:</b> {total_cards}")

    if risk_cards:
        lines.append("\n⚠️ <b>Задачи под риском</b> (висят дольше нормы):")
        for col_title, title, age_days in risk_cards[:10]:
            lines.append(f"  • [{col_title}] {title[:50]} — {age_days:.0f} дн.")
    else:
        lines.append("\n✅ Все задачи в норме")

    await message.answer("\n".join(lines), parse_mode="HTML")
