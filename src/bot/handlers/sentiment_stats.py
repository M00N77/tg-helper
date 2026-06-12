"""Личная статистика тональности — /mysentiment.

Использование (в командном чате):
  /mysentiment       — моя тональность за последние 7 дней
  /mysentiment 14    — за указанное число дней (1..90)
"""
import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src.bot.filters import OwnerOrTeamMember
from src.db.repo import aggregate_team_sentiment, get_team_by_chat
from src.db.session import get_session

logger = logging.getLogger(__name__)
router = Router(name="sentiment_stats")
router.message.filter(OwnerOrTeamMember())


@router.message(Command("mysentiment"))
async def cmd_my_sentiment(message: Message, command: CommandObject) -> None:
    if not message.from_user:
        return

    days = 7
    arg = (command.args or "").strip()
    if arg:
        try:
            days = max(1, min(90, int(arg)))
        except ValueError:
            await message.answer("Укажите число дней, например: /mysentiment 14")
            return

    wait = await message.answer("⏳ Анализирую тональность ваших сообщений...")

    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if not team:
            await wait.edit_text("❌ Это не командный чат.")
            return
        agg = await aggregate_team_sentiment(
            session, team.id, days=days, user_id=message.from_user.id,
        )

    if agg.total == 0:
        await wait.edit_text(
            f"📭 За последние {days} дн. нет сообщений с анализом тональности.\n"
            f"Пишите в чат — ваши сообщения анализируются автоматически."
        )
        return

    lines = [
        f"🧑 <b>Моя тональность</b> · {team.name or 'команда'}",
        f"Период: {days} дн. · сообщений: {agg.total}\n",
        f"😊 позитивных:  {agg.positive} ({agg.positive_pct}%)",
        f"😠 негативных:  {agg.negative} ({agg.negative_pct}%)",
        f"😐 нейтральных: {agg.neutral} ({agg.neutral_pct}%)",
    ]
    if agg.speech:
        lines.append(f"👋 речевых этикетов: {agg.speech} ({agg.speech_pct}%)")

    await wait.edit_text("\n".join(lines), parse_mode="HTML")
