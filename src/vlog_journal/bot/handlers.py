import structlog
from telebot.async_telebot import AsyncTeleBot
from telebot.types import Message

logger = structlog.get_logger(__name__)

WELCOME_TEXT = """🎥 **Welcome to Vlog Journal Bot!**

I am your personal vlog and voice diary assistant. Send me your video clips or voice notes, and I will process them into structured, searchable Obsidian markdown notes.

**Quick Start:**
1. Start a session: `/start_session` (or `/start_session 2026-07-22`)
2. Send video clips or voice notes with optional captions.
3. Finish and process: `/finish_session`

Type `/help` to see all available commands.
"""

HELP_TEXT = """📋 **Available Commands:**

• `/start` - Show welcome message and feature overview
• `/help` - Show this list of commands
• `/start_session [YYYY-MM-DD]` - Start a new vlog recording session
• `/finish_session` - Finish recording and trigger full processing pipeline
• `/cancel` - Cancel current session and discard unprocessed clips
• `/status` - View active session status and clip count
• `/retry` - Retry a failed session from the last successful step
• `/backup` - Trigger manual vault backup to remote storage
• `/sync_tags` - Synchronize tag cache from Obsidian vault
"""

STUB_RESPONSE = "🚧 **Feature coming soon!** Session management and pipelines will be activated in the next step."

def register_handlers(bot: AsyncTeleBot) -> None:
    """Register all command handlers on the AsyncTeleBot instance."""

    @bot.message_handler(commands=["start"])
    async def handle_start(message: Message) -> None:
        logger.info("Command /start received", chat_id=message.chat.id, user_id=message.from_user.id if message.from_user else None)
        await bot.reply_to(message, WELCOME_TEXT, parse_mode="Markdown")

    @bot.message_handler(commands=["help"])
    async def handle_help(message: Message) -> None:
        logger.info("Command /help received", chat_id=message.chat.id)
        await bot.reply_to(message, HELP_TEXT, parse_mode="Markdown")

    @bot.message_handler(commands=["start_session", "finish_session", "cancel", "status", "retry", "backup", "sync_tags"])
    async def handle_stub_commands(message: Message) -> None:
        cmd = message.text.split()[0] if message.text else "command"
        logger.info("Stub command received", command=cmd, chat_id=message.chat.id)
        await bot.reply_to(message, f"{STUB_RESPONSE}\n\nReceived: `{cmd}`", parse_mode="Markdown")
