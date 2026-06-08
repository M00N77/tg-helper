"""Напоминания о Commitment'ах: пинги об overdue и о приближении дедлайна.

Overdue напоминания повторяются каждые RE_REMINDER_HOURS, пока задача не закрыта.
Lead-напоминания (скоро дедлайн) — одноразовые.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from src.config import settings as app_settings
from src.core.notifier import notifier
from src.core.timeutil import fmt_local
from src.db.models import Commitment
from src.db.repo import get_or_create_user
from src.db.session import get_session


logger = logging.getLogger(__name__)


REMINDER_TICK_SECONDS = 300
RE_REMINDER_HOURS = 6


async def _check_once(owner_telegram_id: int) -> None:
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)
        s = owner.settings
        if not s.reminders_enabled:
            return

        lead_hours = max(0, int(s.reminder_lead_hours))
        now = datetime.utcnow()
        soon = now + timedelta(hours=lead_hours)

        result = await session.execute(
            select(Commitment).where(
                Commitment.user_id == owner.id,
                Commitment.status == "open",
                Commitment.deadline_at.is_not(None),
            )
        )
        open_items = list(result.scalars().all())

    if not open_items:
        return

    re_remind = timedelta(hours=RE_REMINDER_HOURS)
    to_remind: list[tuple[Commitment, str]] = []
    for c in open_items:
        d = c.deadline_at
        if d is None:
            continue
        if d < now and s.reminder_overdue_enabled:
            if c.last_reminded_at is None or (now - c.last_reminded_at) > re_remind:
                to_remind.append((c, "overdue"))
        elif now <= d <= soon and lead_hours > 0:
            if c.last_reminded_at is None:
                to_remind.append((c, "lead"))

    if not to_remind:
        return

    tz_name = s.timezone
    for commitment, reason in to_remind:
        who = "Я" if commitment.direction == "mine" else (commitment.peer_name or "Они")
        d = fmt_local(commitment.deadline_at, tz_name)
        if reason == "overdue":
            text = (
                f"⏰ <b>Просрочено</b>\n"
                f"<b>{who}</b>: {commitment.text}\n"
                f"Срок был: {d}"
            )
        else:
            text = (
                f"⏳ <b>Скоро дедлайн</b>\n"
                f"<b>{who}</b>: {commitment.text}\n"
                f"До: {d}"
            )
        await notifier.notify(text)

    async with get_session() as session:
        for c, _ in to_remind:
            commitment = await session.get(Commitment, c.id)
            if commitment is not None:
                commitment.last_reminded_at = now


async def reminders_loop() -> None:
    while True:
        try:
            await _check_once(app_settings.owner_telegram_id)
        except Exception:
            logger.exception("reminders tick failed")
        await asyncio.sleep(REMINDER_TICK_SECONDS)
