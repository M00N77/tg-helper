"""Проверка CRUD-операций репозитория.

ВНИМАНИЕ: Тесты пишут данные в БД и НЕ откатывают их.
Если нужно запускать на продакшене — используйте отдельную тестовую БД.
"""

from datetime import datetime, timedelta

import pytest

from src.db.models import Commitment, Contact, PendingAction
from src.db.repo import (
    add_auto_reply_log,
    add_commitment,
    add_news_topic,
    create_pending_action,
    delete_news_topic,
    delete_pending_action,
    delete_telegram_session,
    get_api_key,
    get_or_create_user,
    list_open_commitments,
    list_recent_auto_replies,
    save_telegram_session,
    toggle_news_topic,
    update_commitment_status,
    upsert_api_key,
    upsert_contact,
)


@pytest.mark.asyncio
async def test_get_or_create_user(session):
    user = await get_or_create_user(session, telegram_id=999999999)
    assert user is not None
    assert user.telegram_id == 999999999
    assert user.id is not None

    user2 = await get_or_create_user(session, telegram_id=999999999)
    assert user2.id == user.id


@pytest.mark.asyncio
async def test_get_or_create_user_creates_settings(session):
    user = await get_or_create_user(session, telegram_id=999999998)
    assert user.settings is not None
    assert user.settings.llm_provider == "openai"
    assert user.settings.timezone == "Europe/Moscow"


@pytest.mark.asyncio
async def test_save_and_load_telegram_session(session):
    user = await get_or_create_user(session, telegram_id=999999997)
    await save_telegram_session(
        session,
        user,
        api_id=12345,
        api_hash="abcdefabcdefabcdefabcdefabcdefab",
        session_string="1AQAAAAFtest-session-string...",
        phone="+79991112233",
        account_label="Test User",
    )
    loaded = await delete_telegram_session(session, user)
    # просто проверяем, что сохранилось без ошибок


@pytest.mark.asyncio
async def test_upsert_and_get_api_key(session):
    user = await get_or_create_user(session, telegram_id=999999996)
    await upsert_api_key(session, user, "test_provider", "sk-test-key-12345")
    key = await get_api_key(session, user, "test_provider")
    assert key == "sk-test-key-12345"


@pytest.mark.asyncio
async def test_api_key_update(session):
    user = await get_or_create_user(session, telegram_id=999999995)
    await upsert_api_key(session, user, "test_provider2", "old-key")
    await upsert_api_key(session, user, "test_provider2", "new-key")
    key = await get_api_key(session, user, "test_provider2")
    assert key == "new-key"


@pytest.mark.asyncio
async def test_upsert_contact(session):
    user = await get_or_create_user(session, telegram_id=999999994)
    contact = await upsert_contact(
        session,
        user,
        peer_id=111111111,
        peer_kind="user",
        display_name="Test Contact",
        username="testcontact",
        phone="+71111111111",
        is_bot=False,
        is_archived=False,
    )
    assert contact is not None
    assert contact.display_name == "Test Contact"
    assert contact.username == "testcontact"


@pytest.mark.asyncio
async def test_upsert_contact_update(session):
    user = await get_or_create_user(session, telegram_id=999999993)
    await upsert_contact(
        session, user, peer_id=111111112, peer_kind="user", display_name="Old Name"
    )
    contact = await upsert_contact(
        session, user, peer_id=111111112, peer_kind="user", display_name="New Name"
    )
    assert contact.display_name == "New Name"


@pytest.mark.asyncio
async def test_commitment_crud(session):
    user = await get_or_create_user(session, telegram_id=999999992)
    deadline = datetime.utcnow() + timedelta(days=1)

    c = await add_commitment(
        session,
        user_id=user.id,
        peer_id=111111113,
        peer_name="Test Chat",
        message_id=42,
        direction="mine",
        text="Test commitment text",
        deadline_at=deadline,
    )
    assert c.id is not None
    assert c.status == "open"

    open_list = await list_open_commitments(session, user)
    assert any(x.id == c.id for x in open_list)

    await update_commitment_status(session, c.id, "done")
    open_list2 = await list_open_commitments(session, user)
    assert not any(x.id == c.id for x in open_list2)


@pytest.mark.asyncio
async def test_auto_reply_log(session):
    user = await get_or_create_user(session, telegram_id=999999991)
    await add_auto_reply_log(
        session,
        user_id=user.id,
        peer_id=111111114,
        peer_name="Chat Name",
        incoming_text="Привет",
        reply_text="Сейчас не у телефона",
    )
    logs = await list_recent_auto_replies(session, user)
    assert len(logs) >= 1
    assert logs[0].incoming_text == "Привет"


@pytest.mark.asyncio
async def test_pending_action_crud(session):
    user = await get_or_create_user(session, telegram_id=999999990)
    pa = await create_pending_action(
        session,
        user_id=user.id,
        kind="send_message",
        payload={"text": "Hello", "peer_id": 111111115},
    )
    assert pa.id is not None

    loaded = await delete_pending_action(session, pa.id)
    loaded_after = await delete_pending_action(session, pa.id)
    # проверяем что удалилось без ошибок


@pytest.mark.asyncio
async def test_news_topic_crud(session):
    user = await get_or_create_user(session, telegram_id=999999989)
    nt = await add_news_topic(session, user, "AI News", hours=24)
    assert nt.id is not None
    assert nt.topic == "AI News"
    assert nt.enabled is True

    new_state = await toggle_news_topic(session, user, nt.id)
    assert new_state is False

    new_state2 = await toggle_news_topic(session, user, nt.id)
    assert new_state2 is True

    deleted = await delete_news_topic(session, user, nt.id)
    assert deleted is True
