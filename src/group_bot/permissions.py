"""Проверки прав участников команды в групповом чате.

Роль определяется по двум источникам:
- Team.owner_telegram_id — директор команды (всегда admin).
- TeamMember.role == "admin" — назначенные руководители.
Остальные участники — "member".
"""
from src.db.repo import get_team_by_chat, get_team_member
from src.db.session import get_session


async def get_role(chat_id: int, telegram_id: int) -> str:
    """Возвращает роль пользователя в команде по chat_id: 'admin' | 'member' | 'none'."""
    async with get_session() as session:
        team = await get_team_by_chat(session, chat_id)
        if team is None:
            return "none"
        if team.owner_telegram_id and team.owner_telegram_id == telegram_id:
            return "admin"
        member = await get_team_member(session, team.id, telegram_id)
        if member is None:
            return "none"
        return member.role or "member"


async def is_admin(chat_id: int, telegram_id: int) -> bool:
    """True, если пользователь — директор команды или назначенный руководитель."""
    return await get_role(chat_id, telegram_id) == "admin"
