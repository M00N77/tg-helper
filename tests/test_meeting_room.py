"""Тесты MTS Link meeting_room: error contract и soft-fail webhook registration."""
import httpx
import pytest

from src.config import settings
from src.services import meeting_room
from src.services.meeting_room import create_mtslink_room


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        assert self._payload is not None
        return self._payload


class FakeClient:
    """httpx.AsyncClient-заглушка, записывает вызовы и отдаёт сценарий ответов."""

    def __init__(self, responses: dict[str, FakeResponse], fail_with: Exception | None = None):
        self.responses = responses
        self.fail_with = fail_with
        self.calls: list[dict] = []

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def post(self, url: str, headers=None, data=None, json=None):
        self.calls.append({"url": url, "data": data, "json": json})
        if self.fail_with is not None:
            raise self.fail_with
        for marker, resp in self.responses.items():
            if url.endswith(marker):
                return resp
        raise AssertionError(f"unexpected url: {url}")


@pytest.fixture
def fake_httpx(monkeypatch):
    def _install(client: FakeClient) -> None:
        monkeypatch.setattr(meeting_room.httpx, "AsyncClient", lambda **kw: client)

    return _install


async def test_create_mtslink_room_success(fake_httpx):
    client = FakeClient({
        "/events": FakeResponse(200, {"eventId": "evt-1", "link": "https://meet/evt-1"}),
        "/sessions": FakeResponse(201, {"link": "https://meet/evt-1", "eventSessionId": "sess-1"}),
    })
    fake_httpx(client)

    link, event_id, session_id = await create_mtslink_room(
        "Синк", "token", starts_at=None, team_chat_id=None,
    )
    assert link == "https://meet/evt-1"
    assert event_id == "evt-1"
    assert session_id == "sess-1"

    # при создании сессии data передаётся словарём, а не None
    session_call = client.calls[1]
    assert session_call["data"] is not None
    assert session_call["data"]["startType"] == "autostart"


async def test_create_mtslink_room_session_failure_status_in_message(fake_httpx):
    client = FakeClient({
        "/events": FakeResponse(200, {"eventId": "evt-1", "link": "https://meet/evt-1"}),
        "/sessions": FakeResponse(503, payload=None, text="nope"),
    })
    fake_httpx(client)

    with pytest.raises(RuntimeError, match=r"сессию встречи \(503\)"):
        await create_mtslink_room("Синк", "token")


async def test_create_mtslink_room_event_timeout(fake_httpx):
    client = FakeClient({}, fail_with=httpx.TimeoutException("timeout"))
    fake_httpx(client)

    with pytest.raises(RuntimeError, match="таймаут"):
        await create_mtslink_room("Синк", "token")


async def test_create_mtslink_room_event_network_error(fake_httpx):
    client = FakeClient({}, fail_with=httpx.ConnectError("dns fail"))
    fake_httpx(client)

    with pytest.raises(RuntimeError, match="сетевой сбой"):
        await create_mtslink_room("Синк", "token")


async def test_webhook_registration_soft_fail(
    fake_httpx, monkeypatch,
):
    """Встреча создаётся успешно, даже если регистрация webhook упала."""
    monkeypatch.setattr(settings, "WEBHOOK_BASE_URL", "https://app.example.com")
    client = FakeClient({
        "/events": FakeResponse(200, {"eventId": "evt-1", "link": "https://meet/evt-1"}),
        "/sessions": FakeResponse(201, {"link": "https://meet/evt-1", "eventSessionId": "sess-1"}),
    })
    fake_httpx(client)

    registered: list[str] = []

    async def _broken_register(token: str, event_id: str, callback_url: str) -> bool:
        registered.append(callback_url)
        raise RuntimeError("MTS Link временно недоступен")

    monkeypatch.setattr(
        "src.services.mtslink_api.register_record_webhook",
        _broken_register,
    )

    link, event_id, session_id = await create_mtslink_room("Синк", "token")
    assert link == "https://meet/evt-1"
    assert event_id == "evt-1"
    assert session_id == "sess-1"
    assert registered == ["https://app.example.com/webhooks/mtslink"]


async def test_webhook_registration_uses_settings_url(fake_httpx, monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_BASE_URL", "https://tg-helper-production.up.railway.app/")
    client = FakeClient({
        "/events": FakeResponse(200, {"eventId": "evt-1", "link": "https://meet/evt-1"}),
        "/sessions": FakeResponse(201, {"link": "https://meet/evt-1", "eventSessionId": "sess-1"}),
    })
    fake_httpx(client)

    registered: list[str] = []

    async def _record_register(token: str, event_id: str, callback_url: str) -> bool:
        registered.append(callback_url)
        return True

    monkeypatch.setattr(
        "src.services.mtslink_api.register_record_webhook",
        _record_register,
    )

    await create_mtslink_room("Синк", "token")
    # trailing slash не должен давать двойной слэш
    assert registered == ["https://tg-helper-production.up.railway.app/webhooks/mtslink"]


async def test_mtslink_webhook_url_property(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_BASE_URL", "https://app.example.com/")
    assert settings.mtslink_webhook_url == "https://app.example.com/webhooks/mtslink"
    assert "//webhooks" not in settings.mtslink_webhook_url


async def test_mtslink_webhook_url_none_without_base(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_BASE_URL", "")
    assert settings.mtslink_webhook_url is None