"""Unit-тесты для src/core/reminders.py."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.reminders import RE_REMINDER_HOURS, _check_once


def _mock_settings(**kwargs) -> MagicMock:
    s = MagicMock()
    s.reminders_enabled = kwargs.get("reminders_enabled", True)
    s.reminder_lead_hours = kwargs.get("reminder_lead_hours", 2)
    s.reminder_overdue_enabled = kwargs.get("reminder_overdue_enabled", True)
    s.timezone = kwargs.get("timezone", "UTC")
    return s


def _mock_commitment(*, id: int = 1, direction: str = "mine", text: str = "test task",
                     deadline_at: datetime | None = None,
                     last_reminded_at: datetime | None = None,
                     peer_name: str | None = None) -> MagicMock:
    c = MagicMock()
    c.id = id
    c.direction = direction
    c.text = text
    c.deadline_at = deadline_at
    c.last_reminded_at = last_reminded_at
    c.peer_name = peer_name
    return c


def _past(hours: int = 1, **kw) -> MagicMock:
    return _mock_commitment(deadline_at=datetime.utcnow() - timedelta(hours=hours), **kw)


def _future(hours: int = 1, **kw) -> MagicMock:
    return _mock_commitment(deadline_at=datetime.utcnow() + timedelta(hours=hours), **kw)


@pytest.fixture
def _setup():
    """Базовый мок: юзер с настройками, get_session как контекстный менеджер."""
    owner = MagicMock()
    owner.settings = _mock_settings()

    patchers = [
        patch("src.core.reminders.get_or_create_user", return_value=owner),
        patch("src.core.reminders.notifier.notify", new_callable=AsyncMock),
        patch("src.core.reminders.get_session"),
    ]
    for p in patchers:
        p.start()
    yield owner
    for p in patchers:
        p.stop()


def _mock_session_execute(items: list) -> MagicMock:
    """Создаёт MagicMock для сессии, где execute возвращает scalars().all() == items."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = items
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


# ── tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reminders_disabled():
    """Если reminders_enabled=False — ничего не отправляем."""
    owner = MagicMock()
    owner.settings = _mock_settings(reminders_enabled=False)

    with (
        patch("src.core.reminders.get_or_create_user", return_value=owner),
        patch("src.core.reminders.notifier.notify", new_callable=AsyncMock) as mock_notify,
        patch("src.core.reminders.get_session") as mock_get_session,
    ):
        mock_get_session.return_value.__aenter__.return_value = MagicMock()
        await _check_once(42)
        mock_notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_open_items():
    """Если открытых commitment'ов нет — ничего не отправляем."""
    owner = MagicMock()
    owner.settings = _mock_settings()

    with (
        patch("src.core.reminders.get_or_create_user", return_value=owner),
        patch("src.core.reminders.notifier.notify", new_callable=AsyncMock) as mock_notify,
        patch("src.core.reminders.get_session") as mock_get_session,
    ):
        mock_session = _mock_session_execute([])
        mock_get_session.return_value.__aenter__.return_value = mock_session
        await _check_once(42)
        mock_notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_overdue_never_reminded():
    """Overdue задача, ещё не напоминали → отправляем + ставим last_reminded_at."""
    now = datetime.utcnow()
    commitment = _past(hours=2, id=1)

    owner = MagicMock()
    owner.settings = _mock_settings()

    with (
        patch("src.core.reminders.get_or_create_user", return_value=owner),
        patch("src.core.reminders.notifier.notify", new_callable=AsyncMock) as mock_notify,
        patch("src.core.reminders.get_session") as mock_get_session,
    ):
        session1 = _mock_session_execute([commitment])
        session2 = MagicMock()
        session2.get = AsyncMock(return_value=commitment)

        mock_get_session.side_effect = [
            AsyncMock(__aenter__=AsyncMock(return_value=session1)),
            AsyncMock(__aenter__=AsyncMock(return_value=session2)),
        ]

        await _check_once(42)

        mock_notify.assert_awaited_once()
        text = mock_notify.await_args[0][0]
        assert "Просрочено" in text


@pytest.mark.asyncio
async def test_overdue_recently_reminded():
    """Overdue задача, reminded < 6ч назад → НЕ отправляем повторно."""
    now = datetime.utcnow()
    commitment = _past(hours=2, id=1,
                       last_reminded_at=now - timedelta(hours=RE_REMINDER_HOURS - 1))

    owner = MagicMock()
    owner.settings = _mock_settings()

    with (
        patch("src.core.reminders.get_or_create_user", return_value=owner),
        patch("src.core.reminders.notifier.notify", new_callable=AsyncMock) as mock_notify,
        patch("src.core.reminders.get_session") as mock_get_session,
    ):
        mock_get_session.return_value.__aenter__.return_value = _mock_session_execute([commitment])
        await _check_once(42)
        mock_notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_overdue_repeat_after_interval():
    """Overdue задача, reminded > 6ч назад → отправляем снова."""
    now = datetime.utcnow()
    commitment = _past(hours=2, id=1,
                       last_reminded_at=now - timedelta(hours=RE_REMINDER_HOURS + 1))

    owner = MagicMock()
    owner.settings = _mock_settings()

    with (
        patch("src.core.reminders.get_or_create_user", return_value=owner),
        patch("src.core.reminders.notifier.notify", new_callable=AsyncMock) as mock_notify,
        patch("src.core.reminders.get_session") as mock_get_session,
    ):
        session1 = _mock_session_execute([commitment])
        session2 = MagicMock()
        session2.get = AsyncMock(return_value=commitment)

        mock_get_session.side_effect = [
            AsyncMock(__aenter__=AsyncMock(return_value=session1)),
            AsyncMock(__aenter__=AsyncMock(return_value=session2)),
        ]

        await _check_once(42)

        mock_notify.assert_awaited_once()
        text = mock_notify.await_args[0][0]
        assert "Просрочено" in text


