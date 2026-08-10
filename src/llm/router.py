import asyncio
import logging
import random
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.db.repo import get_api_key
from src.llm.base import (
    AuthError,
    ChatMessage,
    InvalidRequestError,
    LLMError,
    LLMProvider,
    ProviderTimeoutError,
    RateLimitedError,
    ServerError,
)
from src.llm.complexity import Tier, resolve_tier
from src.llm.gemini_provider import GeminiProvider
from src.llm.gigachat_provider import GigaChatProvider
from src.llm.groq_provider import GroqProvider
from src.llm.openai_provider import OpenAIProvider


logger = logging.getLogger(__name__)

MAX_RETRIES = 2
BASE_DELAY = 1.0
MAX_DELAY = 30.0
PROVIDER_TIMEOUT = 20.0

# Цепочки: от быстрых/дешёвых к тяжёлым (актуальность — по замерам latency/стоимости).
TIER_CHAINS: dict[Tier, list[str]] = {
    Tier.LIGHT: ["groq", "gemini", "openai", "gigachat"],
    Tier.HEAVY: ["openai", "gemini", "gigachat", "groq"],
}

# Только эти ошибки имеют смысл ретраить.
_RETRYABLE: tuple[type[LLMError], ...] = (
    RateLimitedError,
    ServerError,
    ProviderTimeoutError,
)

_PROVIDER_LABELS = {
    "openai": "OpenAI",
    "gemini": "Gemini",
    "groq": "Groq",
    "gigachat": "GigaChat",
}


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


def _provider_label(name: str) -> str:
    return _PROVIDER_LABELS.get(name, name.capitalize())


async def build_provider(session: AsyncSession, user: User) -> LLMProvider | None:
    """Создаёт провайдера согласно настройкам пользователя. None — если ключ не задан."""
    provider_name = user.settings.llm_provider if user.settings else "openai"
    key = await get_api_key(session, user, provider_name)
    if not key:
        return None
    return _create_single_provider(provider_name, key)


async def get_provider_chain(
    session: AsyncSession,
    user: User,
    tier: Tier | None = None,
) -> list[LLMProvider]:
    """Все доступные провайдеры в порядке цепочки tier.

    Активный провайдер (settings.llm_provider) идёт первым, остальные —
    по TIER_CHAINS[<tier>]. Без tier — HEAVY-цепочка.
    Ключи грузятся последовательно: get_api_key() использует один
    AsyncSession, параллельный доступ к нему небезопасен.
    """
    chain = TIER_CHAINS[tier if tier is not None else Tier.HEAVY]
    active = user.settings.llm_provider if user.settings else "openai"

    order = [active] + [name for name in chain if name != active]

    providers: list[LLMProvider] = []
    for name in order:
        key = await get_api_key(session, user, name)
        if key:
            providers.append(_create_single_provider(name, key))

    return providers


def _backoff_delay(attempt: int, retry_after: float | None = None) -> float:
    """Exponential backoff + jitter. Для 429 приоритет у Retry-After."""
    if retry_after is not None:
        return retry_after
    delay = min(BASE_DELAY * (2**attempt), MAX_DELAY)
    return delay + random.uniform(0, delay * 0.1)


async def _call_with_retry(
    provider: LLMProvider,
    messages: list[ChatMessage],
    *,
    heavy: bool,
    **kwargs: Any,
) -> tuple[str | None, LLMError | None]:
    """Вызывает provider.chat с retry-политикой. Возвращает (result, error).

    - 400/401/403 и прочие перманентные ошибки: сразу (None, err), без retry
    - 429/5xx/timeout/network: retry с backoff+jitter, уважая Retry-After
    - успех: (text, None)
    """
    last_error: LLMError | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            result = await asyncio.wait_for(
                provider.chat(messages, heavy=heavy, **kwargs),
                timeout=PROVIDER_TIMEOUT,
            )
            return result, None
        except asyncio.TimeoutError:
            last_error = ProviderTimeoutError(
                f"provider {provider.name} timed out after {PROVIDER_TIMEOUT}s"
            )
        except LLMError as exc:
            last_error = exc
            if not isinstance(exc, _RETRYABLE):
                return None, exc
        except Exception as exc:
            last_error = LLMError(f"unexpected error: {exc}")
            logger.exception("Provider %s unexpected error", provider.name)
            return None, last_error

        if attempt < MAX_RETRIES:
            delay = _backoff_delay(
                attempt,
                retry_after=getattr(last_error, "retry_after", None),
            )
            # Структурированная строка лога, например:
            #   openai: 429 attempt=1/2 retry_after=2.1 retrying in 2.1s
            retry_after = (
                getattr(last_error, "retry_after", None)
                if isinstance(last_error, RateLimitedError)
                else None
            )
            retry_after_part = (
                f" retry_after={retry_after:.1f}" if retry_after is not None else ""
            )
            logger.warning(
                "%s: %s attempt=%d/%d%s retrying in %.1fs",
                provider.name, _error_slug(last_error),
                attempt + 1, MAX_RETRIES, retry_after_part, delay,
            )
            await asyncio.sleep(delay)

    return None, last_error


