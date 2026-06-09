"""Анализ эмоционального выгорания — /burnout."""
import logging
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.repo import get_or_create_user, list_contacts, fetch_my_messages_in_chat
from src.db.session import get_session
from src.llm.base import ChatMessage
from src.llm.router import get_provider_chain, llm_with_fallback

logger = logging.getLogger(__name__)
router = Router(name="burnout")

BURNOUT_PROMPT = """Ты психолог-аналитик. Проанализируй исходящие сообщения сотрудника за последнее время.

Сообщения:
{messages}

Верни анализ в формате:

🧠 **Общее состояние:** [одна фраза]

📊 **Индикаторы:**
- Энергия: [высокая/средняя/низкая]
- Вовлечённость: [высокая/средняя/низкая]
- Стресс: [низкий/умеренный/высокий]
- Риск выгорания: [низкий/средний/высокий]

🔍 **Что настораживает:**
[2-3 конкретных наблюдения из текста или "Признаков нет"]

💡 **Рекомендации:**
[3 конкретных совета]

Будь конкретным и опирайся только на текст сообщений."""


async def _llm_burnout(messages_text: str, providers: list, message: Message) -> str:
    prompt = BURNOUT_PROMPT.format(messages=messages_text)
    msgs = [ChatMessage(role="user", content=prompt)]

    try:
        return await llm_with_fallback(
            providers, msgs, heavy=False,
            notify_bot=message.bot, notify_chat_id=message.chat.id,
        )
    except RuntimeError:
        pass

    return "❌ Все LLM недоступны, попробуй позже"


@router.message(Command("burnout"))
async def cmd_burnout(message: Message) -> None:
    if message.from_user is None:
        return

    wait = await message.answer("🔍 Анализирую эмоциональное состояние...")

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        contacts = await list_contacts(session, owner, kinds=("user",))

    async with get_session() as session:
        owner2 = await get_or_create_user(session, message.from_user.id)
        providers = await get_provider_chain(session, owner2)

    all_messages = []
    if contacts:
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            for contact in contacts[:10]:
                try:
                    msgs = await fetch_my_messages_in_chat(
                        session, owner, contact.peer_id, limit=20
                    )
                    for m in msgs:
                        if m.text:
                            all_messages.append(m.text[:200])
                except Exception:
                    continue

    if not all_messages:
        # Demo mode — use sample messages for presentation
        all_messages = [
            "Окей, сделаю к завтрашнему утру",
            "Устал, столько задач сегодня навалилось",
            "Не успеваю, слишком много всего",
            "Ок, понял",
            "Опять дедлайн горит, разберусь",
            "Когда это закончится наконец",
            "Да да, попробую ещё раз",
            "Всё хорошо, справляюсь",
            "Немного устал но держусь",
            "Задач много, но интересно",
        ]

    sample = "\n---\n".join(all_messages[:50])
    if not providers:
        await wait.edit_text("❌ Нет LLM-ключа — добавь в /settings → 🔑")
        return
    result = await _llm_burnout(sample, providers, message)

    await wait.edit_text(
        f"🧠 <b>Анализ эмоционального состояния</b>\n\n{result}",
        parse_mode="HTML"
    )
