"""Инвайт пользователей в Telegram-группу через userbot (Telethon)."""
import logging
from typing import TYPE_CHECKING

from telethon.errors import (
    ChatAdminRequiredError,
    UserAlreadyParticipantError,
    UserIdInvalidError,
    UserPrivacyRestrictedError,
)
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.tl.functions.contacts import ResolveUsernameRequest

from src.config import settings
from src.core.notifier import notifier
from src.db.repo import add_team_member, get_team_by_chat
from src.db.session import get_session

if TYPE_CHECKING:
    from src.userbot.manager import UserbotManager

logger = logging.getLogger(__name__)


async def resolve_username(userbot_manager: "UserbotManager", username: str) -> int | None:
    """
    Получает telegram_id по @username через Telethon.
    Возвращает telegram_id или None если не найден.
    """
    client = userbot_manager.get_client(settings.owner_telegram_id)
    if client is None:
        logger.warning("No userbot client available for username resolve")
        return None
    try:
        username = username.lstrip("@").strip()
        result = await client(ResolveUsernameRequest(username))
        if result and result.users:
            return result.users[0].id
        return None
    except Exception:
        logger.exception("Failed to resolve username @%s", username)
        return None


async def invite_user_to_team(
    userbot_manager: "UserbotManager",
    *,
    chat_id: int,
    team_id: int,
    username: str,
    invited_by: int,
) -> str:
    """
    Полный цикл инвайта:
    1. Резолвит @username → telegram_id через userbot
    2. Добавляет в Telegram-группу через InviteToChannelRequest
    3. Добавляет в team_members в БД
    4. Уведомляет пользователя в ЛС

    Возвращает строку-статус для ответа боту.
    """
    # 1. Резолвим username
    telegram_id = await resolve_username(userbot_manager, username)
    if telegram_id is None:
        return (
            f"❌ Не удалось найти пользователя @{username}.\n"
            f"Проверь username — он должен быть публичным."
        )

    # 2. Пытаемся добавить в группу через userbot
    client = userbot_manager.get_client(settings.owner_telegram_id)
    added_to_group = False

    if client is not None:
        try:
            await client(InviteToChannelRequest(channel=chat_id, users=[telegram_id]))
            added_to_group = True
        except UserAlreadyParticipantError:
            added_to_group = True  # уже в группе — это ок
        except (ChatAdminRequiredError, UserPrivacyRestrictedError) as e:
            logger.warning("Cannot invite %s via userbot: %s", username, e)
        except UserIdInvalidError:
            return f"❌ Пользователь @{username} недоступен для приглашения."
        except Exception:
            logger.exception("InviteToChannelRequest failed for @%s", username)

    # 3. Добавляем в team_members в БД
    async with get_session() as session:
        try:
            await add_team_member(
                session,
                team_id=team_id,
                telegram_id=telegram_id,
                role="member",
            )
        except Exception:
            logger.exception("add_team_member failed for telegram_id=%s", telegram_id)

    # 4. Уведомляем пользователя в ЛС
    if added_to_group:
        await notifier.notify_user(
            telegram_id,
            "🎉 Вас добавили в команду! Напишите /start чтобы начать работу с ботом.",
        )
        return f"✅ @{username} добавлен в команду и в группу."
    else:
        # Fallback: генерируем invite link через Bot API
        try:
            from src.bot.app import get_bot
            bot = get_bot()
            if bot:
                link = await bot.create_chat_invite_link(
                    chat_id=chat_id, member_limit=1
                )
                await notifier.notify_user(
                    telegram_id,
                    f"🔗 Вас приглашают в команду!\n"
                    f"Перейдите по ссылке: {link.invite_link}",
                )
                return (
                    f"⚠️ @{username} найден, но не удалось добавить напрямую.\n"
                    f"Отправил ссылку-приглашение в ЛС."
                )
        except Exception:
            logger.exception("create_chat_invite_link failed")
        return (
            f"⚠️ @{username} добавлен в БД, но не смог добавить в группу автоматически.\n"
            f"Попросите его вступить в группу вручную."
        )
