from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from openai import AsyncOpenAI

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


def translate_openai_error(exc: Exception) -> LLMError:
    """Переводит SDK-исключения openai в иерархию LLMError."""
    if isinstance(exc, APITimeoutError):
        return ProviderTimeoutError(f"openai timeout: {exc}")
    if isinstance(exc, APIConnectionError):
        return ProviderTimeoutError(f"openai connection error: {exc}")
    if isinstance(exc, RateLimitError):
        retry_after = parse_retry_after(exc.headers.get("retry-after")) if exc.headers else None
        return RateLimitedError(retry_after=retry_after, message=str(exc))
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return AuthError(str(exc))
    if isinstance(exc, (BadRequestError, UnprocessableEntityError, NotFoundError)):
        return InvalidRequestError(str(exc))
    if isinstance(exc, InternalServerError):
        return ServerError(str(exc))
    if isinstance(exc, APIStatusError):
        code = exc.status_code
        retry_after = parse_retry_after(exc.headers.get("retry-after")) if exc.headers else None
        if code == 429:
            return RateLimitedError(retry_after=retry_after, message=str(exc))
        if code in (401, 403):
            return AuthError(str(exc))
        if code in (400, 404, 422):
            return InvalidRequestError(str(exc))
        if code >= 500:
            return ServerError(str(exc))
    return LLMError(str(exc))


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)

    async def validate_key(self) -> bool:
        try:
            await self._client.models.list()
            return True
        except Exception:
            return False

    async def chat(self, messages: list[ChatMessage], *, heavy: bool = False) -> str:
        model = LLMDefaults.OPENAI_CHAT_HEAVY if heavy else LLMDefaults.OPENAI_CHAT_LIGHT
        try:
            resp = await self._client.chat.completions.create(
                model=model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:
            raise translate_openai_error(exc) from exc

    async def embed(self, text: str) -> list[float]:
        resp = await self._client.embeddings.create(model=LLMDefaults.OPENAI_EMBED, input=text)
        return resp.data[0].embedding