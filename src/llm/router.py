import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.db.repo import get_api_key
from src.llm.base import ChatMessage, LLMProvider
from src.llm.gemini_provider import GeminiProvider
from src.llm.gigachat_provider import GigaChatProvider
from src.llm.groq_provider import GroqProvider
from src.llm.openai_provider import OpenAIProvider


logger = logging.getLogger(__name__)

MAX_RETRIES = 2
FALLBACK_ORDER = ["openai", "gemini", "groq", "gigachat"]


def _create_single_provider(name: str, key: str) -> LLMProvider:
    if name == "openai":
        return OpenAIProvider(key)
    if name == "gemini":
        return GeminiProvider(key)
    if name == "gigachat":
        return GigaChatProvider(key)
    if name == "groq":
        return GroqProvider(key)
    raise ValueError(f"Unknown provider: {name}")


async def build_provider(session: AsyncSession, user: User) -> LLMProvider | None:
    """Создаёт провайдера согласно настройкам пользователя. None — если ключ не задан."""
    provider_name = user.settings.llm_provider if user.settings else "openai"
    key = await get_api_key(session, user, provider_name)
    if not key:
        return None
    return _create_single_provider(provider_name, key)


async def get_provider_chain(session: AsyncSession, user: User) -> list[LLMProvider]:
    """Возвращает всех доступных провайдеров в порядке приоритета:
    сначала активный (из settings.llm_provider), затем остальные по FALLBACK_ORDER.
    Провайдеры без API-ключа пропускаются."""
    providers: list[LLMProvider] = []
    active = user.settings.llm_provider if user.settings else "openai"

    key = await get_api_key(session, user, active)
    if key:
        providers.append(_create_single_provider(active, key))

    for name in FALLBACK_ORDER:
        if name == active:
            continue
        key = await get_api_key(session, user, name)
        if key:
            providers.append(_create_single_provider(name, key))

    return providers


async def llm_with_fallback(
    providers: list[LLMProvider],
    messages: list[ChatMessage],
    *,
    notify_bot: Any = None,
    notify_chat_id: int | None = None,
    **kwargs,
) -> str:
    """Пробует провайдеров по очереди с retry-логикой.
    При переключении отправляет уведомление если передан notify_bot + notify_chat_id."""
    last_name: str | None = None

    for provider in providers:
        if last_name is not None:
            msg = f"⚠️ Модель {last_name} недоступна, переключаюсь на {provider.name}..."
            logger.warning(msg)
            if notify_bot is not None and notify_chat_id is not None:
                try:
                    await notify_bot.send_message(notify_chat_id, msg)
                except Exception:
                    pass

        for attempt in range(MAX_RETRIES):
            try:
                return await provider.chat(messages, **kwargs)
            except Exception as e:
                logger.warning(
                    "Provider %s failed (attempt %d/%d): %s",
                    provider.name, attempt + 1, MAX_RETRIES, e,
                )
                if attempt == MAX_RETRIES - 1:
                    break
                await asyncio.sleep(1)

        last_name = provider.name

    raise RuntimeError("All LLM providers failed")
