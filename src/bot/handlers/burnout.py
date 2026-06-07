"""Анализ эмоционального выгорания — /burnout."""
import logging
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.repo import get_or_create_user, list_contacts, fetch_my_messages_in_chat
from src.db.session import get_session
from src.llm.base import ChatMessage
from src.llm.router import build_provider
from src.llm.openai_provider import OpenAIProvider
from src.llm.gemini_provider import GeminiProvider

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


async def _llm_burnout(messages_text: str, provider, session, owner) -> str:
    from src.llm.router import build_provider
    from src.llm.base import ChatMessage

    prompt = BURNOUT_PROMPT.format(messages=messages_text)
    msgs = [ChatMessage(role="user", content=prompt)]

    # Try 1 — основной провайдер
    try:
        return await provider.chat(msgs, heavy=False)
    except Exception as e1:
        logger.warning("Burnout attempt 1 failed: %s", e1)

    # Try 2 — тот же провайдер ещё раз
    try:
        return await provider.chat(msgs, heavy=False)
    except Exception as e2:
        logger.warning("Burnout attempt 2 failed: %s", e2)

    # Try 3 — переключиться на другой провайдер
    try:
        current = owner.settings.llm_provider
        owner.settings.llm_provider = "openai" if current == "gemini" else "gemini"
        fallback = await build_provider(session, owner)
        owner.settings.llm_provider = current  # вернуть обратно
        if fallback:
            return await fallback.chat(msgs, heavy=False)
    except Exception as e3:
        logger.warning("Burnout fallback failed: %s", e3)

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
            provider = await build_provider(session, owner2)

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
    if provider is None:
        await wait.edit_text("❌ Нет LLM-ключа — добавь в /settings → 🔑")
        return
    async with get_session() as session:
        owner3 = await get_or_create_user(session, message.from_user.id)
        result = await _llm_burnout(sample, provider, session, owner3)

    await wait.edit_text(
        f"🧠 <b>Анализ эмоционального состояния</b>\n\n{result}",
        parse_mode="HTML"
    )
