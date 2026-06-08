"""Интеграционный тест для напоминалок с реальной БД.

Запуск: pytest tests/manual_reminders_integration.py
Не входит в авто-discover (префикс manual_), т.к. глобальный engine
из src.db.session конфликтует с event loop'ами других тестов.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from src.core.reminders import RE_REMINDER_HOURS, _check_once
from src.db.models import Commitment
from src.db.repo import get_or_create_user


@pytest.mark.asyncio
async def test_reminders_integration(session):
    """Сквозной сценарий напоминалок:

    1. Overdue: первый раз → отправлено + last_reminded_at
    2. Overdue: повторный вызов сразу → без уведомления
    3. Overdue: сдвиг last_reminded_at на 7ч → повторно отправлено
    4. Lead: первый раз → отправлено (lead одноразовое)
    5. Lead: повторный вызов → без уведомления
    """
    # ── prepare owner ────────────────────────────────────────────────────────
    uid = int(time.time_ns() % 10_000_000 + 42)
    owner = await get_or_create_user(session, uid)
    owner.settings.reminders_enabled = True
    owner.settings.reminder_overdue_enabled = True
    owner.settings.reminder_lead_hours = 2
    owner.settings.timezone = "UTC"

    # ── overdue commitment ───────────────────────────────────────────────────
    past = datetime.utcnow() - timedelta(hours=3)
    session.add(
        Commitment(
            user_id=owner.id,
            peer_id=12345,
            peer_name="Коллега",
            direction="theirs",
            text="Сдать отчёт",
            deadline_at=past,
            status="open",
        )
    )
    # ── lead commitment ──────────────────────────────────────────────────────
    future = datetime.utcnow() + timedelta(hours=1)
    session.add(
        Commitment(
            user_id=owner.id,
            peer_id=12346,
            peer_name="Я",
            direction="mine",
            text="Подготовить презентацию",
            deadline_at=future,
            status="open",
        )
    )
    await session.commit()

    # ══════════════════════════════════════════════════════════════════════════
    # 1. Первый вызов: overdue + lead → 2 уведомления
    # ══════════════════════════════════════════════════════════════════════════
    with patch("src.core.reminders.notifier.notify", new_callable=AsyncMock) as mock_notify:
        await _check_once(uid)

    assert mock_notify.await_count == 2
    texts = [call[0][0] for call in mock_notify.await_args_list]
    assert any("Просрочено" in t and "Сдать отчёт" in t for t in texts)
    assert any("Скоро дедлайн" in t and "Подготовить презентацию" in t for t in texts)

    # Проверяем last_reminded_at
    result = await session.execute(
        select(Commitment).where(
            Commitment.user_id == owner.id,
            Commitment.status == "open",
        ).order_by(Commitment.deadline_at)
    )
    commitments = list(result.scalars().all())
    assert len(commitments) == 2
    c_overdue = commitments[0]
    c_lead = commitments[1]
    assert c_overdue.last_reminded_at is not None
    assert c_lead.last_reminded_at is not None
    first_reminded = c_overdue.last_reminded_at

    # ══════════════════════════════════════════════════════════════════════════
    # 2. Повторный вызов сразу: 0 уведомлений
    # ══════════════════════════════════════════════════════════════════════════
    with patch("src.core.reminders.notifier.notify", new_callable=AsyncMock) as mock_notify:
        await _check_once(uid)

    mock_notify.assert_not_awaited()

    # ══════════════════════════════════════════════════════════════════════════
    # 3. Сдвиг last_reminded_at на 7ч → overdue повторно
    # ══════════════════════════════════════════════════════════════════════════
    c_overdue.last_reminded_at = datetime.utcnow() - timedelta(hours=RE_REMINDER_HOURS + 1)
    c_lead.last_reminded_at = datetime.utcnow() - timedelta(hours=RE_REMINDER_HOURS + 1)
    await session.commit()

    with patch("src.core.reminders.notifier.notify", new_callable=AsyncMock) as mock_notify:
        await _check_once(uid)

    # Только overdue должен повториться (lead — одноразовый, но после дедлайна
    # lead не сработает, а overdue да)
    mock_notify.assert_awaited_once()
    text = mock_notify.await_args[0][0]
    assert "Просрочено" in text
    assert "Сдать отчёт" in text

    await session.refresh(c_overdue)
    assert c_overdue.last_reminded_at > first_reminded
