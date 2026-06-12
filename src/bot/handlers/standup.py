"""Статус-опрос хендлеры: /standup, /standup_status, /standup_skip и обработка ответов."""
import json
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.filters import OwnerOrTeamMember
from src.db.models import Team
from src.db.repo import (
    create_blocker,
    create_or_update_standup,
    get_or_create_user,
    get_standups_for_date,
    get_team_by_chat,
    get_team_members,
)
from src.db.session import get_session
from src.llm.base import ChatMessage
from src.llm.router import build_provider

logger = logging.getLogger(__name__)
router = Router(name="standup")
router.message.filter(OwnerOrTeamMember())

STANDUP_PARSE_PROMPT = """\
Ты парсер ежедневного статус-опроса. Из сообщения сотрудника извлеки:
1. done_today — что сделано (кратко)
2. plan_today — что планирует (кратко)
3. blockers — подвисшие задачи (если есть слова: жду, stuck, blocked, проблема, не могу, зависит) или null
4. mood — positive | neutral | negative

Верни ТОЛЬКО JSON без markdown:
{"done_today": "...", "plan_today": "...", "blockers": null, "mood": "neutral"}
"""

TODAY_START = lambda: datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


@router.message(Command("standup"))
async def cmd_standup(message: Message) -> None:
    """Вручную постит статус-опрос (для теста)."""
    from src.core.standup_scheduler import STANDUP_TEXT, _post_standup
    from src.bot.app import get_bot

    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
    if not team:
        await message.answer("❌ Это не командный чат. Сначала /team → Создать.")
        return
    bot = get_bot()
    if bot:
        await _post_standup(bot, team)


@router.message(Command("standup_status"))
async def cmd_standup_status(message: Message) -> None:
    today = TODAY_START()
    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if not team:
            await message.answer("❌ Команда не найдена.")
            return
        standups = await get_standups_for_date(session, team.id, today)
        members = await get_team_members(session, team.id)

    answered_ids = {s.user_id for s in standups}
    all_ids = {m.telegram_id for m in members}
    not_answered = all_ids - answered_ids

    blockers_count = sum(1 for s in standups if s.blockers and s.blockers != "null")

    lines = [f"☀ <b>Статус-опрос {today.strftime('%d.%m.%Y')}</b>\n"]
    if answered_ids:
        names = ", ".join(s.display_name or str(s.user_id) for s in standups)
        lines.append(f"✅ Ответили: {names}")
    if not_answered:
        lines.append(f"❌ Не ответили: {len(not_answered)} чел.")
    if blockers_count:
        lines.append(f"🚧 Подвисшие задачи: {blockers_count}")
    else:
        lines.append("✅ Подвисших задач нет")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("standup_skip"))
async def cmd_standup_skip(message: Message) -> None:
    if not message.from_user:
        return
    today = TODAY_START()
    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if not team:
            await message.answer("❌ Команда не найдена.")
            return
        name = message.from_user.full_name or str(message.from_user.id)
        await create_or_update_standup(
            session,
            team_id=team.id,
            user_id=message.from_user.id,
            display_name=name,
            date=today,
            done_today="(пропуск)",
            plan_today="",
            blockers="",
            mood="neutral",
        )
    await message.answer("✅ Статус-опрос пропущен.")


@router.message(F.reply_to_message)
async def handle_standup_reply(message: Message) -> None:
    """Перехватывает ответы на статус-опрос бота."""
    if not message.from_user or not message.text:
        raise SkipHandler

    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if not team or not team.standup_msg_id:
            # Не статус-опрос чат — пропускаем дальше (например, к согласованию задач).
            raise SkipHandler
        if message.reply_to_message.message_id != team.standup_msg_id:
            # Ответ не на статус-опрос — отдаём другим роутерам.
            raise SkipHandler

        owner = await get_or_create_user(session, message.from_user.id)
        provider = await build_provider(session, owner)

    if provider is None:
        return

    try:
        raw = await provider.chat(
            [
                ChatMessage(role="system", content=STANDUP_PARSE_PROMPT),
                ChatMessage(role="user", content=message.text),
            ],
            heavy=False,
        )
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        parsed = json.loads(raw)
    except Exception:
        logger.exception("Failed to parse standup reply")
        return

    today = TODAY_START()
    name = message.from_user.full_name or str(message.from_user.id)
    blocker_text = parsed.get("blockers") or ""

    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if not team:
            return
        s = await create_or_update_standup(
            session,
            team_id=team.id,
            user_id=message.from_user.id,
            display_name=name,
            date=today,
            done_today=parsed.get("done_today", ""),
            plan_today=parsed.get("plan_today", ""),
            blockers=blocker_text,
            mood=parsed.get("mood", "neutral"),
        )
        if blocker_text and blocker_text.lower() not in ("null", "нет", "no", ""):
            await create_blocker(
                session,
                team_id=team.id,
                reported_by=message.from_user.id,
                display_name=name,
                description=blocker_text,
                severity="medium",
                standup_id=s.id,
                telegram_message_id=message.message_id,
            )

    await message.react([])
