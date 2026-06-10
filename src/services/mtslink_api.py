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


async def _trigger_conversion(token: str, event_session_id: str) -> int | None:
    """POST /eventsessions/{id}/records/conversions — запуск конвертации в MP4.
    Возвращает conversionId или None при ошибке."""
    url = f"{MTSLINK_API_BASE}/eventsessions/{event_session_id}/records/conversions"
    headers = {"x-auth-token": token, "Content-Type": "application/x-www-form-urlencoded"}
    body = {"quality": "1080", "view": "chat"}

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(url, data=body, headers=headers) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    conv_id = data.get("id") if isinstance(data, dict) else None
                    logger.info(
                        "MTSLINK: conversion triggered for session=%s, conversionId=%s",
                        event_session_id, conv_id,
                    )
                    return conv_id
                else:
                    text = await resp.text()
                    logger.warning(
                        "MTSLINK: trigger conversion failed %d for session=%s: %s",
                        resp.status, event_session_id, text,
                    )
                    return None
    except Exception as e:
        logger.warning(
            "MTSLINK: trigger conversion error for session=%s: %s",
            event_session_id, e,
        )
        return None


async def get_recording_download_url(token: str, event_session_id: str, record_id: str) -> str | None:
    """
    1. Запускает конвертацию записи в MP4 (POST …/records/conversions).
    2. Поллит GET /{base}/eventsessions/{id}/converted-records пока не появится downloadUrl.
    Ответ по документации: {"downloadUrl": "https://…"}.
    """
    url = f"{MTSLINK_API_BASE}/eventsessions/{event_session_id}/converted-records"
    headers = {"x-auth-token": token}
    max_attempts = 30
    interval_sec = 30

    conv_id = await _trigger_conversion(token, event_session_id)
    logger.info(
        "MTSLINK: starting poll for session=%s record=%s conv_id=%s url=%s",
        event_session_id, record_id, conv_id, url,
    )

    for attempt in range(max_attempts):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get(url, headers=headers) as resp:
                    logger.info("MTSLINK: attempt=%d HTTP status=%d", attempt + 1, resp.status)
                    if resp.status != 200:
                        logger.info("MTSLINK: non-200 status=%d -> skip", resp.status)
                        continue

                    raw_text = await resp.text()
                    logger.info("MTSLINK: raw body (%d chars)=%s", len(raw_text), raw_text)

                    try:
                        parsed = __import__("json").loads(raw_text)
                    except Exception as e:
                        logger.warning("MTSLINK: json parse error: %s", e)
                        continue

                    logger.info("MTSLINK: parsed type=%s repr=%s", type(parsed).__name__, repr(parsed))

                    download_url = None

                    if isinstance(parsed, dict):
                        logger.info("MTSLINK: top-level keys=%s", list(parsed.keys()))
                        download_url = parsed.get("downloadUrl")

                    elif isinstance(parsed, list):
                        logger.info("MTSLINK: list length=%d", len(parsed))
                        if parsed and isinstance(parsed[0], dict):
                            logger.info("MTSLINK: first element keys=%s", list(parsed[0].keys()))
                            download_url = (
                                parsed[0].get("url")
                                or parsed[0].get("downloadUrl")
                                or parsed[0].get("fileUrl")
                            )

                    if download_url:
                        logger.info("MTSLINK: download_url получен на попытке %d: %s", attempt + 1, download_url)
                        return download_url

                    logger.info("MTSLINK: download_url not found yet on attempt %d", attempt + 1)

        except Exception as e:
            logger.warning("MTSLINK: attempt %d/%d error: %s", attempt + 1, max_attempts, e)

        if attempt < max_attempts - 1:
            await asyncio.sleep(interval_sec)

    logger.warning("MTSLINK: download_url не появился за %dс для session %s", max_attempts * interval_sec, event_session_id)
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
