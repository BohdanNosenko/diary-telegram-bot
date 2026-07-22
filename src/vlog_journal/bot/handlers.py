import os
import shutil
from datetime import datetime
from pathlib import Path
import structlog
from telebot.async_telebot import AsyncTeleBot
from telebot.types import Message

from vlog_journal.bot.state import SessionManager

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

STUB_RESPONSE = "🚧 **Feature coming soon!** Pipeline execution and backup steps will be activated in Phase 3 & 5."

def _get_temp_dir(chat_id: int) -> Path:
    temp_dir = Path("data/temp") / str(chat_id)
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir

def _cleanup_temp_dir(chat_id: int) -> None:
    temp_dir = Path("data/temp") / str(chat_id)
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)

def register_handlers(bot: AsyncTeleBot, session_manager: SessionManager) -> None:
    """Register all command and media handlers on the AsyncTeleBot instance."""

    @bot.message_handler(commands=["start"])
    async def handle_start(message: Message) -> None:
        logger.info("Command /start received", chat_id=message.chat.id)
        await bot.reply_to(message, WELCOME_TEXT, parse_mode="Markdown")

    @bot.message_handler(commands=["help"])
    async def handle_help(message: Message) -> None:
        logger.info("Command /help received", chat_id=message.chat.id)
        await bot.reply_to(message, HELP_TEXT, parse_mode="Markdown")

    @bot.message_handler(commands=["start_session"])
    async def handle_start_session(message: Message) -> None:
        chat_id = message.chat.id
        text_parts = message.text.split() if message.text else []
        date_override = None

        if len(text_parts) > 1:
            raw_date = text_parts[1].strip()
            try:
                datetime.strptime(raw_date, "%Y-%m-%d")
                date_override = raw_date
            except ValueError:
                await bot.reply_to(
                    message,
                    "⚠️ Invalid date format. Please use `YYYY-MM-DD` (e.g. `/start_session 2026-07-22`).",
                    parse_mode="Markdown",
                )
                return

        session = session_manager.start_session(chat_id, date_override=date_override)
        date_str = session["entry_date"] or "today"
        await bot.reply_to(
            message,
            f"🎬 **Session started for {date_str}!**\n\nSend me your video clips or voice notes with optional captions. When finished, run `/finish_session`.",
            parse_mode="Markdown",
        )

    @bot.message_handler(content_types=["video", "video_note"])
    async def handle_video(message: Message) -> None:
        chat_id = message.chat.id
        video_obj = message.video or message.video_note
        if not video_obj:
            return

        file_id = video_obj.file_id
        temp_dir = _get_temp_dir(chat_id)
        ext = ".mp4"
        file_name = f"clip_{int(datetime.now().timestamp())}_{file_id[:8]}{ext}"
        local_path = temp_dir / file_name

        try:
            file_info = await bot.get_file(file_id)
            downloaded_file = await bot.download_file(file_info.file_path)
            with open(local_path, "wb") as f:
                f.write(downloaded_file)

            session = session_manager.add_clip(
                chat_id=chat_id,
                clip_path=str(local_path),
                media_type="video",
                caption=message.caption,
            )
            clip_count = len(session["clips"])
            await bot.reply_to(
                message,
                f"📹 **Video clip received** (#{clip_count}).\nKeep sending clips or run `/finish_session` to process.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error("Failed to download video clip", chat_id=chat_id, error=str(e))
            await bot.reply_to(message, "❌ Failed to download video clip. Please try sending it again.")

    @bot.message_handler(content_types=["voice", "audio"])
    async def handle_audio(message: Message) -> None:
        chat_id = message.chat.id
        audio_obj = message.voice or message.audio
        if not audio_obj:
            return

        file_id = audio_obj.file_id
        temp_dir = _get_temp_dir(chat_id)
        ext = ".ogg" if message.voice else os.path.splitext(audio_obj.file_name or "audio.mp3")[1]
        file_name = f"voice_{int(datetime.now().timestamp())}_{file_id[:8]}{ext}"
        local_path = temp_dir / file_name

        try:
            file_info = await bot.get_file(file_id)
            downloaded_file = await bot.download_file(file_info.file_path)
            with open(local_path, "wb") as f:
                f.write(downloaded_file)

            session = session_manager.add_clip(
                chat_id=chat_id,
                clip_path=str(local_path),
                media_type="voice",
                caption=message.caption,
            )
            clip_count = len(session["clips"])
            await bot.reply_to(
                message,
                f"🎙️ **Voice note received** (#{clip_count}).\nKeep sending notes/clips or run `/finish_session` to process.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error("Failed to download voice note", chat_id=chat_id, error=str(e))
            await bot.reply_to(message, "❌ Failed to download voice note. Please try sending it again.")

    @bot.message_handler(commands=["status"])
    async def handle_status(message: Message) -> None:
        chat_id = message.chat.id
        session = session_manager.get_session(chat_id)

        if not session:
            await bot.reply_to(
                message,
                "ℹ️ No active session found. Start one with `/start_session` or just send a video/voice message.",
                parse_mode="Markdown",
            )
            return

        status = session["status"]
        clip_count = len(session["clips"])
        entry_date = session["entry_date"] or "Auto (min timestamp)"
        created_at = session["created_at"][:19].replace("T", " ")

        summary = (
            f"📊 **Current Session Status:** `{status}`\n"
            f"• **Entry Date:** {entry_date}\n"
            f"• **Clips Collected:** {clip_count}\n"
            f"• **Started At:** {created_at} UTC\n"
            f"• **Pipeline Progress:** {session['pipeline_progress']} step(s)\n"
        )
        if session.get("error"):
            summary += f"\n⚠️ **Last Error:** `{session['error']}`"

        await bot.reply_to(message, summary, parse_mode="Markdown")

    @bot.message_handler(commands=["finish_session"])
    async def handle_finish_session(message: Message) -> None:
        chat_id = message.chat.id
        session = session_manager.get_session(chat_id)

        if not session or not session["clips"]:
            await bot.reply_to(
                message,
                "⚠️ You have no active session or clips to process. Send video/voice files first!",
                parse_mode="Markdown",
            )
            return

        session_manager.set_status(chat_id, "processing")
        clip_count = len(session["clips"])
        await bot.reply_to(
            message,
            f"🚀 **Processing started for {clip_count} clip(s)!**\nStatus updated to `processing`.",
            parse_mode="Markdown",
        )

    @bot.message_handler(commands=["cancel"])
    async def handle_cancel(message: Message) -> None:
        chat_id = message.chat.id
        if not session_manager.is_active(chat_id):
            await bot.reply_to(message, "ℹ️ No active session to cancel.")
            return

        session_manager.pop_session(chat_id)
        _cleanup_temp_dir(chat_id)
        await bot.reply_to(message, "❌ **Session cancelled.** All temporary clips deleted.", parse_mode="Markdown")

    @bot.message_handler(commands=["backup", "sync_tags", "retry"])
    async def handle_stub_commands(message: Message) -> None:
        cmd = message.text.split()[0] if message.text else "command"
        logger.info("Stub command received", command=cmd, chat_id=message.chat.id)
        await bot.reply_to(message, f"{STUB_RESPONSE}\n\nReceived: `{cmd}`", parse_mode="Markdown")
