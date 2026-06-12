"""In-memory кэш словаря терминов команды + поиск подстрок.

Паттерн: ленивая инъекция. При первом обращении к команде кэш прогревается
одним SELECT-запросом. Последующие вызовы работают синхронно, без БД.
"""
from __future__ import annotations

import asyncio
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repo import list_team_dictionary


logger = logging.getLogger(__name__)


class DictionaryCache:
    """Потокобезопасный кэш словарей команд.

    Структура:
      _cache[team_id] -> {lower_term: definition}
      _compiled[team_id] -> re.Pattern
    """

    def __init__(self) -> None:
        self._cache: dict[int, dict[str, str]] = {}
        self._compiled: dict[int, re.Pattern] = {}
        self._loading: dict[int, asyncio.Lock] = {}

    def _team_lock(self, team_id: int) -> asyncio.Lock:
        if team_id not in self._loading:
            self._loading[team_id] = asyncio.Lock()
        return self._loading[team_id]

    async def load_team_dictionary(self, team_id: int, session: AsyncSession) -> None:
        """Прогревает кэш для команды, если ещё не загружен."""
        if team_id in self._cache:
            return
        lock = self._team_lock(team_id)
        async with lock:
            if team_id in self._cache:
                return
            terms = await list_team_dictionary(session, team_id)
            self._cache[team_id] = {t.term.lower(): t.definition for t in terms}
            self._compile_pattern(team_id)
            logger.info("dictionary_cache: loaded %d terms for team_id=%d", len(terms), team_id)

    def invalidate_team_cache(self, team_id: int) -> None:
        """Сбрасывает кэш команды (при редактировании словаря)."""
        self._cache.pop(team_id, None)
        self._compiled.pop(team_id, None)
        logger.info("dictionary_cache: invalidated team_id=%d", team_id)

    def _compile_pattern(self, team_id: int) -> None:
        terms = list(self._cache[team_id].keys())
        if not terms:
            self._compiled[team_id] = re.compile(r"(?!x)x")  # never matches
            return
        terms.sort(key=len, reverse=True)
        pattern = "|".join(re.escape(t) for t in terms)
        self._compiled[team_id] = re.compile(pattern, re.IGNORECASE)

    def match_terms(self, text: str, team_id: int) -> dict[str, str]:
        """Ищет термины команды в тексте.

        Возвращает {original_lower: (original_case, definition)}.
        """
        cache = self._cache.get(team_id)
        if not cache:
            return {}

        pattern = self._compiled.get(team_id)
        if pattern is None:
            return {}

        seen: set[str] = set()
        result: dict[str, str] = {}
        for m in pattern.finditer(text):
            raw = m.group(0).strip()
            key = raw.lower()
            if key not in seen and key in cache:
                seen.add(key)
                result[raw] = cache[key]
        return result


dictionary_cache = DictionaryCache()


async def get_context_definitions(text: str, team_id: int) -> str:
    """Основная точка входа для инъекции.

    1. Лениво прогревает кэш, если он ещё пуст.
    2. Сканирует text на предмет терминов команды.
    3. Возвращает Markdown-строку с определениями или пустую строку.

    Должна вызываться внутри async with get_session() — session нужна
    только при первом обращении к team_id, дальнейшие вызовы синхронны.
    """
    from src.db.session import get_session

    cache = dictionary_cache._cache.get(team_id)
    if cache is None:
        async with get_session() as session:
            await dictionary_cache.load_team_dictionary(team_id, session)
        cache = dictionary_cache._cache.get(team_id, {})

    if not cache:
        return ""

    matched = dictionary_cache.match_terms(text, team_id)
    if not matched:
        return ""

    lines = "\n".join(
        f"- **{term}**: {definition}"
        for term, definition in matched.items()
    )
    return f"Профессиональный контекст для этого сообщения:\n{lines}"
