import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import TeamMember
from src.db.repo import get_role_permissions, init_default_permissions


logger = logging.getLogger(__name__)


async def check_user_permission(
    intent_name: str,
    member: TeamMember,
    session: AsyncSession,
) -> bool:
    perms = await get_role_permissions(session, member.team_id, member.role)
    allowed = perms.get("allowed_intents") or []
    denied = perms.get("denied_intents") or []

    # Если permissions не настроены для команды — инициализируем дефолтные
    if not allowed and not denied:
        try:
            await init_default_permissions(session, member.team_id)
            perms = await get_role_permissions(session, member.team_id, member.role)
            allowed = perms.get("allowed_intents") or []
            denied = perms.get("denied_intents") or []
        except Exception:
            logger.exception(
                "check_user_permission: init_default failed for team=%s",
                member.team_id,
            )
            return True  # fail open — таблица ещё не создана миграцией

    if "*" in allowed:
        return True

    if intent_name in denied:
        return False

    if intent_name in allowed:
        return True

    return False
