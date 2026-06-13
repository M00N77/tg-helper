"""Вечерний дайджест: задачи на завтра из Commitment и YouGile."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.config import settings as app_settings
from src.core.notifier import notifier
from src.core.timeutil import now_in_tz
from src.db.models import Commitment, Team
from src.db.repo import get_or_create_user
from src.db.session import get_session


logger = logging.getLogger(__name__)

EVENING_DIGEST_TIME = "20:00"
CHECK_INTERVAL = 60


async def _get_tomorrow_commitments() -> list[Commitment]:
    now = datetime.utcnow()
    tomorrow_start = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    tomorrow_end = tomorrow_start + timedelta(days=1)

    async with get_session() as session:
        result = await session.execute(
            select(Commitment).where(
                Commitment.status == "open",
                Commitment.deadline_at >= tomorrow_start,
                Commitment.deadline_at < tomorrow_end,
            )
        )
        return list(result.scalars().all())


async def _get_yougile_cards() -> list[dict]:
    from src.bot.handlers.yougile import YouGileClient, get_board_id

    async with get_session() as session:
        result = await session.execute(
            select(Team).where(
                Team.kanban_token.is_not(None),
                Team.kanban_board_id.is_not(None),
            )
        )
        teams = list(result.scalars().all())

    now = datetime.utcnow()
    tomorrow_start = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    tomorrow_end = tomorrow_start + timedelta(days=1)

    cards: list[dict] = []
    for team in teams:
        try:
            board_id = get_board_id(team)
            if not board_id:
                continue
            client = YouGileClient(team.kanban_token, board_id)
            columns = await client.get_columns()
            for col in columns:
                col_cards = await client.get_cards_in_column(col["id"], limit=100)
                for card in col_cards:
                    deadline_ts = card.get("deadline") or card.get("dueDate")
                    if deadline_ts and isinstance(deadline_ts, (int, float)):
                        deadline_dt = datetime.fromtimestamp(deadline_ts / 1000, tz=timezone.utc).replace(tzinfo=None)
                        if tomorrow_start <= deadline_dt < tomorrow_end:
                            cards.append(card)
        except Exception:
            logger.exception("YouGile fetch failed for team %s", team.chat_id)

    return cards


async def _build_digest_text() -> str:
    commitments = await _get_tomorrow_commitments()
    yougile_cards = await _get_yougile_cards()

    parts = ["🌙 *Вечерний дайджест задач на завтра*"]

    if commitments:
        lines = []
        for c in commitments:
            who = "Я" if c.direction == "mine" else (c.peer_name or "Они")
            lines.append(f"- *{who}*: {c.text}")
        parts.append("*Из чатов (Обязательства):*\n" + "\n".join(lines))

    if yougile_cards:
        lines = []
        for card in yougile_cards:
            title = card.get("title", "Без названия")
            lines.append(f"- {title}")
        parts.append("*В YouGile:*\n" + "\n".join(lines))

    if not commitments and not yougile_cards:
        return "На завтра задач не запланировано! Отдыхай 🚀"

    return "\n\n".join(parts)


async def send_evening_digest(owner_telegram_id: int) -> None:
    try:
        text = await _build_digest_text()
        await notifier.notify(text, parse_mode="Markdown")
    except Exception:
        logger.exception("Failed to send evening digest")


async def evening_digest_loop() -> None:
    last_sent: dict[int, str] = {}
    while True:
        try:
            owner_id = app_settings.owner_telegram_id
            async with get_session() as session:
                owner = await get_or_create_user(session, owner_id)
                tz_name = owner.settings.timezone
            local_now = now_in_tz(tz_name)
            current_hm = local_now.strftime("%H:%M")
            current_day = local_now.strftime("%Y-%m-%d")
            if current_hm == EVENING_DIGEST_TIME and last_sent.get(owner_id) != current_day:
                await send_evening_digest(owner_id)
                last_sent[owner_id] = current_day
        except Exception:
            logger.exception("evening digest loop tick failed")
        await asyncio.sleep(CHECK_INTERVAL)
