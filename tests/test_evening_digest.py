"""Unit-тесты для src/core/evening_digest.py."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.evening_digest import (
    _build_digest_text,
    _get_tomorrow_commitments,
    _get_yougile_cards,
    evening_digest_loop,
    send_evening_digest,
)


def _mock_commitment(
    *,
    direction: str = "mine",
    text: str = "test task",
    deadline_at: datetime | None = None,
    peer_name: str | None = None,
) -> MagicMock:
    c = MagicMock()
    c.direction = direction
    c.text = text
    c.deadline_at = deadline_at
    c.peer_name = peer_name
    return c


def _mock_yougile_card(
    *,
    title: str = "Card",
    deadline: int | None = None,
    dueDate: int | None = None,
) -> dict:
    card: dict = {"id": "card-1", "title": title}
    if deadline is not None:
        card["deadline"] = deadline
    if dueDate is not None:
        card["dueDate"] = dueDate
    return card


def _tomorrow_ms(offset_hours: int = 0) -> int:
    """unix ms на завтра в указанный час (UTC)."""
    tomorrow = (
        datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        + timedelta(days=1, hours=offset_hours)
    )
    return int(tomorrow.timestamp() * 1000)


# ── _build_digest_text ──────────────────────────────────────────────────────

class TestBuildDigestText:
    @pytest.mark.asyncio
    async def test_empty(self):
        with (
            patch("src.core.evening_digest._get_tomorrow_commitments", return_value=[]),
            patch("src.core.evening_digest._get_yougile_cards", return_value=[]),
        ):
            text = await _build_digest_text()
            assert "Отдыхай" in text

    @pytest.mark.asyncio
    async def test_commitments_only(self):
        comms = [
            _mock_commitment(direction="mine", text="Сдать отчёт"),
            _mock_commitment(direction="theirs", text="Прислать макет", peer_name="Клиент"),
        ]
        with (
            patch("src.core.evening_digest._get_tomorrow_commitments", return_value=comms),
            patch("src.core.evening_digest._get_yougile_cards", return_value=[]),
        ):
            text = await _build_digest_text()
            assert "Сдать отчёт" in text
            assert "Прислать макет" in text
            assert "Клиент" in text
            assert "Из чатов" in text
            assert "YouGile" not in text

    @pytest.mark.asyncio
    async def test_yougile_only(self):
        cards = [_mock_yougile_card(title="Сверстать лендинг")]
        with (
            patch("src.core.evening_digest._get_tomorrow_commitments", return_value=[]),
            patch("src.core.evening_digest._get_yougile_cards", return_value=cards),
        ):
            text = await _build_digest_text()
            assert "Сверстать лендинг" in text
            assert "В YouGile" in text
            assert "Из чатов" not in text

    @pytest.mark.asyncio
    async def test_both_sources(self):
        comms = [_mock_commitment(text="Позвонить клиенту")]
        cards = [_mock_yougile_card(title="Написать ТЗ")]
        with (
            patch("src.core.evening_digest._get_tomorrow_commitments", return_value=comms),
            patch("src.core.evening_digest._get_yougile_cards", return_value=cards),
        ):
            text = await _build_digest_text()
            assert "Позвонить клиенту" in text
            assert "Написать ТЗ" in text
            assert "Из чатов" in text
            assert "В YouGile" in text


# ── _get_tomorrow_commitments ───────────────────────────────────────────────

class TestGetTomorrowCommitments:
    @pytest.mark.asyncio
    async def test_queries_open_status_and_tomorrow(self):
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = False

        expected_commitment = _mock_commitment()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [expected_commitment]
        mock_session.execute = AsyncMock(return_value=mock_result)

        tomorrow_start = (
            datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        )

        with patch("src.core.evening_digest.get_session", return_value=mock_session):
            result = await _get_tomorrow_commitments()

        assert result == [expected_commitment]
        call = mock_session.execute.call_args[0][0]
        where_clauses = [str(c) for c in call.whereclause.clauses]
        assert any("status" in c for c in where_clauses)
        assert any("deadline_at" in c for c in where_clauses)
        mock_session.execute.assert_awaited_once()


# ── _get_yougile_cards ──────────────────────────────────────────────────────

class TestGetYougileCards:
    @pytest.mark.asyncio
    async def test_filters_cards_by_tomorrow_deadline(self):
        tomorrow_noon_ms = _tomorrow_ms(offset_hours=12)
        yesterday_ms = _tomorrow_ms(offset_hours=-24)
        day_after_ms = _tomorrow_ms(offset_hours=36)

        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = False
        mock_team = MagicMock()
        mock_team.chat_id = -100123
        mock_team.kanban_token = "token"
        mock_team.kanban_board_id = "board"
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_team]
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_client = MagicMock()
        mock_client.get_columns = AsyncMock(
            return_value=[{"id": "col-1", "title": "In Progress"}]
        )
        mock_client.get_cards_in_column = AsyncMock(
            return_value=[
                _mock_yougile_card(title="На завтра", deadline=tomorrow_noon_ms),
                _mock_yougile_card(title="Вчерашняя", deadline=yesterday_ms),
                _mock_yougile_card(title="Послезавтра", deadline=day_after_ms),
                _mock_yougile_card(title="Без дедлайна"),
            ]
        )

        with (
            patch("src.core.evening_digest.get_session", return_value=mock_session),
            patch(
                "src.bot.handlers.yougile.YouGileClient", return_value=mock_client,
            ),
        ):
            cards = await _get_yougile_cards()

        titles = [c["title"] for c in cards]
        assert "На завтра" in titles
        assert "Вчерашняя" not in titles
        assert "Послезавтра" not in titles
        assert "Без дедлайна" not in titles

    @pytest.mark.asyncio
    async def test_skips_team_on_error(self):
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = False
        mock_team = MagicMock()
        mock_team.chat_id = -100123
        mock_team.kanban_token = "token"
        mock_team.kanban_board_id = "board"
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_team]
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_client = MagicMock()
        mock_client.get_columns = AsyncMock(side_effect=RuntimeError("API error"))

        with (
            patch("src.core.evening_digest.get_session", return_value=mock_session),
            patch(
                "src.bot.handlers.yougile.YouGileClient", return_value=mock_client,
            ),
        ):
            cards = await _get_yougile_cards()

        assert cards == []


# ── send_evening_digest ─────────────────────────────────────────────────────

class TestSendEveningDigest:
    @pytest.mark.asyncio
    async def test_notifies_with_markdown(self):
        with (
            patch(
                "src.core.evening_digest._build_digest_text",
                return_value="🌙 *test*",
            ),
            patch("src.core.evening_digest.notifier.notify", new_callable=AsyncMock) as mock_notify,
        ):
            await send_evening_digest(12345)

        mock_notify.assert_awaited_once_with("🌙 *test*", parse_mode="Markdown")


# ── evening_digest_loop ─────────────────────────────────────────────────────

class TestEveningDigestLoop:
    @pytest.mark.asyncio
    async def test_sends_once_then_skips_duplicate(self):
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = False

        mock_owner = MagicMock()
        mock_owner.settings.timezone = "Europe/Moscow"
        mock_result = MagicMock()
        mock_session.execute.return_value = mock_result

        call_count = 0

        async def fake_get_or_create_user(session, telegram_id):
            nonlocal call_count
            call_count += 1
            return mock_owner

        mock_now = datetime(2026, 6, 8, 20, 0, 0)

        send_mock = AsyncMock()

        with (
            patch("src.core.evening_digest.get_session", return_value=mock_session),
            patch("src.core.evening_digest.get_or_create_user", side_effect=fake_get_or_create_user),
            patch("src.core.evening_digest.now_in_tz", return_value=mock_now),
            patch("src.core.evening_digest.send_evening_digest", send_mock),
            patch("src.core.evening_digest.asyncio.sleep", side_effect=[None, asyncio.CancelledError]),
        ):
            with pytest.raises(asyncio.CancelledError):
                await evening_digest_loop()

        assert send_mock.await_count == 1
