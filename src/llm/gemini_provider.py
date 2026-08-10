import asyncio

import httpx
from google import genai
from google.genai import errors as genai_errors

from src.config import LLMDefaults
from src.llm.base import (
    AuthError,
    ChatMessage,
    InvalidRequestError,
    LLMError,
    ProviderTimeoutError,
    RateLimitedError,
    ServerError,
    parse_retry_after,
)


def _to_gemini_contents(messages: list[ChatMessage]) -> tuple[str | None, list[dict]]:
    """Возвращает (system_instruction, contents) для google-genai."""
    system_chunks: list[str] = []
    contents: list[dict] = []
    for m in messages:
        if m.role == "system":
            system_chunks.append(m.content)
        else:
            role = "model" if m.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.content}]})
    system = "\n\n".join(system_chunks) if system_chunks else None
    return system, contents


def translate_gemini_error(exc: Exception) -> LLMError:
    """Переводит SDK-исключения google-genai в иерархию LLMError."""
    if isinstance(exc, genai_errors.APIError):
        code = exc.code if isinstance(exc.code, int) else None
        retry_after = None
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) if response is not None else None
        if headers is not None:
            retry_after = parse_retry_after(headers.get("retry-after"))

        if code == 429:
            return RateLimitedError(retry_after=retry_after, message=str(exc))
        if code in (401, 403):
            return AuthError(str(exc))
        if code is not None and 400 <= code < 500:
            return InvalidRequestError(str(exc))
        if code is not None and code >= 500:
            return ServerError(str(exc))
    if isinstance(exc, httpx.TimeoutException):
        return ProviderTimeoutError(f"gemini timeout: {exc}")
    if isinstance(exc, httpx.HTTPError):
        return ProviderTimeoutError(f"gemini network error: {exc}")
    return LLMError(str(exc))


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

    async def validate_key(self) -> bool:
        def _check() -> bool:
            try:
                # пагинированный итератор; первый элемент достаточен
                next(iter(self._client.models.list()))
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_check)

    async def chat(self, messages: list[ChatMessage], *, heavy: bool = False) -> str:
        model = LLMDefaults.GEMINI_CHAT_HEAVY if heavy else LLMDefaults.GEMINI_CHAT_LIGHT
        system, contents = _to_gemini_contents(messages)

        def _call() -> str:
            config = {"system_instruction": system} if system else None
            resp = self._client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            return resp.text or ""

        try:
            return await asyncio.to_thread(_call)
        except Exception as exc:
            raise translate_gemini_error(exc) from exc

    async def embed(self, text: str) -> list[float]:
        def _call() -> list[float]:
            resp = self._client.models.embed_content(
                model=LLMDefaults.GEMINI_EMBED,
                contents=text,
            )
            return list(resp.embeddings[0].values)

        return await asyncio.to_thread(_call)