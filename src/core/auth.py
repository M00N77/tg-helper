from src.db.models import TeamMember
from src.db.repo import get_role_permissions
from sqlalchemy.ext.asyncio import AsyncSession


async def check_user_permission(
    intent_name: str,
    member: TeamMember,
    session: AsyncSession,
) -> bool:
    perms = await get_role_permissions(session, member.team_id, member.role)
    allowed = perms.get("allowed_intents") or []
    denied = perms.get("denied_intents") or []

    if "*" in allowed:
        return True

    if intent_name in denied:
        return False

    if intent_name in allowed:
        return True

    return False
