from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.repo import get_team_by_chat, get_recent_risks
from src.db.session import get_session
from src.group_bot.filters import GroupOnly

router = Router(name="risks")
router.message.filter(GroupOnly())


@router.message(Command("risks"))
async def cmd_risks(message: Message) -> None:
    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if not team:
            await message.answer("Команда не настроена.")
            return
        risks = await get_recent_risks(session, team.id, limit=10)

    if not risks:
        await message.answer("✅ Рисков за последнее время не обнаружено.")
        return

    lines = [f"🚨 <b>Последние риски команды</b> ({len(risks)}):\n"]
    for r in risks:
        lines.append(
            f"• <b>{r.display_name}</b>: {r.risk_reason}\n"
            f"  <i>{r.created_at.strftime('%d.%m %H:%M')}</i>"
        )
    await message.answer("\n".join(lines))
