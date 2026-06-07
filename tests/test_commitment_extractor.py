"""Тесты для src/core/commitment_extractor.py: парсинг JSON, парсинг ISO, extract_and_save."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.commitment_extractor import (
    _parse_iso,
    _parse_json_array,
    extract_and_save_commitments,
)


# ── _parse_json_array ─────────────────────────────────────────────────────

class TestParseJsonArray:
    def test_valid_array(self):
        raw = (
            '[{"direction": "mine", "text": "позвонить", '
            '"deadline": null, "message_id": 1}]'
        )
        result = _parse_json_array(raw)
        assert len(result) == 1
        assert result[0]["direction"] == "mine"
        assert result[0]["text"] == "позвонить"

    def test_empty_array(self):
        result = _parse_json_array("[]")
        assert result == []

    def test_invalid_json(self):
        result = _parse_json_array("not json")
        assert result == []

    def test_json_with_fence(self):
        raw = '```json\n[{"direction": "mine", "text": "test", "deadline": null}]\n```'
        result = _parse_json_array(raw)
        assert len(result) == 1
        assert result[0]["text"] == "test"

    def test_not_array_returns_empty(self):
        result = _parse_json_array('{"direction": "mine"}')
        assert result == []


# ── _parse_iso ─────────────────────────────────────────────────────────────

class TestParseIso:
    def test_valid_iso(self):
        dt = _parse_iso("2026-06-07T15:00:00")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 7

    def test_iso_with_z(self):
        dt = _parse_iso("2026-06-10T10:00:00Z")
        assert dt is not None
        assert dt.tzinfo is None
        assert dt.hour == 10

    def test_null(self):
        assert _parse_iso(None) is None

    def test_invalid_string(self):
        assert _parse_iso("not-a-date") is None

    def test_empty_string(self):
        assert _parse_iso("") is None


# ── extract_and_save_commitments ───────────────────────────────────────────

class TestExtractAndSaveCommitments:
    async def test_empty_messages_returns_early(self):
        provider = MagicMock()
        provider.chat = AsyncMock()
        contact = MagicMock()
        result = await extract_and_save_commitments(
            provider, user_id=1, contact=contact, messages=[],
        )
        assert result == []
        provider.chat.assert_not_called()

    async def test_valid_commitments_saved(self):
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=(
            '[{"direction": "mine", "text": "Купить молоко", '
            '"deadline": null, "message_id": 1},'
            '{"direction": "theirs", "text": "Прислать отчёт", '
            '"deadline": "2026-06-10T10:00:00Z", "message_id": 2}]'
        ))
        contact = MagicMock()
        contact.display_name = "Артём"
        contact.peer_id = 999
        messages = [MagicMock()]

        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = False

        with (
            patch("src.core.commitment_extractor.get_session", return_value=mock_session),
            patch("src.core.commitment_extractor.add_commitment", new_callable=AsyncMock) as mock_add,
            patch("src.core.commitment_extractor.message_to_text", return_value="Артём: hello"),
        ):
            result = await extract_and_save_commitments(
                provider, user_id=1, contact=contact, messages=messages,
            )

        assert len(result) == 2
        assert mock_add.await_count == 2

    async def test_invalid_direction_skipped(self):
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=(
            '[{"direction": "unknown", "text": "test",'
            '"deadline": null, "message_id": null}]'
        ))

        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = False

        with (
            patch("src.core.commitment_extractor.get_session", return_value=mock_session),
            patch("src.core.commitment_extractor.add_commitment", new_callable=AsyncMock) as mock_add,
            patch("src.core.commitment_extractor.message_to_text", return_value="user: test"),
        ):
            result = await extract_and_save_commitments(
                provider, user_id=1,
                contact=MagicMock(display_name="Test"),
                messages=[MagicMock()],
            )

        assert result == []
        mock_add.assert_not_called()

    async def test_empty_text_skipped(self):
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=(
            '[{"direction": "mine", "text": "",'
            '"deadline": null, "message_id": null}]'
        ))

        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = False

        with (
            patch("src.core.commitment_extractor.get_session", return_value=mock_session),
            patch("src.core.commitment_extractor.add_commitment", new_callable=AsyncMock) as mock_add,
            patch("src.core.commitment_extractor.message_to_text", return_value="user: test"),
        ):
            result = await extract_and_save_commitments(
                provider, user_id=1,
                contact=MagicMock(display_name="Test"),
                messages=[MagicMock()],
            )

        assert result == []
        mock_add.assert_not_called()

    async def test_bad_llm_response_returns_empty(self):
        provider = MagicMock()
        provider.chat = AsyncMock(return_value="не json")

        with (
            patch("src.core.commitment_extractor.get_session"),
            patch("src.core.commitment_extractor.message_to_text", return_value="user: test"),
        ):
            result = await extract_and_save_commitments(
                provider, user_id=1,
                contact=MagicMock(display_name="Test"),
                messages=[MagicMock()],
            )

        assert result == []

    async def test_deadline_parsed_correctly(self):
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=(
            '[{"direction": "mine", "text": "отчёт",'
            '"deadline": "2026-12-31T23:59:00Z", "message_id": 3}]'
        ))

        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = False

        with (
            patch("src.core.commitment_extractor.get_session", return_value=mock_session),
            patch("src.core.commitment_extractor.add_commitment", new_callable=AsyncMock) as mock_add,
            patch("src.core.commitment_extractor.message_to_text", return_value="user: test"),
        ):
            await extract_and_save_commitments(
                provider, user_id=1,
                contact=MagicMock(display_name="Test"),
                messages=[MagicMock()],
            )

        mock_add.assert_called_once()
        _, kwargs = mock_add.call_args
        assert kwargs["deadline_at"] is not None
        assert isinstance(kwargs["deadline_at"], datetime)

    async def test_system_prompt_sent_to_llm(self):
        provider = MagicMock()
        provider.chat = AsyncMock(return_value="[]")

        with (
            patch("src.core.commitment_extractor.message_to_text", return_value="user: test"),
        ):
            await extract_and_save_commitments(
                provider, user_id=1,
                contact=MagicMock(display_name="Test"),
                messages=[MagicMock()],
            )

        provider.chat.assert_called_once()
        messages = provider.chat.call_args[0][0]
        assert messages[0].role == "system"
        assert "direction" in messages[0].content


# Список покрытых сценариев:
# TestParseJsonArray:
#   - valid_array → list
#   - empty_array → []
#   - invalid_json → []
#   - json_with_fence → list (```json)
#   - not_array_returns_empty → []
# TestParseIso:
#   - valid_iso → datetime
#   - iso_with_z → datetime no tzinfo
#   - null → None
#   - invalid_string → None
#   - empty_string → None
# TestExtractAndSaveCommitments:
#   - empty_messages → early return, no LLM call
#   - valid_commitments → saved with add_commitment
#   - invalid_direction → skipped
#   - empty_text → skipped
#   - bad_llm_response → empty list, no crash
#   - deadline_parsed → datetime object
#   - system_prompt → contains "direction"
