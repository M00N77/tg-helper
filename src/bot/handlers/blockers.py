"""Блокеры команды: /blockers, /blocker_resolve, /blocker_dismiss."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.filters import OwnerOrTeamMember
from src.db.repo import (
    dismiss_blocker,
    get_open_blockers,
    get_team_by_chat,
    resolve_blocker,
)
from src.db.session import get_session

router = Router(name="blockers")
router.message.filter(OwnerOrTeamMember())

SEVERITY_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}


@router.message(Command("blockers"))
async def cmd_blockers(message: Message) -> None:
    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if not team:
            await message.answer("❌ Команда не найдена.")
            return
        open_b = await get_open_blockers(session, team.id)

    if not open_b:
        await message.answer("✅ Открытых блокеров нет.")
        return

    lines = ["🚧 <b>Блокеры команды</b>\n"]
    for b in open_b:
        icon = SEVERITY_ICON.get(b.severity, "⚠️")
        from datetime import datetime
        age_h = (datetime.utcnow() - b.created_at).total_seconds() / 3600
        lines.append(
            f"{icon} <b>#{b.id}</b> {b.display_name}: {b.description[:150]}"
            f"\n   <i>Висит {age_h:.0f}ч</i>"
            f" · /blocker_resolve_{b.id} · /blocker_dismiss_{b.id}"
        )

    await message.answer("\n\n".join(lines), parse_mode="HTML")


@router.message(Command(commands=["blocker_resolve"]))
async def cmd_blocker_resolve(message: Message) -> None:
    args = (message.text or "").split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Использование: /blocker_resolve <id>")
        return
    blocker_id = int(args[1])
    async with get_session() as session:
        ok = await resolve_blocker(session, blocker_id)
    if ok:
        await message.answer(f"✅ Блокер #{blocker_id} закрыт.")
    else:
        await message.answer(f"❌ Блокер #{blocker_id} не найден.")


@router.message(Command(commands=["blocker_dismiss"]))
async def cmd_blocker_dismiss(message: Message) -> None:
    args = (message.text or "").split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Использование: /blocker_dismiss <id>")
        return
    blocker_id = int(args[1])
    async with get_session() as session:
        ok = await dismiss_blocker(session, blocker_id)
    if ok:
        await message.answer(f"🗑 Блокер #{blocker_id} отклонён.")
    else:
        await message.answer(f"❌ Блокер #{blocker_id} не найден.")
