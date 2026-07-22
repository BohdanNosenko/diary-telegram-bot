import asyncio
import structlog
import telebot
from telebot.async_telebot import AsyncTeleBot

from vlog_journal.bot.handlers import register_handlers
from vlog_journal.bot.middleware import WhitelistMiddleware
from vlog_journal.bot.state import SessionManager
from vlog_journal.config import AppSettings

logger = structlog.get_logger(__name__)

def create_bot(settings: AppSettings, session_manager: SessionManager | None = None) -> tuple[AsyncTeleBot, SessionManager]:
    """Factory function to initialize AsyncTeleBot with middleware, session manager, and handlers."""
    token = settings.telegram_bot_token.get_secret_value()

    if settings.telegram_local_api_url:
        local_url = settings.telegram_local_api_url.rstrip("/")
        telebot.apihelper.API_URL = f"{local_url}/bot{{0}}/{{1}}"
        telebot.apihelper.FILE_URL = f"{local_url}/file/bot{{0}}/{{1}}"
        logger.info("Configured custom Telegram Bot API URL", local_api_url=local_url)

    bot = AsyncTeleBot(token)

    if session_manager is None:
        session_manager = SessionManager(settings.app.sessions_state_file)

    # Setup Whitelist Middleware
    whitelist_middleware = WhitelistMiddleware(settings.allowed_user_ids)
    bot.setup_middleware(whitelist_middleware)
    logger.info("Registered WhitelistMiddleware", allowed_count=len(whitelist_middleware.allowed_user_ids))

    # Register Handlers with session_manager
    register_handlers(bot, session_manager)
    logger.info("Registered command and media handlers")

    return bot, session_manager

async def run_crash_recovery(bot: AsyncTeleBot, session_manager: SessionManager, timeout_hours: int = 12) -> None:
    """Check for pending reviews, stale collecting sessions, and interrupted processing sessions on startup."""
    # 1. Check pending reviews (draft_pending)
    pending_reviews = session_manager.get_pending_reviews()
    for chat_id, session in pending_reviews:
        logger.info("Crash recovery: Found pending draft review", chat_id=chat_id)
        try:
            await bot.send_message(
                chat_id,
                "⚠️ **Bot restarted!** You have a pending draft ready for review. Use `/status` to view.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("Failed to send crash recovery notification", chat_id=chat_id, error=str(e))

    # 2. Check stale collecting sessions
    stale_sessions = session_manager.get_stale_sessions(timeout_hours=timeout_hours)
    for chat_id, session in stale_sessions:
        clip_count = len(session.get("clips", []))
        logger.info("Found stale collecting session", chat_id=chat_id, clip_count=clip_count)
        try:
            await bot.send_message(
                chat_id,
                f"⏰ **Reminder:** You have an active session with {clip_count} clip(s) from earlier. Run `/finish_session` to process or `/cancel` to discard.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("Failed to send stale session reminder", chat_id=chat_id, error=str(e))

    # 3. Check interrupted processing sessions
    interrupted = session_manager.get_interrupted_processing()
    for chat_id, session in interrupted:
        progress = session.get("pipeline_progress", 0)
        if progress > 0:
            logger.info("Interrupted processing session found with progress > 0", chat_id=chat_id, progress=progress)
            try:
                await bot.send_message(
                    chat_id,
                    f"🔄 **Bot restarted during processing!** Session was at step {progress}. Use `/retry` to resume.",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning("Failed to send interrupted session notification", chat_id=chat_id, error=str(e))
        else:
            logger.info("Interrupted processing session with 0 progress, reverting to collecting", chat_id=chat_id)
            session_manager.set_status(chat_id, "collecting")
            try:
                await bot.send_message(
                    chat_id,
                    "⚠️ **Bot restarted during processing startup.** Session status reverted to `collecting`. Run `/finish_session` to try again.",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning("Failed to send status revert notification", chat_id=chat_id, error=str(e))

async def start_bot(settings: AppSettings) -> None:
    """Main entrypoint to run the Telegram bot polling loop with crash recovery."""
    bot, session_manager = create_bot(settings)

    # Execute Crash Recovery Check
    await run_crash_recovery(bot, session_manager, timeout_hours=settings.app.session_timeout_hours)

    logger.info("Starting Telegram Bot infinity polling loop...")
    try:
        await bot.infinity_polling(timeout=10, request_timeout=60)
    except asyncio.CancelledError:
        logger.info("Bot polling loop cancelled")
    except Exception as e:
        logger.error("Unexpected error in bot polling loop", error=str(e))
        raise
