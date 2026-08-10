import asyncio
import json as json_mod
import logging
import hashlib
import hmac
from pathlib import Path

import aiofiles
import aiohttp
from aiohttp import web

from src.config import settings

logger = logging.getLogger(__name__)

_runner = None

_active_tasks: set[asyncio.Task] = set()


def extract_event_id(payload: dict) -> str | None:
    """Извлекает event_id из payload вебхука МТС Линк.
    Поддерживает несколько вариантов ключей.
    ВНИМАНИЕ: реальные имена ключей должны быть подтверждены
    на первом живом webhook — отредактируй candidates под реальный JSON."""
    candidates = [
        payload.get("eventId"),
        payload.get("event_id"),
        payload.get("session_id"),
        payload.get("data", {}).get("eventId") if isinstance(payload.get("data"), dict) else None,
        payload.get("data", {}).get("event_id") if isinstance(payload.get("data"), dict) else None,
        payload.get("data", {}).get("eventSessionId") if isinstance(payload.get("data"), dict) else None,
    ]
    for val in candidates:
        if val:
            return str(val)
    return None


def extract_record_id(payload: dict) -> str | None:
    """Извлекает record_id из payload вебхука МТС Линк.
    Поддерживает несколько вариантов ключей.
    ВНИМАНИЕ: реальные имена ключей должны быть подтверждены
    на первом живом webhook — отредактируй candidates под реальный JSON."""
    candidates = [
        payload.get("recordId"),
        payload.get("record_id"),
        payload.get("file_id"),
        payload.get("data", {}).get("recordId") if isinstance(payload.get("data"), dict) else None,
        payload.get("data", {}).get("record_id") if isinstance(payload.get("data"), dict) else None,
    ]
    for val in candidates:
        if val:
            return str(val)
    return None


def _health(_: web.Request) -> web.Response:
    """Liveness endpoint. Не зависит от LLM/MTS Link — только факт работы процесса."""
    return web.json_response({"status": "ok"})


async def handle_mtslink_webhook(request: web.Request) -> web.Response:
    # Проверка секрета/подписи вебхука
    secret = settings.WEBHOOK_SECRET
    if secret:
        actual = request.headers.get("X-Webhook-Secret", "")
        if not hmac.compare_digest(actual, secret):
            logger.warning("webhook: invalid secret (got=%s)", actual[:8] if actual else "none")
            return web.Response(status=401, text="unauthorized")

    data = await request.json()
    event = data.get("event") or data.get("type", "unknown")

    logger.info(
        "=== WEBHOOK RECEIVED === event=%s",
        event,
    )

    if event == "recordFile.ready":
        event_id = extract_event_id(data)
        record_id = extract_record_id(data)
        logger.info("Extracted from payload: event_id=%s record_id=%s", event_id, record_id)

        if not event_id:
            logger.warning("Cannot process webhook: event_id not found in payload. Check candidates in extract_event_id()")
            logger.warning("Available top-level keys: %s", list(data.keys()))
            return web.Response(status=200, text="ignored: no event_id")

        if not record_id:
            logger.warning("Cannot process webhook: record_id not found in payload. Check candidates in extract_record_id()")
            logger.warning("Available top-level keys: %s", list(data.keys()))
            return web.Response(status=200, text="ignored: no record_id")

        task = asyncio.create_task(_handle_record_ready(data, event_id, record_id))
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)
    else:
        logger.info("Ignoring unhandled event type: %s", event)

    return web.Response(status=200, text="ok")


async def _handle_record_ready(data: dict, event_id: str, record_id: str) -> None:
    from src.db.session import get_session
    from src.db.repo import (
        get_meeting_by_mtslink_id,
        get_meeting_by_mtslink_session_id,
        get_meeting_by_record_id,
        update_meeting_status,
        update_meeting_record_id,
    )
    from src.services.mtslink_api import get_recording_download_url

    try:
        async with get_session() as session:
            meeting = await get_meeting_by_mtslink_id(session, event_id)
            if not meeting:
                meeting = await get_meeting_by_mtslink_session_id(session, event_id)
            if not meeting:
                logger.warning("No meeting found for event_id/session_id=%s", event_id)
                return
            logger.info("Found meeting id=%s status=%s", meeting.id, meeting.status)

            existing = await get_meeting_by_record_id(session, record_id)
            if existing and existing.id != meeting.id:
                logger.warning("Record %s already belongs to meeting %s, skipping", record_id, existing.id)
                return

            if meeting.status not in ("recording", "active"):
                logger.info("Meeting %s already processed (status=%s), skipping", meeting.id, meeting.status)
                return

            await update_meeting_status(session, meeting.id, "downloading")
            if meeting.mtslink_record_id is None:
                await update_meeting_record_id(session, meeting.id, record_id)

            team = meeting.team

        from src.services.crypto_service import crypto_service
        mtslink_token = await crypto_service.decrypt_data(team.mtslink_token, fallback_raw=True) if team else None
        if not mtslink_token:
            logger.warning("MTS Link token not found for meeting %s (team=%s)", meeting.id, team.id if team else "?")
            async with get_session() as session:
                await update_meeting_status(session, meeting.id, "failed")
            return

        logger.info(
            "Processing meeting %s, token source=team.mtslink_token, team=%s",
            meeting.id, team.id,
        )

        session_id = meeting.mtslink_session_id or event_id
        dl_url = await get_recording_download_url(mtslink_token, session_id, record_id)
        if not dl_url:
            logger.warning("No download URL for event_id %s (meeting %s)", event_id, meeting.id)
            async with get_session() as session:
                await update_meeting_status(session, meeting.id, "failed")
            return

        from src.bot.app import _bot

        if _bot is None:
            logger.error("Bot not initialized, cannot send meeting result")
            async with get_session() as session:
                await update_meeting_status(session, meeting.id, "failed")
            return

        logger.info("Spawning background task for meeting=%s", meeting.id)
        task = asyncio.create_task(
            download_and_process_meeting(
                download_url=dl_url,
                meeting_id=meeting.id,
                bot=_bot,
                chat_id=team.chat_id,
                team=team,
                record_id=record_id,
            )
        )
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)
    except Exception:
        logger.exception("PIPELINE FAILED (pre-download): event_id=%s record_id=%s", event_id, record_id)
        async with get_session() as session:
            try:
                meeting = await get_meeting_by_mtslink_id(session, event_id)
                if meeting:
                    await update_meeting_status(session, meeting.id, "failed")
            except Exception:
                pass


