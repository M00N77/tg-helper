"""Определение сложности запроса: LIGHT / HEAVY.

Эвристика — бесплатная и детерминированная. LLM-классификация для каждого
запроса НЕ выполняется: если эвристика не уверена, используется HEAVY
как безопасный default.
"""
from enum import Enum

from src.llm.base import ChatMessage


class Tier(str, Enum):
    LIGHT = "light"
    HEAVY = "heavy"


_HEAVY_HINTS = (
    "код", "code", "debug", "отлад", "алгоритм", "algorithm",
    "анализ", "analysis", "объясн", "explain", "рефактор", "refactor",
    "sql", "json", "regex", "регуляр",
)

# Короткий conversational prompt — LIGHT
_LIGHT_CONVERSATIONAL_MAX = 600
# Длинный prompt — HEAVY
_HEAVY_LENGTH_THRESHOLD = 2000


def heuristic_tier(messages: list[ChatMessage]) -> Tier | None:
    """Эвристика: возвращает tier, если уверены; иначе None (нужен fallback).

    - код / debug / алгоритмы / анализ / объяснения → HEAVY
    - длинный prompt → HEAVY
    - короткий разговорный prompt → LIGHT
    """
    if not messages:
        return Tier.LIGHT

    text = "\n".join(m.content for m in messages)
    lowered = text.lower()

    if len(text) > _HEAVY_LENGTH_THRESHOLD:
        return Tier.HEAVY
    if any(hint in lowered for hint in _HEAVY_HINTS):
        return Tier.HEAVY
    if len(text) < _LIGHT_CONVERSATIONAL_MAX:
        return Tier.LIGHT
    return None


def classify_tier(messages: list[ChatMessage]) -> Tier | None:
    """Опциональный LLM-классификатор.

    По умолчанию недоступен (None): классифицировать каждый запрос LLM —
    расточительно. Подключается явно, если когда-нибудь понадобится.
    """
    return None


def resolve_tier(
    messages: list[ChatMessage],
    classifier=None,
) -> Tier:
    """Итоговый tier: эвристика → (опциональный) classifier → HEAVY."""
    tier = heuristic_tier(messages)
    if tier is not None:
        return tier
    if classifier is not None:
        classified = classifier(messages)
        if classified is not None:
            return classified
    return Tier.HEAVY