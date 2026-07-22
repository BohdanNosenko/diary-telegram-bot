import asyncio
import structlog
import telebot
from telebot.async_telebot import AsyncTeleBot

from vlog_journal.bot.handlers import register_handlers
from vlog_journal.bot.middleware import WhitelistMiddleware
from vlog_journal.config import AppSettings

logger = structlog.get_logger(__name__)

def create_bot(settings: AppSettings) -> AsyncTeleBot:
    """Factory function to initialize AsyncTeleBot with middleware and handlers."""
    token = settings.telegram_bot_token.get_secret_value()
    
    if settings.telegram_local_api_url:
        local_url = settings.telegram_local_api_url.rstrip("/")
        telebot.apihelper.API_URL = f"{local_url}/bot{{0}}/{{1}}"
        telebot.apihelper.FILE_URL = f"{local_url}/file/bot{{0}}/{{1}}"
        logger.info("Configured custom Telegram Bot API URL", local_api_url=local_url)

    bot = AsyncTeleBot(token)

    # Setup Whitelist Middleware
    whitelist_middleware = WhitelistMiddleware(settings.allowed_user_ids)
    bot.setup_middleware(whitelist_middleware)
    logger.info("Registered WhitelistMiddleware", allowed_count=len(whitelist_middleware.allowed_user_ids))

    # Register Handlers
    register_handlers(bot)
    logger.info("Registered command handlers")

    return bot

async def start_bot(settings: AppSettings) -> None:
    """Main entrypoint to run the Telegram bot polling loop."""
    bot = create_bot(settings)
    logger.info("Starting Telegram Bot infinity polling loop...")
    
    try:
        await bot.infinity_polling(timeout=10, request_timeout=60)
    except asyncio.CancelledError:
        logger.info("Bot polling loop cancelled")
    except Exception as e:
        logger.error("Unexpected error in bot polling loop", error=str(e))
        raise