async def download_and_process_meeting(
    download_url: str,
    meeting_id: int,
    bot,
    chat_id: int,
    team,
    record_id: str,
) -> None:
    from src.db.session import get_session
    from src.db.repo import update_meeting_status
    from src.core.meeting_processor import process_meeting_audio

    media_dir = Path(settings.data_dir) / "media" / "meetings"
    media_dir.mkdir(parents=True, exist_ok=True)

    raw_path = media_dir / f"raw_{meeting_id}.mp4"
    audio_path = media_dir / f"audio_{meeting_id}.mp3"

    try:
        logger.info("DOWNLOAD STARTED: meeting=%s record=%s", meeting_id, record_id)

        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Download failed with status {resp.status}")
                async with aiofiles.open(str(raw_path), "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        await f.write(chunk)

        logger.info("DOWNLOAD DONE: meeting=%s size=%dMB", meeting_id, raw_path.stat().st_size // 1024 // 1024)

        logger.info("FFMPEG STARTED: meeting=%s", meeting_id)
        import subprocess as _subprocess

        loop = asyncio.get_running_loop()

        def _run_ffmpeg() -> tuple[int, str]:
            result = _subprocess.run(
                ["ffmpeg", "-y",
                 "-i", str(raw_path),
                 "-vn",
                 "-acodec", "libmp3lame",
                 "-q:a", "5",
                 str(audio_path)],
                capture_output=True, text=True,
            )
            return result.returncode, result.stderr

        rc, stderr = await loop.run_in_executor(None, _run_ffmpeg)
        if rc != 0:
            raise RuntimeError(f"ffmpeg failed (code={rc}): {stderr[:500]}")

        logger.info("FFMPEG DONE: meeting=%s", meeting_id)

        raw_path.unlink(missing_ok=True)
        logger.info("Cleaned up raw video for meeting=%s", meeting_id)

        async with get_session() as session:
            await update_meeting_status(session, meeting_id, "processing")

        logger.info("PIPELINE STARTED: meeting=%s audio=%s", meeting_id, audio_path)
        await process_meeting_audio(
            audio_path=audio_path,
            meeting_id=meeting_id,
            chat_id=chat_id,
            bot=bot,
            team=team,
            owner_telegram_id=team.owner_telegram_id or None,
        )

        bot_msg = "✅ Сигнал обработан. Транскрипция и задачи сформированы."
        await bot.send_message(chat_id, bot_msg)

    except Exception as e:
        logger.exception("PIPELINE FAILED: meeting=%s record=%s", meeting_id, record_id)
        error_text = str(e)[:300]
        async with get_session() as session:
            try:
                await update_meeting_status(session, meeting_id, "failed")
            except Exception:
                pass
        try:
            await bot.send_message(chat_id, "⚠️ Сбой пайплайна обработки записи. Администратор уведомлён.")
        except Exception:
            pass
    finally:
        for p in (raw_path, audio_path):
            try:
                if p.exists():
                    p.unlink(missing_ok=True)
            except Exception:
                pass


def build_app() -> web.Application:
    """Собирает aiohttp-приложение с webhook и health endpoint."""
    app = web.Application()
    app.router.add_post("/webhooks/mtslink", handle_mtslink_webhook)
    app.router.add_get("/health", _health)
    return app


async def run_webhook_server() -> None:
    """Запускает HTTP-сервер как часть lifecycle приложения.

    Слушает 0.0.0.0:$PORT (Railway задаёт PORT сам) и работает
    до отмены задачи; cleanup выполняется в stop_webhook_server().
    """
    global _runner

    if _runner is not None:
        logger.warning("webhook server already running")
        return

    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.PORT)
    await site.start()
    _runner = runner
    logger.info("Webhook server started on 0.0.0.0:%d", settings.PORT)

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        logger.info("Webhook server task cancelled")
        raise
    finally:
        await stop_webhook_server()


async def stop_webhook_server() -> None:
    global _runner

    if _active_tasks:
        logger.info("Waiting for %d active webhook tasks to finish...", len(_active_tasks))
        done, pending = await asyncio.wait(list(_active_tasks), timeout=30.0)
        if pending:
            logger.warning("%d webhook tasks did not finish in time", len(pending))
        for t in pending:
            t.cancel()

    runner = _runner
    _runner = None
    if runner is not None:
        await runner.cleanup()
