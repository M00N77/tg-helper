"""Тесты LLM router: retry-политика, tier routing, уведомления, error report."""
import asyncio

import pytest

from src.llm import router
from src.llm.base import (
    AuthError,
    ChatMessage,
    InvalidRequestError,
    ProviderTimeoutError,
    RateLimitedError,
    ServerError,
)
from src.llm.complexity import Tier, resolve_tier
from src.llm.router import TIER_CHAINS, _call_with_retry, llm_with_fallback


class FakeProvider:
    """Провайдер-заглушка с настраиваемым поведением."""

    def __init__(self, name: str = "fake", error: Exception | None = None, ok_after: int | None = None):
        self.name = name
        self.error = error
        self.ok_after = ok_after
        self.calls = 0

    async def chat(self, messages: list[ChatMessage], *, heavy: bool = False) -> str:
        self.calls += 1
        if self.ok_after is not None and self.calls >= self.ok_after:
            return "ok-text"
        if self.error is not None:
            raise self.error
        return "ok-text"


@pytest.fixture
def no_sleep(monkeypatch):
    """Заменяет asyncio.sleep, записывая интервалы."""
    intervals: list[float] = []

    async def _fake_sleep(seconds: float):
        intervals.append(seconds)

    monkeypatch.setattr(router.asyncio, "sleep", _fake_sleep)
    return intervals


# --- _call_with_retry -------------------------------------------------------


async def test_retry_400_no_retry():
    provider = FakeProvider(error=InvalidRequestError("bad model"))
    result, err = await _call_with_retry(provider, [ChatMessage("user", "hi")], heavy=False)
    assert result is None
    assert isinstance(err, InvalidRequestError)
    assert provider.calls == 1


async def test_retry_401_no_retry():
    provider = FakeProvider(error=AuthError("bad key"))
    result, err = await _call_with_retry(provider, [ChatMessage("user", "hi")], heavy=False)
    assert result is None
    assert isinstance(err, AuthError)
    assert provider.calls == 1


async def test_retry_429_respects_retry_after(no_sleep):
    provider = FakeProvider(error=RateLimitedError(retry_after=0.05), ok_after=2)
    result, err = await _call_with_retry(provider, [ChatMessage("user", "hi")], heavy=False)
    assert result == "ok-text"
    assert provider.calls == 2
    assert no_sleep == [0.05]


async def test_retry_429_no_retry_after_uses_backoff(no_sleep):
    provider = FakeProvider(error=RateLimitedError(retry_after=None))
    result, err = await _call_with_retry(provider, [ChatMessage("user", "hi")], heavy=False)
    assert result is None
    assert provider.calls == 3
    # attempt 0 -> ~1s, attempt 1 -> ~2s (с jitter <=10%)
    assert 1.0 <= no_sleep[0] <= 1.1
    assert 2.0 <= no_sleep[1] <= 2.2


async def test_retry_5xx_exhausts_then_fails():
    provider = FakeProvider(error=ServerError("boom"))
    result, err = await _call_with_retry(provider, [ChatMessage("user", "hi")], heavy=False)
    assert result is None
    assert isinstance(err, ServerError)
    assert provider.calls == router.MAX_RETRIES + 1


async def test_retry_timeout_wrapped(monkeypatch, no_sleep):
    class HungProvider:
        name = "hung"
        calls = 0

        async def chat(self, messages, *, heavy=False):
            HungProvider.calls += 1
            await asyncio.Future()  # никогда не завершится -> wait_for таймаутит
            return "never"

    monkeypatch.setattr(router, "PROVIDER_TIMEOUT", 0.05)
    result, err = await _call_with_retry(
        HungProvider(), [ChatMessage("user", "hi")], heavy=False,
    )
    assert result is None
    assert isinstance(err, ProviderTimeoutError)
    assert HungProvider.calls == 3  # 3 попытки с таймаутом


async def test_retry_success_first_try():
    provider = FakeProvider()
    result, err = await _call_with_retry(provider, [ChatMessage("user", "hi")], heavy=False)
    assert result == "ok-text"
    assert err is None
    assert provider.calls == 1


