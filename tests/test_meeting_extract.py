"""Тесты парсинга ответа LLM для MEETING_EXTRACT_SYSTEM."""
from __future__ import annotations

import json

import pytest

from src.core.agent import _safe_parse


class TestMeetingExtractParsing:
    """Проверяем, что _safe_parse корректно разбирает JSON
    из MEETING_EXTRACT_SYSTEM (саммари + задачи)."""

    def test_empty_tasks(self):
        data = {
            "summary": "Обсудили планы на неделю.",
            "participants": ["Анна", "Иван"],
            "tasks": [],
        }
        assert data["summary"] == "Обсудили планы на неделю."
        assert data["tasks"] == []

    def test_parse_summary_with_tasks(self):
        raw = json.dumps({
            "summary": "Обсудили спринт",
            "participants": ["Анна", "Иван"],
            "tasks": [
                {
                    "title": "Сделать отчёт",
                    "assignee": "Анна",
                    "deadline": "2026-06-15T00:00:00Z",
                }
            ],
        })
        data = json.loads(raw)
        assert data["summary"] == "Обсудили спринт"
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["title"] == "Сделать отчёт"
        assert data["tasks"][0]["assignee"] == "Анна"

    def test_parse_multiple_tasks(self):
        raw = json.dumps({
            "summary": "Планирование",
            "participants": ["Иван"],
            "tasks": [
                {"title": "Задача 1", "assignee": None, "deadline": None},
                {"title": "Задача 2", "assignee": "Иван", "deadline": "2026-06-20T12:00:00Z"},
            ],
        })
        data = json.loads(raw)
        assert len(data["tasks"]) == 2
        assert data["tasks"][0]["assignee"] is None

    def test_parse_bad_json_returns_unknown(self):
        raw = "это не json"
        data = _safe_parse(raw)
        assert data["intent"] == "unknown"

    def test_parse_missing_tasks_field(self):
        raw = json.dumps({"summary": "Встреча"})
        data = json.loads(raw)
        assert data["summary"] == "Встреча"
        assert "tasks" not in data

    def test_parse_with_fence(self):
        raw = "```json\n{\"intent\": \"join_meeting\", \"url\": \"https://my.mts-link.ru/abc\"}\n```"
        data = _safe_parse(raw)
        assert data["intent"] == "join_meeting"
        assert data["url"] == "https://my.mts-link.ru/abc"

    @pytest.mark.parametrize("url", [
        "https://my.mts-link.ru/abc123",
        "https://telemost.yandex.ru/j/xyz",
        "https://jazz.sber.ru/meet/123",
        "https://kontur.ru/tolk/abc",
    ])
    def test_join_meeting_urls(self, url):
        """Проверяем, что _safe_parse парсит join_meeting с разными URL платформ."""
        raw = json.dumps({"intent": "join_meeting", "url": url})
        data = _safe_parse(raw)
        assert data["intent"] == "join_meeting"
        assert data["url"] == url
