"""E2E-тест: реальный Gemini с транскрипцией встречи.

Проверяет, что LLM с MEETING_EXTRACT_SYSTEM корректно извлекает
саммари, участников и задачи из транскрипции."""
from __future__ import annotations

import json
import os

import pytest

from src.llm.base import ChatMessage
from src.llm.gemini_provider import GeminiProvider

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY не задан",
)

MEETING_EXTRACT_SYSTEM = (
    "Ты анализируешь транскрипцию встречи.\n"
    "Верни СТРОГИЙ JSON (без markdown-обёртки):\n"
    '{\n'
    '  "summary": "саммари встречи 3-5 предложений",\n'
    '  "participants": ["имя1", "имя2"],\n'
    '  "tasks": [\n'
    '    {"title": "название задачи", "assignee": "имя или null", "deadline": "ISO-8601 или null"},\n'
    '    ...\n'
    '  ]\n'
    '}\n'
    'Если задач нет — tasks: [].\n'
    "Опирайся только на то, что сказано в тексте."
)

FAKE_TRANSCRIPT = """
Анна: Привет, Иван! Давай обсудим планы на этот спринт.
Иван: Привет. Нужно доделать форму обратной связи, я беру фронтенд.
Анна: Отлично. Я сделаю бэкенд API до среды.
Иван: Ещё нам нужны авто-тесты для этого функционала.
Анна: Да, Петя может написать тесты до пятницы.
Иван: Договорились. Тогда в пятницу ревью.
"""


@pytest.fixture(scope="module")
def provider():
    return GeminiProvider(os.environ["GEMINI_API_KEY"])


class TestMeetingExtractReal:
    """Реальные вызовы Gemini с транскрипцией встречи."""

    async def test_extract_summary_and_tasks(self, provider):
        raw = await provider.chat(
            [
                ChatMessage(role="system", content=MEETING_EXTRACT_SYSTEM),
                ChatMessage(role="user", content=f"Транскрипция:\n\n{FAKE_TRANSCRIPT}"),
            ],
            heavy=True,
        )
        # Очищаем от markdown-обёртки если LLM забыла инструкцию
        cleaned = raw.strip().strip("```").lstrip("json").strip()
        data = json.loads(cleaned)

        assert "summary" in data, f"Нет summary в ответе: {data}"
        assert len(data["summary"]) > 10, f"summary слишком короткий: {data['summary']}"

        assert "participants" in data
        assert len(data["participants"]) >= 2, (
            f"Должно быть минимум 2 участника: {data['participants']}"
        )

        assert "tasks" in data, f"Нет tasks в ответе: {data}"
        assert len(data["tasks"]) >= 2, (
            f"Должно быть минимум 2 задачи из транскрипции: {data['tasks']}"
        )

        # Проверяем структуру каждой задачи
        for task in data["tasks"]:
            assert "title" in task, f"Задача без title: {task}"
            assert len(task["title"]) > 0, f"Пустой title у задачи: {task}"

    async def test_extract_with_deadline(self, provider):
        """Проверяем, что LLM корректно извлекает дедлайн из фразы «до среды»."""
        raw = await provider.chat(
            [
                ChatMessage(role="system", content=MEETING_EXTRACT_SYSTEM),
                ChatMessage(role="user", content=(
                    "Транскрипция:\n\n"
                    "Анна: Сделай отчёт до пятницы.\n"
                    "Иван: Хорошо, будет готово в пятницу."
                )),
            ],
            heavy=True,
        )
        cleaned = raw.strip().strip("```").lstrip("json").strip()
        data = json.loads(cleaned)

        assert len(data["tasks"]) >= 1
        task = data["tasks"][0]
        assert "deadline" in task, (
            f"LLM должна извлечь дедлайн из «до пятницы»: {task}"
        )