@pytest.mark.asyncio
async def test_lead_never_reminded():
    """Lead задача (скоро дедлайн), не напоминали → отправляем."""
    commitment = _future(hours=1, id=1)

    owner = MagicMock()
    owner.settings = _mock_settings(reminder_lead_hours=2)

    with (
        patch("src.core.reminders.get_or_create_user", return_value=owner),
        patch("src.core.reminders.notifier.notify", new_callable=AsyncMock) as mock_notify,
        patch("src.core.reminders.get_session") as mock_get_session,
    ):
        session1 = _mock_session_execute([commitment])
        session2 = MagicMock()
        session2.get = AsyncMock(return_value=commitment)

        mock_get_session.side_effect = [
            AsyncMock(__aenter__=AsyncMock(return_value=session1)),
            AsyncMock(__aenter__=AsyncMock(return_value=session2)),
        ]

        await _check_once(42)

        mock_notify.assert_awaited_once()
        text = mock_notify.await_args[0][0]
        assert "Скоро дедлайн" in text


@pytest.mark.asyncio
async def test_lead_already_reminded():
    """Lead задача, уже напоминали → НЕ отправляем (lead — одноразовый)."""
    commitment = _future(hours=1, id=1, last_reminded_at=datetime.utcnow())

    owner = MagicMock()
    owner.settings = _mock_settings(reminder_lead_hours=2)

    with (
        patch("src.core.reminders.get_or_create_user", return_value=owner),
        patch("src.core.reminders.notifier.notify", new_callable=AsyncMock) as mock_notify,
        patch("src.core.reminders.get_session") as mock_get_session,
    ):
        mock_get_session.return_value.__aenter__.return_value = _mock_session_execute([commitment])
        await _check_once(42)
        mock_notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_mixed_overdue_and_lead():
    """Из нескольких задач: только overdue без недавнего reminded + lead без reminded."""
    now = datetime.utcnow()
    overdue_not_reminded = _past(hours=3, id=1)
    overdue_stale = _past(hours=5, id=2,
                          last_reminded_at=now - timedelta(hours=RE_REMINDER_HOURS + 1))
    overdue_recent = _past(hours=1, id=3,
                           last_reminded_at=now - timedelta(hours=1))
    lead_not_reminded = _future(hours=1, id=4)
    lead_already = _future(hours=1, id=5, last_reminded_at=now)
    no_deadline = _mock_commitment(id=6, deadline_at=None)

    items = [overdue_not_reminded, overdue_stale, overdue_recent,
             lead_not_reminded, lead_already, no_deadline]

    owner = MagicMock()
    owner.settings = _mock_settings(reminder_lead_hours=2)

    with (
        patch("src.core.reminders.get_or_create_user", return_value=owner),
        patch("src.core.reminders.notifier.notify", new_callable=AsyncMock) as mock_notify,
        patch("src.core.reminders.get_session") as mock_get_session,
    ):
        session1 = _mock_session_execute(items)

        session2 = MagicMock()
        async def get_side_effect(cls, pk):
            mapping = {1: overdue_not_reminded, 2: overdue_stale, 4: lead_not_reminded}
            return mapping.get(pk)
        session2.get = get_side_effect

        mock_get_session.side_effect = [
            AsyncMock(__aenter__=AsyncMock(return_value=session1)),
            AsyncMock(__aenter__=AsyncMock(return_value=session2)),
        ]

        await _check_once(42)

        assert mock_notify.await_count == 3
        texts = [call[0][0] for call in mock_notify.await_args_list]
        overdue_texts = [t for t in texts if "Просрочено" in t]
        lead_texts = [t for t in texts if "Скоро дедлайн" in t]
        assert len(overdue_texts) == 2
        assert len(lead_texts) == 1

        assert overdue_not_reminded.last_reminded_at is not None
        assert overdue_stale.last_reminded_at is not None
        assert lead_not_reminded.last_reminded_at is not None


@pytest.mark.asyncio
async def test_overdue_disabled():
    """reminder_overdue_enabled=False — просрочки не шлются."""
    commitment = _past(hours=2, id=1)

    owner = MagicMock()
    owner.settings = _mock_settings(reminder_overdue_enabled=False)

    with (
        patch("src.core.reminders.get_or_create_user", return_value=owner),
        patch("src.core.reminders.notifier.notify", new_callable=AsyncMock) as mock_notify,
        patch("src.core.reminders.get_session") as mock_get_session,
    ):
        mock_get_session.return_value.__aenter__.return_value = _mock_session_execute([commitment])
        await _check_once(42)
        mock_notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_lead_hours_zero():
    """reminder_lead_hours=0 — lead напоминания не шлются."""
    commitment = _future(hours=1, id=1)

    owner = MagicMock()
    owner.settings = _mock_settings(reminder_lead_hours=0)

    with (
        patch("src.core.reminders.get_or_create_user", return_value=owner),
        patch("src.core.reminders.notifier.notify", new_callable=AsyncMock) as mock_notify,
        patch("src.core.reminders.get_session") as mock_get_session,
    ):
        mock_get_session.return_value.__aenter__.return_value = _mock_session_execute([commitment])
        await _check_once(42)
        mock_notify.assert_not_awaited()
