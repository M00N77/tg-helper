"""Интеграционный тест для вечернего дайджеста с реальной БД и мокнутым YouGile."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.evening_digest import send_evening_digest
from src.db.models import Commitment, Team
from src.db.repo import get_or_create_user


@pytest.mark.asyncio
async def test_integration_evening_digest(session):
    """Сценарий: есть User + Commitment на завтра + Team с YouGile.
    send_evening_digest должен отправить дайджест с обоими источниками."""
    owner = await get_or_create_user(session, 999001)
    owner.settings.timezone = "UTC"

    tomorrow = (
        datetime.utcnow().replace(hour=10, minute=0, second=0, microsecond=0)
        + timedelta(days=1)
    )
    session.add(
        Commitment(
            user_id=owner.id,
            peer_id=12345,
            peer_name="Коллега",
            direction="theirs",
            text="Прислать отчёт к завтра",
            deadline_at=tomorrow,
            status="open",
        )
    )

    unique_chat_id = time.time_ns() % 10_000_000 + 1
    session.add(
        Team(
            chat_id=unique_chat_id,
            kanban_token="test-token",
            kanban_board_id="test-board",
        )
    )
    await session.commit()

    mock_client = MagicMock()
    mock_client.get_columns = AsyncMock(
        return_value=[{"id": "col-1", "title": "In Progress"}]
    )
    tomorrow_noon_ms = int(
        (
            datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        ).timestamp()
        * 1000
    )
    mock_client.get_cards_in_column = AsyncMock(
        return_value=[
            {
                "id": "yg-1",
                "title": "Сверстать лендинг",
                "deadline": tomorrow_noon_ms,
            },
        ]
    )

    with (
        patch(
            "src.bot.handlers.yougile.YouGileClient", return_value=mock_client,
        ),
        patch("src.core.evening_digest.notifier.notify", new_callable=AsyncMock) as mock_notify,
    ):
        await send_evening_digest(999001)

    mock_notify.assert_awaited_once()
    args, kwargs = mock_notify.call_args
    text = args[0]
    assert "Прислать отчёт к завтра" in text
    assert "Сверстать лендинг" in text
    assert kwargs.get("parse_mode") == "Markdown"
