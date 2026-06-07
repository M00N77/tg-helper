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


async def _analyze_sentiment(card_titles: list[str]) -> str:
    if not card_titles:
        return "😐 Недостаточно данных"
    sample = "\n".join(f"- {t}" for t in card_titles[:30])
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            r = await s.post(
                "https://api.anthropic.com/v1/messages",
                headers={"Content-Type": "application/json"},
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 100,
                    "messages": [{
                        "role": "user",
                        "content": (
                            f"Оцени общее настроение команды по названиям задач. "
                            f"Ответь ТОЛЬКО одной строкой: emoji + 2-4 слова (например: '😊 Команда в тонусе' или '😰 Много проблем').\n\n"
                            f"Задачи:\n{sample}"
                        )
                    }]
                }
            )
            data = await r.json()
            text = data["content"][0]["text"].strip()
            return text
    except Exception as e:
        return f"😐 Анализ недоступен"


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

    all_titles = []
    for col in columns:
        try:
            cards = await client.get_cards_in_column(col["id"])
            all_titles.extend([c.get("title", "") for c in cards if c.get("title")])
        except Exception:
            pass
    sentiment = await _analyze_sentiment(all_titles)
    lines.append(f"\n🧠 <b>Настроение команды:</b> {sentiment}")

    await message.answer("\n".join(lines), parse_mode="HTML")
