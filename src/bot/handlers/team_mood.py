"""Аналитика настроения команды по анонимным пульс-опросам — /team_mood.

Связывает накопленные данные пульс-опросов (activity_responses) с LLM-рекомендациями
руководителю. В отличие от /burnout (личные сообщения одного человека), здесь
агрегированная анонимная картина по всей команде: средний балл, распределение,
динамика по дням, тренд — и на их основе конкретные советы.

Использование (в командном чате):
  /team_mood       — срез за последние 7 дней
  /team_mood 14    — срез за указанное число дней (1..90)
"""
import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src.bot.filters import OwnerOrTeamMember
from src.db.repo import (
    aggregate_pulse_responses,
    get_or_create_user,
    get_team_by_chat,
)
from src.db.session import get_session
from src.llm.base import ChatMessage
from src.llm.router import build_provider

logger = logging.getLogger(__name__)
router = Router(name="team_mood")
router.message.filter(OwnerOrTeamMember())

_TREND_LABEL = {
    "up": "📈 настроение растёт",
    "down": "📉 настроение снижается",
    "flat": "➡️ стабильно",
    "n/a": "—",
}

MOOD_RECO_PROMPT = """Ты HR-аналитик. По анонимным данным пульс-опросов команды дай короткие
практичные рекомендации руководителю. Данные агрегированы, без привязки к людям.

Период: {days} дн.
Голосов всего: {total}
Средний балл (1..5): {avg}
Распределение: {dist}
Динамика по дням: {by_day}
Тренд: {trend}

Ответь СТРОГО в формате (без вступлений):
🔎 Вывод: <1-2 фразы что происходит с командой>
⚠️ На что обратить внимание: <1-2 фразы или "ничего тревожного">
💡 Рекомендации: <2-3 конкретных действия для руководителя>"""


def _build_chart(by_day) -> str:
    if not by_day:
        return ""
    lines = []
    for d in by_day:
        filled = round(d.avg)
        bar = "▰" * filled + "▱" * (5 - filled)
        lines.append(f"  {d.day.strftime('%d.%m')}: {bar} {d.avg:.1f} ({d.count})")
    return "\n".join(lines)


@router.message(Command("team_mood"))
async def cmd_team_mood(message: Message, command: CommandObject) -> None:
    if not message.from_user:
        return

    days = 7
    arg = (command.args or "").strip()
    if arg:
        try:
            days = max(1, min(90, int(arg)))
        except ValueError:
            await message.answer("Укажите число дней, например: /team_mood 14")
            return

    wait = await message.answer("⏳ Считаю настроение команды по пульс-опросам...")

    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if not team:
            await wait.edit_text("❌ Это не командный чат. Сначала /team → Создать.")
            return
        agg = await aggregate_pulse_responses(session, team.id, days=days)
        owner = await get_or_create_user(session, message.from_user.id)
        provider = await build_provider(session, owner)

    if agg.total_responses == 0:
        await wait.edit_text(
            f"📭 За последние {days} дн. нет данных пульс-опросов.\n"
            f"Запустите опрос командой /pulse или включите расписание /activities_on."
        )
        return

    mood = (
        "☀️ команда в тонусе" if agg.avg >= 4
        else "⛅️ рабочее состояние" if agg.avg >= 3
        else "🌧 стоит обратить внимание"
    )
    dist = " · ".join(f"{i}:{agg.distribution[i]}" for i in range(1, 6))

    lines = [
        f"🫶 <b>Настроение команды</b> · {team.name or 'команда'}",
        f"Период: {days} дн. · опросов: {agg.sessions} · голосов: {agg.total_responses}\n",
        f"Средний балл: <b>{agg.avg:.1f}/5</b> · {mood}",
        f"Тренд: {_TREND_LABEL.get(agg.trend, '—')}",
        f"Распределение: {dist}",
    ]
    chart = _build_chart(agg.by_day)
    if chart:
        lines.append(f"\nПо дням:\n{chart}")

    # LLM-рекомендации руководителю.
    if provider:
        try:
            by_day_text = ", ".join(
                f"{d.day.strftime('%d.%m')}={d.avg:.1f}" for d in agg.by_day
            )
            prompt = MOOD_RECO_PROMPT.format(
                days=days,
                total=agg.total_responses,
                avg=f"{agg.avg:.2f}",
                dist=dist,
                by_day=by_day_text or "нет",
                trend=agg.trend,
            )
            raw = await provider.chat(
                [ChatMessage(role="user", content=prompt)],
                heavy=False,
            )
            lines.append(f"\n{raw.strip()[:700]}")
        except Exception:
            logger.exception("team_mood LLM reco failed")

    await wait.edit_text("\n".join(lines), parse_mode="HTML")
