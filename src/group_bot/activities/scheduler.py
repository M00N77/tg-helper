"""Шедулер групповых активностей (пульс-опросы и т.п.).

Построен по образцу src/core/standup_scheduler.py: раз в минуту проверяет, не
наступило ли время запуска для каждой команды (сравнение HH:MM в TZ команды),
с защитой от повторного запуска в тот же день. Запускает активность из реестра
(registry.DEFAULT_SCHEDULED_ACTIVITY).

Дополнительно во втором проходе авто-закрывает опросы, у которых истекло окно
(team.pulse_auto_close_minutes), публикует итоги реплаем и помечает summary_posted.
"""
import asyncio
import logging

from aiogram import Bot
from sqlalchemy import select

from src.bot.app import get_bot
from src.core.timeutil import now_in_tz
from src.db.models import ActivitySession, Team
from src.db.repo import (
    create_activity_session,
    get_activity_responses,
    list_due_activity_sessions,
    mark_activity_summary_posted,
    set_activity_message_id,
)
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


async def post_activity_summary(bot: Bot, act: "ActivitySession") -> bool:
    """Публикует итоги активности в чат (реплаем на исходный опрос) и помечает
    её как закрытую. Возвращает True, если сводка успешно опубликована.

    Используется и авто-закрытием в loop, и ручной командой /pulse_results."""
    plugin = get_activity(act.activity_code)
    if plugin is None:
        return False

    async with get_session() as session:
        responses = await get_activity_responses(session, act.id)

    values = [r.answer_value for r in responses if r.answer_value is not None]
    texts = [r.answer_text for r in responses if r.answer_text]
    summary = plugin.summarize(values, texts)

    try:
        await bot.send_message(
            chat_id=act.chat_id,
            text=summary,
            reply_to_message_id=act.telegram_message_id,
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Failed to post activity summary for session %s", act.id)
        return False

    async with get_session() as session:
        await mark_activity_summary_posted(session, act.id)
    return True


async def activities_scheduler_loop() -> None:
    """Каждую минуту: 1) запускает активности по расписанию, 2) авто-закрывает
    просроченные опросы и постит итоги."""
    last_run: dict[int, str] = {}  # team_id -> "YYYY-MM-DD"
    while True:
        try:
            bot = get_bot()
            if bot is not None:
                # ── 1. Запуск по расписанию ──
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

                # ── 2. Авто-итоги по истечении окна ──
                async with get_session() as session:
                    due = await list_due_activity_sessions(session)
                for act, _team in due:
                    await post_activity_summary(bot, act)
        except Exception:
            logger.exception("activities scheduler tick failed")
        await asyncio.sleep(60)
