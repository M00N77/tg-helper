from sqlalchemy import select

from src.db.models import Team, TeamMember
from src.db.repo import get_team_by_chat
from src.db.session import get_session


async def get_role(chat_id: int, telegram_id: int) -> str | None:
    async with get_session() as session:
        team = await get_team_by_chat(session, chat_id)
        if not team:
            return None
        result = await session.execute(
            select(TeamMember).where(
                TeamMember.team_id == team.id,
                TeamMember.telegram_id == telegram_id,
            )
        )
        member = result.scalar_one_or_none()
        return member.role if member else None


async def is_admin(chat_id: int, telegram_id: int) -> bool:
    return await get_role(chat_id, telegram_id) == "admin"


async def team_exists(chat_id: int) -> bool:
    async with get_session() as session:
        team = await get_team_by_chat(session, chat_id)
        return team is not None
