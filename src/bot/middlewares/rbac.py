import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from src.core.auth import check_user_permission
from src.db.repo import get_team_by_chat, get_team_member
from src.db.session import get_session

logger = logging.getLogger(__name__)


class RBACMiddleware(BaseMiddleware):
    """Проверяет права пользователя на выполнение intent-действия в групповом чате.

    Использование на роутере:
        router.message.outer_middleware(RBACMiddleware("create_task"))
        router.callback_query.outer_middleware(RBACMiddleware("create_task"))

    Если пользователь не состоит в команде или у его роли нет права — хэндлер
    не вызовется, пользователь получит уведомление.
    """

    def __init__(self, required_intent: str) -> None:
        self.required_intent = required_intent
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        chat_id: int | None = None
        user_id: int | None = None

        if isinstance(event, Message):
            chat_id = event.chat.id
            user_id = event.from_user.id if event.from_user else None
            if event.chat.type == "private":
                return await handler(event, data)
        elif isinstance(event, CallbackQuery):
            if event.message is None:
                return await handler(event, data)
            chat_id = event.message.chat.id
            user_id = event.from_user.id if event.from_user else None
            if event.message.chat.type == "private":
                return await handler(event, data)

        if chat_id is None or user_id is None:
            return await handler(event, data)

        async with get_session() as session:
            team = await get_team_by_chat(session, chat_id)
            if team is None:
                return await handler(event, data)

            member = await get_team_member(session, team.id, user_id)
            if member is None:
                if isinstance(event, Message):
                    await event.answer("⛔ Вы не являетесь участником команды.")
                else:
                    await event.answer("⛔ Вы не являетесь участником команды.", show_alert=True)
                return

            allowed = await check_user_permission(self.required_intent, member, session)
            if not allowed:
                if isinstance(event, Message):
                    await event.answer("⛔ Доступ запрещён для вашей роли.")
                else:
                    await event.answer("⛔ Доступ запрещён для вашей роли.", show_alert=True)
                return

        return await handler(event, data)