# --- llm_with_fallback ------------------------------------------------------


class FakeNotifier:
    def __init__(self):
        self.sent: list[str] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append(text)


async def test_fallback_switches_provider_with_auth_notification():
    notifier = FakeNotifier()
    providers = [
        FakeProvider("openai", error=AuthError("bad key")),
        FakeProvider("gemini"),
    ]
    text = await llm_with_fallback(
        providers, [ChatMessage("user", "hi")],
        notify_bot=notifier, notify_chat_id=42,
    )
    assert text == "ok-text"
    assert len(notifier.sent) == 1
    assert "OpenAI" in notifier.sent[0]
    assert "недействителен" in notifier.sent[0]


async def test_fallback_400_message_does_not_blame_key():
    notifier = FakeNotifier()
    providers = [
        FakeProvider("openai", error=InvalidRequestError("bad model")),
        FakeProvider("gemini"),
    ]
    await llm_with_fallback(
        providers, [ChatMessage("user", "hi")],
        notify_bot=notifier, notify_chat_id=42,
    )
    assert "отклонил запрос" in notifier.sent[0]
    assert "недействителен" not in notifier.sent[0]


async def test_fallback_all_failed_report(no_sleep):
    providers = [
        FakeProvider("openai", error=AuthError("x")),
        FakeProvider("gemini", error=RateLimitedError(retry_after=None)),
        FakeProvider("groq", error=ProviderTimeoutError("t")),
    ]
    with pytest.raises(RuntimeError) as exc_info:
        await llm_with_fallback(providers, [ChatMessage("user", "hi")])
    report = str(exc_info.value)
    assert "OpenAI — API key invalid" in report
    assert "Gemini — rate limited" in report
    assert "Groq — timeout" in report


async def test_fallback_notification_failure_does_not_break():
    class BrokenNotifier:
        async def send_message(self, chat_id: int, text: str) -> None:
            raise RuntimeError("telegram down")

    providers = [
        FakeProvider("openai", error=AuthError("x")),
        FakeProvider("gemini"),
    ]
    text = await llm_with_fallback(
        providers, [ChatMessage("user", "hi")],
        notify_bot=BrokenNotifier(), notify_chat_id=42,
    )
    assert text == "ok-text"


# --- tier routing -----------------------------------------------------------


def test_heuristic_light_for_short_conversation():
    tier = resolve_tier([ChatMessage("user", "привет, как дела?")])
    assert tier is Tier.LIGHT


def test_heuristic_heavy_for_code():
    tier = resolve_tier([ChatMessage("user", "напиши код на python: алгоритм сортировки")])
    assert tier is Tier.HEAVY


def test_heuristic_heavy_for_long_prompt():
    tier = resolve_tier([ChatMessage("user", "x" * 2500)])
    assert tier is Tier.HEAVY


def test_heuristic_not_sure_falls_back_to_heavy():
    tier = resolve_tier([ChatMessage("user", "m" * 1000)])
    assert tier is Tier.HEAVY


def test_tier_chains_order():
    assert TIER_CHAINS[Tier.LIGHT] == ["groq", "gemini", "openai", "gigachat"]
    assert TIER_CHAINS[Tier.HEAVY] == ["openai", "gemini", "gigachat", "groq"]
    # активный провайдер идёт первым в get_provider_chain — проверяем порядок в цепочке
    for chain in TIER_CHAINS.values():
        assert len(set(chain)) == 4
        assert chain[0] in {"groq", "openai"}


async def test_llm_with_fallback_auto_tier_passes_heavy_flag():
    """Тяжёлый запрос без явного heavy -> провайдер получает heavy=True."""
    seen: list[bool] = []

    class RecordingProvider:
        name = "rec"

        async def chat(self, messages, *, heavy=False):
            seen.append(heavy)
            return "done"

    await llm_with_fallback(
        [RecordingProvider()],
        [ChatMessage("user", "напиши код для алгоритма")],
    )
    assert seen == [True]

    await llm_with_fallback(
        [RecordingProvider()],
        [ChatMessage("user", "привет")],
    )
    assert seen == [True, False]