def _human_reason(err: LLMError | None) -> str:
    if isinstance(err, AuthError):
        return "API key invalid"
    if isinstance(err, InvalidRequestError):
        return "invalid request (model/params)"
    if isinstance(err, RateLimitedError):
        return "rate limited"
    if isinstance(err, ProviderTimeoutError):
        return "timeout"
    if isinstance(err, ServerError):
        return "server error"
    return "unavailable"


def _error_slug(err: LLMError) -> str:
    """Короткий статус ошибки для структурированного лога retry."""
    if isinstance(err, RateLimitedError):
        return "429"
    if isinstance(err, AuthError):
        return "auth"
    if isinstance(err, InvalidRequestError):
        return "400"
    if isinstance(err, ServerError):
        return "5xx"
    if isinstance(err, ProviderTimeoutError):
        return "timeout"
    return "error"


def _switch_message(name: str, err: LLMError, next_name: str | None = None) -> str:
    """Сообщение пользователю при переключении провайдера.

    Отправляется один раз на каждый переключённый провайдер —
    детали ретраев остаются в серверных логах.
    """
    next_part = (
        f"Переключаюсь на {next_name}..."
        if next_name
        else "Переключаюсь на следующего провайдера..."
    )
    if isinstance(err, AuthError):
        return (
            f"⚠️ {name}: API-ключ недействителен.\n"
            f"Замените ключ в настройках.\n\n"
            f"{next_part}"
        )
    if isinstance(err, InvalidRequestError):
        return (
            f"⚠️ {name} недоступен: провайдер вернул ошибку 400.\n"
            f"Проверьте и замените API-ключ {name}.\n\n"
            f"{next_part}"
        )
    return (
        f"⚠️ {name} временно недоступен.\n"
        f"{next_part}"
    )


async def _notify(notify_bot: Any, notify_chat_id: int | None, text: str) -> None:
    if notify_bot is None or notify_chat_id is None:
        return
    try:
        await notify_bot.send_message(notify_chat_id, text)
    except Exception:
        # сбой уведомления не должен ломать роутер
        logger.exception("Failed to send LLM fallback notification")


async def llm_with_fallback(
    providers: list[LLMProvider],
    messages: list[ChatMessage],
    *,
    heavy: bool | None = None,
    notify_bot: Any = None,
    notify_chat_id: int | None = None,
    **kwargs: Any,
) -> str:
    """Пробует провайдеров по очереди (цепочка tier) с retry-логикой.

    heavy=None → tier определяется эвристикой по содержимому messages.
    При переключении уведомляет, если передан notify_bot + notify_chat_id.
    Когда провайдеры кончились — RuntimeError с понятным отчётом по каждому.
    """
    if heavy is None:
        heavy = resolve_tier(messages) == Tier.HEAVY

    errors: list[tuple[str, str]] = []

    for idx, provider in enumerate(providers):
        result, err = await _call_with_retry(provider, messages, heavy=heavy, **kwargs)
        if result is not None:
            return result

        errors.append((_provider_label(provider.name), _human_reason(err)))
        if idx < len(providers) - 1:
            next_label = _provider_label(providers[idx + 1].name)
            await _notify(
                notify_bot,
                notify_chat_id,
                _switch_message(_provider_label(provider.name), err, next_label),
            )

    report = "❌ Все доступные LLM-провайдеры недоступны.\n\n" + "\n".join(
        f"{name} — {reason}" for name, reason in errors
    )
    logger.error(report)
    raise RuntimeError(report)