import logging

from aiogram import Bot, Dispatcher, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from src.bot.handlers import (
    catchup_cmd,
    chat_cmd,
    digest_cmd,
    digest_evening_cmd,
    free_text,
    kanban,
    kanban_analytics,
    dashboard,
    weekly,
    burnout,
    team_mood,
    sentiment_stats,
    tasks_rating,
    login,
    meeting,
    menu,
    news_cmd,
    news_topics,
    search,
    send,
    settings as settings_handlers,
    setup_yougile,
    start,
    style_cmd,
    team as team_handlers,
    todos,
    standup as standup_handlers,
    blockers as blockers_handlers,
    activities as activities_handlers,
)
from src.group_bot.handlers import (
    director as group_director,
    free_text as group_free_text,
    link as group_link,
    risks as group_risks,
    setup_kanban as group_setup_kanban,
    tasks as group_tasks,
)
from src.config import settings
from src.core.notifier import notifier
from src.bot.middlewares.invite_check import InviteCheckMiddleware
from src.userbot.manager import UserbotManager

from src.services.ngrok_tunnel import start_tunnel, stop_tunnel
from src.services.webhook_server import start_webhook_server, stop_webhook_server


logger = logging.getLogger(__name__)


debug_logger = logging.getLogger("debug.catch_all")
debug_router = Router(name="debug_catch_all")


@debug_router.message()
async def catch_all_debug(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    debug_logger.info(
        "[CATCH-ALL] text=%s chat_id=%s user_id=%s state=%s",
        (message.text or "(non-text)")[:100],
        message.chat.id,
        message.from_user.id if message.from_user else None,
        current_state,
    )

_bot: Bot | None = None


def get_bot() -> Bot | None:
    return _bot


async def run_bot(userbot_manager: UserbotManager) -> None:
    global _bot
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    _bot = bot
    notifier.attach(bot)

    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(InviteCheckMiddleware())

    dp["userbot_manager"] = userbot_manager

    dp.include_router(start.router)
    dp.include_router(login.router)
    dp.include_router(menu.router)
    dp.include_router(settings_handlers.router)
    dp.include_router(chat_cmd.router)
    dp.include_router(catchup_cmd.router)
    dp.include_router(send.router)
    dp.include_router(search.router)
    dp.include_router(todos.router)
    dp.include_router(digest_cmd.router)
    dp.include_router(digest_evening_cmd.router)
    dp.include_router(style_cmd.router)
    dp.include_router(news_cmd.router)
    dp.include_router(news_topics.router)
    dp.include_router(kanban.router)
    dp.include_router(setup_yougile.router)
    dp.include_router(kanban_analytics.router)
    dp.include_router(dashboard.router)
    dp.include_router(weekly.router)
    dp.include_router(burnout.router)
    dp.include_router(team_mood.router)
    dp.include_router(sentiment_stats.router)
    dp.include_router(tasks_rating.router)
    dp.include_router(meeting.router)
    dp.include_router(team_handlers.router)
    dp.include_router(standup_handlers.router)
    dp.include_router(blockers_handlers.router)
    dp.include_router(activities_handlers.router)
    # Групповой HR-функционал (создание задач, права участников, согласование).
    # GroupOnly-фильтр гарантирует, что эти роутеры срабатывают только в группах.
    dp.include_router(group_director.router)
    dp.include_router(group_setup_kanban.router)
    dp.include_router(group_link.make_router())
    dp.include_router(group_risks.router)
    dp.include_router(group_tasks.router)
    dp.include_router(group_free_text.router)
    # ВАЖНО: free_text — самым последним, чтобы команды и FSM перехватили текст раньше
    dp.include_router(free_text.router)
    # Catch-all debug роутер — логирует все необработанные сообщения
    dp.include_router(debug_router)

    try:
        me = await bot.get_me()
        logger.info("Control bot started as @%s", me.username)

        from src.services import webhook_server as ws_module

        public_url = await start_tunnel()
        if public_url:
            ws_module.PUBLIC_WEBHOOK_URL = public_url + "/webhook/mtslink"
            logger.info("Webhook URL: %s", ws_module.PUBLIC_WEBHOOK_URL)
        await start_webhook_server()

        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except Exception:
        logger.exception("Fatal error in run_bot")
        raise
    finally:
        await stop_webhook_server()
        await stop_tunnel()
        await bot.session.close()
        storage_close = getattr(dp.storage, 'close', None)
        if storage_close:
            await storage_close()
        await userbot_manager.close_all()
        logger.info("Bot shut down complete")
