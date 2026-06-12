"""Ежедневный статус-опрос шедулер и эскалация подвисших задач."""
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select

from src.bot.app import get_bot
from src.core.timeutil import now_in_tz
from src.db.models import Team, TeamMember, Blocker
from src.db.session import get_session

logger = logging.getLogger(__name__)

STANDUP_TEXT = (
    "☀ <b>Ежедневный статус-опрос — {date}</b>\n\n"
    "@all, напишите в ответ на это сообщение:\n"
    "• Что сделано вчера?\n"
    "• Что планируете сегодня?\n"
    "• Есть ли подвисшие задачи?\n\n"
    "<i>(или просто напишите текстом — я сам разберу)</i>"
)

ESCALATION_THRESHOLDS = {
    "critical": 1,
    "high": 4,
    "medium": 24,
    "low": 72,
}


async def _post_standup(bot: Bot, team: "Team") -> None:
    """Постит статус-опрос в групповой чат команды и сохраняет message_id."""
    date_str = datetime.utcnow().strftime("%d.%m.%Y")
    try:
        msg = await bot.send_message(
            chat_id=team.chat_id,
            text=STANDUP_TEXT.format(date=date_str),
            parse_mode="HTML",
        )
        async with get_session() as session:
            t = await session.get(Team, team.id)
            if t:
                t.standup_msg_id = msg.message_id
    except Exception:
        logger.exception("Failed to post standup for team %s", team.id)


async def standup_scheduler_loop() -> None:
    """Каждую минуту проверяет, пора ли постить статус-опрос для каждой команды."""
    last_sent: dict[int, str] = {}  # team_id -> "YYYY-MM-DD"
    while True:
        try:
            bot = get_bot()
            if bot is not None:
                async with get_session() as session:
                    teams = list((await session.execute(select(Team))).scalars().all())
                for team in teams:
                    if not team.standup_enabled:
                        continue
                    local_now = now_in_tz("Europe/Moscow")
                    current_hm = local_now.strftime("%H:%M")
                    current_day = local_now.strftime("%Y-%m-%d")
                    weekday = local_now.weekday()
                    if weekday >= 5:
                        continue
                    if (
                        team.standup_time == current_hm
                        and last_sent.get(team.id) != current_day
                    ):
                        await _post_standup(bot, team)
                        last_sent[team.id] = current_day
        except Exception:
            logger.exception("standup scheduler tick failed")
        await asyncio.sleep(60)


async def blocker_escalation_loop() -> None:
    """Каждые 30 минут пингует незакрытые подвисшие задачи."""
    while True:
        try:
            bot = get_bot()
            if bot is not None:
                now = datetime.utcnow()
                async with get_session() as session:
                    result = await session.execute(
                        select(Blocker).where(Blocker.status == "open")
                    )
                    open_blockers = list(result.scalars().all())

                for b in open_blockers:
                    threshold_h = ESCALATION_THRESHOLDS.get(b.severity, 24)
                    age_h = (now - b.created_at).total_seconds() / 3600
                    if age_h < threshold_h:
                        continue
                    async with get_session() as session:
                        team = await session.get(Team, b.team_id)
                    if not team:
                        continue
                    icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(b.severity, "⚠️")
                    try:
                        await bot.send_message(
                            chat_id=team.chat_id,
                            text=(
                                f"{icon} <b>Эскалация задачи #{b.id}</b>\n"
                                f"от {b.display_name}: {b.description[:200]}\n"
                                f"Висит {age_h:.0f}ч · /blocker_resolve {b.id}"
                            ),
                            parse_mode="HTML",
                        )
                    except Exception:
                        logger.exception("Failed to escalate blocker %s", b.id)
        except Exception:
            logger.exception("blocker escalation tick failed")
        await asyncio.sleep(1800)
