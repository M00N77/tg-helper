import asyncio
import logging
import signal
import sys

from src.bot.app import run_bot
from src.core.auto_sync import auto_sync_loop
from src.core.digest import digest_scheduler_loop
from src.core.evening_digest import evening_digest_loop
from src.core.news import news_scheduler_loop
from src.core.reminders import reminders_loop
from src.core.standup_scheduler import standup_scheduler_loop, blocker_escalation_loop
from src.core.vector_store import vector_store
from src.group_bot.activities.scheduler import activities_scheduler_loop
from src.db.session import init_db, get_session
from src.userbot.manager import UserbotManager


logger = logging.getLogger(__name__)


async def _clean_trash_loop() -> None:
    from src.db.repo import hard_delete_expired_trash
    while True:
        try:
            async with get_session() as session:
                deleted = await hard_delete_expired_trash(session)
                if deleted:
                    logger.info("Trash cleaner: hard-deleted %d expired commitments", deleted)
        except Exception:
            logger.exception("trash cleaner error")
        await asyncio.sleep(3600)


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

    await init_db()

    userbot_manager = UserbotManager()
    await userbot_manager.restore_all()

    bg_tasks = [
        asyncio.create_task(digest_scheduler_loop(), name="digest-scheduler"),
        asyncio.create_task(evening_digest_loop(), name="evening-digest"),
        asyncio.create_task(reminders_loop(), name="reminders-loop"),
        asyncio.create_task(news_scheduler_loop(), name="news-scheduler"),
        asyncio.create_task(auto_sync_loop(), name="auto-sync"),
        asyncio.create_task(_clean_trash_loop(), name="trash-cleaner"),
        asyncio.create_task(standup_scheduler_loop(), name="standup-scheduler"),
        asyncio.create_task(blocker_escalation_loop(), name="blocker-escalation"),
        asyncio.create_task(activities_scheduler_loop(), name="activities-scheduler"),
    ]

    try:
        await run_bot(userbot_manager)
    except (RuntimeError, Exception) as exc:
        logger.critical("Bot terminated: %s", exc)
        raise
    finally:
        stop_event.set()
        for t in bg_tasks:
            t.cancel()
        for t in bg_tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await userbot_manager.close_all()
        await vector_store.close()


def run() -> None:
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown requested")
    except RuntimeError as exc:
        logger.critical("Bot terminated after cleanup: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    run()
