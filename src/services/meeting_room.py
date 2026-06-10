from datetime import datetime, timezone, timedelta
import uuid
import httpx
import logging

JITSI_BASE = "https://meet.jit.si"
MTSLINK_API = "https://userapi.mts-link.ru/v3"

MSK = timezone(timedelta(hours=3))

logger = logging.getLogger(__name__)


def _slug(title: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title).strip()
    return safe.replace(" ", "_")[:30] or "meeting"


def _to_msk_iso(starts_at: str | None) -> str | None:
    if not starts_at:
        return None
    s = starts_at.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK)
    dt_msk = dt.astimezone(MSK)
    return dt_msk.strftime("%Y-%m-%dT%H:%M:%S+03:00")


def create_jitsi_room(title: str) -> str:
    room = f"{_slug(title)}-{uuid.uuid4().hex[:8]}"
    return f"{JITSI_BASE}/{room}"


async def create_mtslink_room(
    title: str,
    api_token: str,
    starts_at: str | None = None,
    team_chat_id: int | None = None,
) -> tuple[str, str | None, str | None]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {
            "x-auth-token": api_token,
            "Content-Type": "application/x-www-form-urlencoded",
        }

        event_data = {
            "name": title,
            "type": "meeting",
            "accessSettings[isPasswordRequired]": "0",
            "accessSettings[isRegistrationRequired]": "0",
            "accessSettings[isModerationRequired]": "0",
        }

        if starts_at:
            msk_iso = _to_msk_iso(starts_at)
        else:
            msk_iso = datetime.now(MSK).strftime("%Y-%m-%dT%H:%M:%S+03:00")
        event_data["startsAtTimestamp"] = msk_iso

        resp = await client.post(f"{MTSLINK_API}/events", headers=headers, data=event_data)
        if resp.status_code not in (200, 201):
            body = resp.text
            logger.warning(f"[MTSLink] create event failed: {resp.status_code} {body}")
            raise RuntimeError(f"МТС Линк: не удалось создать шаблон встречи ({resp.status_code})")

        event = resp.json()
        event_id = str(event.get("eventId") or event.get("data", {}).get("eventId") or "")
        if not event_id:
            raise RuntimeError("МТС Линк: ответ не содержит eventId")

        session_data = {"startType": "autostart"}
        if msk_iso:
            session_data["startsAtTimestamp"] = msk_iso

        sess_resp = await client.post(
            f"{MTSLINK_API}/events/{event_id}/sessions",
            headers=headers,
            data=session_data or None,
        )
        if sess_resp.status_code not in (200, 201):
            body = sess_resp.text
            logger.warning(f"[MTSLink] create session failed: {resp.status_code} {body}")
            raise RuntimeError(f"МТС Линк: не удалось создать сессию встречи ({sess_resp.status_code})")

        session = sess_resp.json()
        link = session.get("link") or event.get("link", "")
        session_id = str(
            session.get("eventSessionId")
            or session.get("id")
            or session.get("sessionId")
            or ""
        )
        if not link:
            raise RuntimeError("МТС Линк: ответ не содержит ссылку на встречу")

        from src.services.mtslink_api import register_record_webhook
        import src.services.webhook_server as ws_module

        callback_url = getattr(ws_module, "PUBLIC_WEBHOOK_URL", None)
        if callback_url:
            await register_record_webhook(api_token, event_id, callback_url)

        if team_chat_id:
            from src.db.session import get_session
            from src.db.repo import update_team_mtslink_token

            async with get_session() as session:
                await update_team_mtslink_token(session, team_chat_id, api_token)
            logger.info("Saved mtslink_token to team chat_id=%s", team_chat_id)

        return link, event_id, session_id or None


async def create_meeting_room(
    title: str,
    mtslink_token: str | None = None,
    starts_at: str | None = None,
    team_chat_id: int | None = None,
) -> tuple[str, str | None, str | None]:
    if mtslink_token:
        link, event_id, session_id = await create_mtslink_room(title, mtslink_token, starts_at, team_chat_id)
        return link, event_id, session_id
    return create_jitsi_room(title), None, None
