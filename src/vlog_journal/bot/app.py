import asyncio
import structlog
import telebot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telebot.async_telebot import AsyncTeleBot

from vlog_journal.bot.handlers import register_handlers
from vlog_journal.bot.middleware import WhitelistMiddleware
from vlog_journal.bot.review import build_review_message
from vlog_journal.bot.state import SessionManager
from vlog_journal.config import AppSettings
from vlog_journal.pipeline.registry import PipelineContext
from vlog_journal.pipeline.runner import run_pipeline

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

    # Register Handlers with session_manager and settings
    register_handlers(bot, session_manager, settings=settings)
    logger.info("Registered command and media handlers")

    return bot, session_manager

def setup_backup_scheduler(bot: AsyncTeleBot, settings: AppSettings) -> AsyncIOScheduler | None:
    """Initialize APScheduler cron job for automated daily vault backup."""
    b_cfg = getattr(settings, "backup", None)
    if not b_cfg or not getattr(b_cfg, "enabled", False):
        logger.info("Backup scheduler disabled in configuration")
        return None

    cron_expr = getattr(b_cfg, "schedule_cron", "0 4 * * *")
    scheduler = AsyncIOScheduler()

    async def _scheduled_backup_job() -> None:
        logger.info("Scheduled backup cron triggered", cron_expr=cron_expr)
        allowed_users = list(settings.allowed_user_ids)
        target_chat_id = allowed_users[0] if allowed_users else 0

        ctx = PipelineContext(chat_id=target_chat_id, config=settings)
        backup_steps = [
            "vault.create_encrypted_archive",
            "vault.upload_and_prune_remote",
        ]
        try:
            res_ctx = await run_pipeline(backup_steps, ctx)
            archive_name = res_ctx.payload.get("archive_name", "archive.7z")
            if target_chat_id:
                await bot.send_message(
                    target_chat_id,
                    f"⏰ **Automated Backup Complete!**\n\n📦 **Archive:** `{archive_name}`\n☁️ Uploaded to remote cloud storage.",
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.error("Scheduled backup cron failed", error=str(e))
            if target_chat_id:
                await bot.send_message(
                    target_chat_id,
                    f"❌ **Automated Scheduled Backup Failed!**\nError: `{e}`",
                    parse_mode="Markdown",
                )

    try:
        parts = cron_expr.strip().split()
        if len(parts) == 5:
            trigger = CronTrigger(
                minute=parts[0],
                hour=parts[1],
                day=parts[2],
                month=parts[3],
                day_of_week=parts[4],
            )
            scheduler.add_job(_scheduled_backup_job, trigger, id="vault_backup_cron")
            scheduler.start()
            logger.info("Started backup AsyncIOScheduler", cron=cron_expr)
            return scheduler
    except Exception as e:
        logger.error("Failed to parse backup cron expression", cron=cron_expr, error=str(e))

    return None

async def run_crash_recovery(bot: AsyncTeleBot, session_manager: SessionManager, timeout_hours: int = 12) -> None:
    """Check for pending reviews, stale collecting sessions, and interrupted processing sessions on startup."""
    # 1. Check pending reviews (draft_pending)
    pending_reviews = session_manager.get_pending_reviews()
    for chat_id, session in pending_reviews:
        logger.info("Crash recovery: Resending pending draft review", chat_id=chat_id)
        try:
            review_text, review_kb = build_review_message(session)
            await bot.send_message(
                chat_id,
                f"⚠️ **Bot restarted!** Re-sending your pending draft review:\n\n{review_text}",
                reply_markup=review_kb,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("Failed to resend crash recovery notification", chat_id=chat_id, error=str(e))

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
    """Main entrypoint to run the Telegram bot polling loop with crash recovery and scheduler."""
    bot, session_manager = create_bot(settings)

    # Execute Crash Recovery Check
    await run_crash_recovery(bot, session_manager, timeout_hours=settings.app.session_timeout_hours)

    # Initialize Backup Scheduler
    scheduler = setup_backup_scheduler(bot, settings)

    logger.info("Starting Telegram Bot infinity polling loop...")
    try:
        await bot.infinity_polling(timeout=10, request_timeout=60)
    except asyncio.CancelledError:
        logger.info("Bot polling loop cancelled")
    except Exception as e:
        logger.error("Unexpected error in bot polling loop", error=str(e))
        raise
    finally:
        if scheduler and scheduler.running:
            scheduler.shutdown()
            logger.info("Stopped backup AsyncIOScheduler")
