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

_LIGHT_CONVERSATIONAL_MAX = 600
_HEAVY_LENGTH_THRESHOLD = 2000


def heuristic_tier(messages: list[ChatMessage]) -> Tier:
    """Эвристика сложности: LIGHT / HEAVY."""
    if not messages:
        return Tier.LIGHT

    text = "\n".join(m.content for m in messages)
    lowered = text.lower()

    if len(text) > _HEAVY_LENGTH_THRESHOLD or any(hint in lowered for hint in _HEAVY_HINTS):
        return Tier.HEAVY
    if len(text) < _LIGHT_CONVERSATIONAL_MAX:
        return Tier.LIGHT
    return Tier.HEAVY


def resolve_tier(messages: list[ChatMessage]) -> Tier:
    """Итоговый tier по эвристике."""
    return heuristic_tier(messages)