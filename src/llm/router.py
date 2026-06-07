from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.db.repo import get_api_key
from src.llm.base import LLMProvider
from src.llm.gemini_provider import GeminiProvider
from src.llm.gigachat_provider import GigaChatProvider
from src.llm.openai_provider import OpenAIProvider


async def build_provider(session: AsyncSession, user: User) -> LLMProvider | None:
    """Создаёт провайдера согласно настройкам пользователя. None — если ключ не задан."""
    provider_name = user.settings.llm_provider if user.settings else "openai"
    key = await get_api_key(session, user, provider_name)
    if not key:
        return None
    if provider_name == "openai":
        return OpenAIProvider(key)
    if provider_name == "gemini":
        return GeminiProvider(key)
    if provider_name == "gigachat":
        return GigaChatProvider(key)
    return None
