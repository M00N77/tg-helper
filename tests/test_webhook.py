"""Тесты webhook endpoint: /health, секрет, обработка payload."""
import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.config import settings
from src.services.webhook_server import build_app, _active_tasks


@pytest.fixture
async def webhook_client():
    client = TestClient(TestServer(build_app()))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


async def test_health_returns_ok(webhook_client):
    resp = await webhook_client.get("/health")
    assert resp.status == 200
    assert await resp.json() == {"status": "ok"}


async def test_webhook_rejects_bad_secret(webhook_client, monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "super-secret")
    resp = await webhook_client.post(
        "/webhooks/mtslink",
        json={"event": "recordFile.ready"},
    )
    assert resp.status == 401


async def test_webhook_accepts_valid_secret_no_event_id(webhook_client, monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "super-secret")
    resp = await webhook_client.post(
        "/webhooks/mtslink",
        json={"event": "recordFile.ready"},
        headers={"X-Webhook-Secret": "super-secret"},
    )
    assert resp.status == 200
    assert "ignored" in await resp.text()


async def test_webhook_missing_record_id_ignored(webhook_client, monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "")
    resp = await webhook_client.post(
        "/webhooks/mtslink",
        json={"event": "recordFile.ready", "eventId": "evt-1"},
    )
    assert resp.status == 200
    assert "ignored" in await resp.text()


async def test_webhook_valid_payload_spawns_task(webhook_client, monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "")
    resp = await webhook_client.post(
        "/webhooks/mtslink",
        json={"event": "recordFile.ready", "eventId": "evt-1", "recordId": "rec-1"},
    )
    assert resp.status == 200
    assert await resp.text() == "ok"
    # дождаться завершения фоновой обработки (meeting не найден — безопасно)
    for _ in range(50):
        if not _active_tasks:
            break
        await asyncio.sleep(0.05)
    assert not _active_tasks