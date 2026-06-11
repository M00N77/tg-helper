"""Шедулер групповых активностей (пульс-опросы и т.п.).

Построен по образцу src/core/standup_scheduler.py: раз в минуту проверяет, не
наступило ли время запуска для каждой команды (сравнение HH:MM в TZ команды),
с защитой от повторного запуска в тот же день. Запускает активность из реестра
(registry.DEFAULT_SCHEDULED_ACTIVITY).
"""
import asyncio
import logging

from aiogram import Bot
from sqlalchemy import select

from src.bot.app import get_bot
from src.core.timeutil import now_in_tz
from src.db.models import Team
from src.db.repo import create_activity_session, set_activity_message_id
from src.db.session import get_session
from src.group_bot.activities.registry import DEFAULT_SCHEDULED_ACTIVITY, get_activity

logger = logging.getLogger(__name__)

# TZ команд для расписания (как в standup_scheduler — Europe/Moscow для отдела).
TEAM_TZ = "Europe/Moscow"


async def _launch_pulse(bot: Bot, team: "Team") -> None:
    """Создаёт сессию активности, постит вопрос с кнопками и сохраняет message_id."""
    plugin = get_activity(DEFAULT_SCHEDULED_ACTIVITY)
    if plugin is None:
        return

    question = plugin.build_question()

    async with get_session() as session:
        act = await create_activity_session(
            session,
            team_id=team.id,
            activity_code=plugin.code,
            kind=plugin.kind,
            is_anonymous=plugin.is_anonymous,
            chat_id=team.chat_id,
            question=question,
        )
        session_id = act.id

    try:
        msg = await bot.send_message(
            chat_id=team.chat_id,
            text=question,
            reply_markup=plugin.build_keyboard(session_id),
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Failed to post pulse poll for team %s", team.id)
        return

    async with get_session() as session:
        await set_activity_message_id(session, session_id, msg.message_id)


async def activities_scheduler_loop() -> None:
    """Каждую минуту проверяет, пора ли запускать активность для каждой команды."""
    last_run: dict[int, str] = {}  # team_id -> "YYYY-MM-DD"
    while True:
        try:
            bot = get_bot()
            if bot is not None:
                async with get_session() as session:
                    teams = list((await session.execute(select(Team))).scalars().all())

                for team in teams:
                    if not team.activities_enabled:
                        continue
                    local_now = now_in_tz(TEAM_TZ)
                    current_hm = local_now.strftime("%H:%M")
                    current_day = local_now.strftime("%Y-%m-%d")
                    if local_now.weekday() >= 5:  # выходные пропускаем
                        continue
                    if team.pulse_time == current_hm and last_run.get(team.id) != current_day:
                        await _launch_pulse(bot, team)
                        last_run[team.id] = current_day
        except Exception:
            logger.exception("activities scheduler tick failed")
        await asyncio.sleep(60)
