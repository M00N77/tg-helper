import asyncio
import logging

import aiohttp

logger = logging.getLogger(__name__)
MTSLINK_API_BASE = "https://userapi.mts-link.ru/v3"


async def register_record_webhook(
    token: str,
    event_id: str,
    callback_url: str,
) -> bool:
    url = f"{MTSLINK_API_BASE}/webhooks/create"
    headers = {"x-auth-token": token, "Content-Type": "application/json"}
    body = {
        "endpointUrl": callback_url,
        "name": "TelegramHelper webhook",
        "eventTypeNames": ["recordFile.ready", "eventSession.ended"],
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body, headers=headers) as resp:
            ok = resp.status in (200, 201, 409)
            if not ok:
                text = await resp.text()
                if resp.status == 400 and "WEBHOOK_URL_ALREADY_EXISTS" in text:
                    logger.info("Webhook already registered (400/ALREADY_EXISTS)")
                    ok = True
                else:
                    logger.warning("register_webhook failed %d: %s", resp.status, text)
            elif resp.status == 409:
                logger.info("Webhook already registered (409)")
            return ok


async def get_recording_download_url(token: str, event_session_id: str, record_id: str) -> str | None:
    """
    Поллит GET /{base}/eventsessions/{id}/converted-records пока не появится MP4.
    recordFile.ready означает только что сырая запись есть,
    конвертация в MP4 может занять несколько минут (до 15).
    Параметры record_id и token сохраняются для совместимости вызова,
    реально используются только event_session_id и token.
    """
    url = f"{MTSLINK_API_BASE}/eventsessions/{event_session_id}/converted-records"
    headers = {"x-auth-token": token}
    max_attempts = 15
    interval_sec = 60

    for attempt in range(max_attempts):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        records = await resp.json()
                        if isinstance(records, list) and records:
                            rec = records[0]
                            download_url = (
                                rec.get("url")
                                or rec.get("downloadUrl")
                                or rec.get("fileUrl")
                            )
                            if download_url:
                                logger.info(
                                    "download_url получен на попытке %d: %s",
                                    attempt + 1, download_url,
                                )
                                return download_url
        except Exception as e:
            logger.warning(
                "converted-records attempt %d/%d error: %s",
                attempt + 1, max_attempts, e,
            )
        if attempt < max_attempts - 1:
            await asyncio.sleep(interval_sec)

    logger.warning(
        "converted-records не появился за %dс для session %s",
        max_attempts * interval_sec, event_session_id,
    )
    return None


async def download_recording(url: str, dest_path: str) -> bool:
    import aiofiles

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return False
            async with aiofiles.open(dest_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 64):
                    await f.write(chunk)
    return True
