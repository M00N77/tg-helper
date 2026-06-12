"""Анализ тональности текстов через LLM-провайдера.

LLM дешевле и точнее локальной модели для русского языка, а главное —
не требует загрузки FastText-модели (сервер Dostoevsky недоступен).

Потребление: ~50 входящих + 5 исходящих токенов на сообщение.
На GPT-5-mini это ~$0.01 / 1000 сообщений.
"""
import json
import logging

from src.llm.base import ChatMessage
from src.core.schemas import SentimentRiskResult

logger = logging.getLogger(__name__)

SENTIMENT_PROMPT = """Определи тональность этого сообщения на русском языке.
Ответь ОДНИМ словом: positive, negative, neutral или speech (приветствия/прощания/благодарность).

Сообщение: {text}
Тональность:"""

SENTIMENT_RISK_PROMPT = """\
Определи тональность сообщения на русском языке.
Также определи, содержит ли оно признаки риска:
слова вроде «не успеваем», «проблема», «застрял», «подвисшая задача», «срочно», «критично»,
«не получается», «помогите», «горим», «провалили», «сдвигаем сроки».

Ответь строгим JSON без пояснений и без markdown:
{"sentiment": "<positive|negative|neutral|speech>", "has_risk": <true|false>, "risk_reason": "<причина или пустая строка>"}

Сообщение: {text}
"""

_LABELS = {"positive", "negative", "neutral", "speech"}


async def analyze_sentiment(text: str, provider) -> str | None:
    """Возвращает 'positive' | 'negative' | 'neutral' | 'speech' | None."""
    if not text or len(text) < 3:
        return None
    try:
        raw = await provider.chat(
            [ChatMessage(role="user", content=SENTIMENT_PROMPT.format(text=text[:500]))],
            heavy=False,
        )
        label = raw.strip().lower().rstrip(".,!?")
        return label if label in _LABELS else "neutral"
    except Exception:
        logger.exception("sentiment LLM call failed")
        return None


async def analyze_sentiment_and_risk(
    text: str,
    provider,
) -> SentimentRiskResult | None:
    if not text or len(text.strip()) < 3:
        return None
    try:
        raw = await provider.chat(
            [ChatMessage(role="user", content=SENTIMENT_RISK_PROMPT.format(text=text[:500]))],
            heavy=False,
        )
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("no JSON object found in LLM response")
        raw = raw[start : end + 1]
        data = json.loads(raw)
        return SentimentRiskResult(**data)
    except Exception as e:
        logger.warning("analyze_sentiment_and_risk failed: %s", e)
        return None
