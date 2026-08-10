from dataclasses import dataclass
from typing import Literal, Protocol


Role = Literal["system", "user", "assistant"]


@dataclass
class ChatMessage:
    role: Role
    content: str


class LLMError(Exception):
    """Базовая ошибка LLM-провайдера."""


class RateLimitedError(LLMError):
    """429: превышен лимит запросов. retry_after — Retry-After из ответа (сек)."""

    def __init__(
        self,
        retry_after: float | None = None,
        message: str = "rate limited",
    ):
        self.retry_after = retry_after
        super().__init__(message)


class AuthError(LLMError):
    """401/403: API-ключ недействителен или нет доступа."""


class InvalidRequestError(LLMError):
    """400: некорректный запрос (модель/параметры) — retry бесполезен."""


class ServerError(LLMError):
    """5xx: ошибка на стороне провайдера."""


class ProviderTimeoutError(LLMError):
    """Timeout или сетевая ошибка."""


def parse_retry_after(value: object) -> float | None:
    """Парсит Retry-After в секундах. HTTP-date не поддерживается — None."""
    if value is None:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


class LLMProvider(Protocol):
    name: str

    async def validate_key(self) -> bool:
        """Лёгкий запрос: подходит ли ключ. Используется в /settings."""

    async def chat(self, messages: list[ChatMessage], *, heavy: bool = False) -> str:
        ...

    async def embed(self, text: str) -> list[float]:
        ...