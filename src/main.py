import asyncio
import logging
import signal
import sys
from collections.abc import Coroutine

from src.bot.app import run_bot
from src.core.auto_sync import auto_sync_loop
from src.core.digest import digest_scheduler_loop
from src.core.evening_digest import evening_digest_loop
from src.core.lifecycle import shutdown_all, wait_until_stopped, with_timeout
from src.core.news import news_scheduler_loop
from src.core.reminders import reminders_loop
from src.core.standup_scheduler import standup_scheduler_loop, blocker_escalation_loop
from src.core.vector_store import vector_store
from src.group_bot.activities.scheduler import activities_scheduler_loop
from src.db.session import init_db, close_db
from src.userbot.manager import UserbotManager


logger = logging.getLogger(__name__)

INIT_DB_TIMEOUT = 60.0
RESTORE_TIMEOUT = 120.0
SHUTDOWN_AWAIT_TIMEOUT = 30.0


async def _clean_trash_loop() -> None:
    from src.db.repo import hard_delete_expired_trash
    from src.db.session import get_session
    while True:
        try:
            async with get_session() as session:
                deleted = await hard_delete_expired_trash(session)
                if deleted:
                    logger.info("Trash cleaner: hard-deleted %d expired commitments", deleted)
        except Exception:
            logger.exception("trash cleaner error")
        await asyncio.sleep(3600)


def _background_loops() -> dict[str, Coroutine[None, None, None]]:
    """Именованные coroutine-объекты фоновых задач (стартуют в main)."""
    return {
        "digest-scheduler": digest_scheduler_loop(),
        "evening-digest": evening_digest_loop(),
        "reminders-loop": reminders_loop(),
        "news-scheduler": news_scheduler_loop(),
        "auto-sync": auto_sync_loop(),
        "trash-cleaner": _clean_trash_loop(),
        "standup-scheduler": standup_scheduler_loop(),
        "blocker-escalation": blocker_escalation_loop(),
        "activities-scheduler": activities_scheduler_loop(),
    }


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    logger.info("Starting TelegramAssistant")

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Received termination signal, shutting down...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except (NotImplementedError, ValueError):
            pass

    userbot_manager: UserbotManager | None = None
    supervised: dict[str, asyncio.Task] = {}
    crash: BaseException | None = None

    try:
        await with_timeout(
            init_db(),
            INIT_DB_TIMEOUT,
            f"DB init failed (timeout after {INIT_DB_TIMEOUT}s)",
        )

        userbot_manager = UserbotManager()
        await with_timeout(
            userbot_manager.restore_all(),
            RESTORE_TIMEOUT,
            f"restore_all() hang: userbots not restored within {RESTORE_TIMEOUT}s",
        )

        supervised["bot"] = asyncio.create_task(run_bot(userbot_manager), name="bot")
        for name, coro in _background_loops().items():
            supervised[name] = asyncio.create_task(coro, name=name)

        crashed_task = await wait_until_stopped(stop_event, supervised)
        if crashed_task is not None:
            exc = crashed_task.exception() or RuntimeError("unknown task failure")
            crash = exc
            logger.critical("Task '%s' crashed: %s", crashed_task.get_name(), exc)
    except asyncio.CancelledError:
        logger.info("Main task cancelled")
    except Exception as exc:
        crash = exc
        logger.critical("Fatal error: %s", exc)
        logger.exception("Fatal error details")
    finally:
        await shutdown_all(supervised, cancel_timeout=SHUTDOWN_AWAIT_TIMEOUT)

        from src.services.webhook_server import stop_webhook_server
        await stop_webhook_server()

        if userbot_manager is not None:
            await userbot_manager.close_all()
        await vector_store.close()
        await close_db()
        logger.info("Shutdown complete")

    if crash is not None:
        raise crash


def run() -> None:
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown requested")
    except RuntimeError as exc:
        logger.critical("Terminated after cleanup: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    run()