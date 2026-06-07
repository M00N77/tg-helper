# test_fill_messages.py — запусти из корня проекта
import asyncio
from src.db.session import get_session
from src.db.repo import get_or_create_user, upsert_message

OWNER_ID = 6235799942  # число

test_messages = [
    "Окей, сделаю к завтрашнему утру",
    "Устал, столько задач сегодня",
    "Не успеваю, слишком много всего навалилось",
    "Ок",
    "Понял",
    "Сделаю",
    "Опять дедлайн горит",
    "Да да, разберусь",
    "Когда это закончится",
    "Ладно, попробую ещё раз",
]


async def fill():
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_ID)

    async with get_session() as session:
        for i, text in enumerate(test_messages):
            from datetime import datetime

            now = datetime.utcnow()
            await upsert_message(
                session,
                user_id=owner.id,
                peer_id=999999,
                message_id=i + 1,
                text=text,
                is_outgoing=True,
                kind="text",
                sender_id=owner.telegram_id,
                sender_name="",
                date=now,
            )
    print("Test messages added successfully")


asyncio.run(fill())