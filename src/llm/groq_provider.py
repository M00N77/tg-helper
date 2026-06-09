import logging

from openai import AsyncOpenAI

from src.config import LLMDefaults
from src.llm.base import ChatMessage


logger = logging.getLogger(__name__)

_GROQ_LIGHT_FALLBACK: list[str] = [
    "llama-3.1-8b-instant",
]

_GROQ_HEAVY_FALLBACK: list[str] = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
]


class GroqProvider:
    name = "groq"

    def __init__(self, api_key: str) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
        )

    async def validate_key(self) -> bool:
        try:
            await self._client.models.list()
            return True
        except Exception:
            return False

    async def chat(self, messages: list[ChatMessage], *, heavy: bool = False) -> str:
        primary = LLMDefaults.GROQ_CHAT_HEAVY if heavy else LLMDefaults.GROQ_CHAT_LIGHT
        fallback = _GROQ_HEAVY_FALLBACK if heavy else _GROQ_LIGHT_FALLBACK
        models_to_try = [primary] + fallback

        last_error: Exception | None = None
        for model in models_to_try:
            try:
                resp = await self._client.chat.completions.create(
                    model=model,
                    messages=[{"role": m.role, "content": m.content} for m in messages],
                )
                return resp.choices[0].message.content or ""
            except Exception as e:
                logger.warning("Groq model %s failed: %s", model, e)
                last_error = e
        raise last_error  # type: ignore[misc]

    async def embed(self, text: str) -> list[float]:
        resp = await self._client.embeddings.create(
            model=LLMDefaults.GROQ_EMBED,
            input=text,
        )
        return resp.data[0].embedding
