import os
import shutil
from datetime import datetime
from pathlib import Path

import structlog
from telebot.async_telebot import AsyncTeleBot
from telebot.types import CallbackQuery, Message

from vlog_journal.bot.review import (
    build_edit_keyboard,
    build_review_keyboard,
    build_review_message,
    parse_date_input,
    parse_speaker_map_input,
)
from vlog_journal.bot.state import SessionManager
from vlog_journal.config import AppSettings
from vlog_journal.pipeline.registry import PipelineContext, get_step
from vlog_journal.pipeline.runner import run_pipeline

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

STUB_RESPONSE = "🚧 **Feature coming soon!** Command active."

DRAFT_PIPELINE_STEPS = [
    "media.prepare_and_stitch",
    "media.extract_audio",
    "transcription.whisper_transcribe",
    "transcription.diarize_speakers",
    "transcription.merge_segments",
    "llm.structure_transcript",
    "enrichment.extract_gps",
    "enrichment.reverse_geocode",
    "enrichment.fetch_weather",
    "enrichment.compute_media_stats",
    "vault.render_markdown",
]

APPROVE_PIPELINE_STEPS = [
    "vault.save_entry",
    "vault.update_tag_cache",
    "media.cleanup_temp_files",
]

def _get_temp_dir(chat_id: int) -> Path:
    temp_dir = Path("data/temp") / str(chat_id)
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir

def _cleanup_temp_dir(chat_id: int) -> None:
    temp_dir = Path("data/temp") / str(chat_id)
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)

def register_handlers(
    bot: AsyncTeleBot,
    session_manager: SessionManager,
    settings: AppSettings | None = None,
) -> None:
    """Register all command, callback, and media handlers on the AsyncTeleBot instance."""

    async def _notify_user(chat_id: int, text: str) -> None:
        try:
            await bot.send_message(chat_id, text, parse_mode="Markdown")
        except Exception as e:
            logger.warning("Failed to send notify message to user", chat_id=chat_id, error=str(e))

    async def _reprocess_draft(chat_id: int, session: dict) -> None:
        """Re-run LLM structuring and/or Markdown rendering after edits."""
        if not settings:
            logger.warning("No settings available for re-processing draft", chat_id=chat_id)
            return

        ctx = PipelineContext(chat_id=chat_id, config=settings)
        ctx.payload = dict(session)

        # Re-run LLM structure & render markdown
        try:
            step_llm = get_step("llm.structure_transcript")
            ctx = await step_llm(ctx)
            step_render = get_step("vault.render_markdown")
            ctx = await step_render(ctx)

            session_manager.update_payload(
                chat_id,
                draft_markdown=ctx.payload.get("draft_markdown"),
                note_schema=ctx.payload.get("note_schema"),
                speaker_map=ctx.payload.get("speaker_map", {}),
                awaiting_edit=None,
            )
            updated_session = session_manager.get_session(chat_id)
            if updated_session:
                review_text, review_kb = build_review_message(updated_session)
                await bot.send_message(chat_id, review_text, reply_markup=review_kb, parse_mode="Markdown")
        except Exception as e:
            logger.error("Failed to re-process draft after edit", chat_id=chat_id, error=str(e))
            await bot.send_message(chat_id, f"❌ Failed to re-process draft: {e}")

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
            f"🚀 **Processing started for {clip_count} clip(s)!**",
            parse_mode="Markdown",
        )

        if not settings:
            logger.error("AppSettings missing in handlers for pipeline execution", chat_id=chat_id)
            await bot.send_message(chat_id, "❌ System error: Configuration settings missing.")
            return

        ctx = PipelineContext(chat_id=chat_id, config=settings)
        ctx.payload = {
            "clips": session["clips"],
            "entry_date": session.get("entry_date"),
            "speaker_map": session.get("speaker_map", {}),
        }

        async def custom_notify(msg: str) -> None:
            await _notify_user(chat_id, f"⚙️ {msg}")

        ctx.notify = custom_notify

        try:
            res_ctx = await run_pipeline(DRAFT_PIPELINE_STEPS, ctx)
            
            # Store results in session
            session_manager.update_payload(
                chat_id,
                draft_markdown=res_ctx.payload.get("draft_markdown"),
                note_schema=res_ctx.payload.get("note_schema"),
                media_stats=res_ctx.payload.get("media_stats"),
                labeled_segments=res_ctx.payload.get("labeled_segments"),
                locations_visited=res_ctx.payload.get("locations_visited"),
                primary_location=res_ctx.payload.get("primary_location"),
                primary_weather=res_ctx.payload.get("primary_weather"),
                raw_video_path=res_ctx.payload.get("raw_video_path"),
                raw_audio_path=res_ctx.payload.get("raw_audio_path"),
                is_voice_memo=res_ctx.payload.get("is_voice_memo", False),
                entry_date=res_ctx.payload.get("entry_date"),
                pipeline_progress=len(DRAFT_PIPELINE_STEPS),
            )
            session_manager.set_status(chat_id, "draft_pending")

            updated_session = session_manager.get_session(chat_id)
            if updated_session:
                review_text, review_kb = build_review_message(updated_session)
                await bot.send_message(
                    chat_id,
                    review_text,
                    reply_markup=review_kb,
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.error("Processing pipeline failed", chat_id=chat_id, error=str(e))
            session_manager.update_payload(chat_id, error=str(e))
            await bot.send_message(
                chat_id,
                f"❌ **Processing failed!**\nError: `{e}`\n\nRun `/retry` to attempt resuming from step.",
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

    @bot.callback_query_handler(func=lambda call: True)
    async def handle_callbacks(call: CallbackQuery) -> None:
        chat_id = call.message.chat.id
        data = call.data
        session = session_manager.get_session(chat_id)

        if not session or session.get("status") != "draft_pending":
            await bot.answer_callback_query(call.id, "⚠️ No pending review session found.")
            return

        await bot.answer_callback_query(call.id)

        if data == "review_approve":
            if not settings:
                await bot.send_message(chat_id, "❌ System error: Settings missing.")
                return

            await bot.send_message(chat_id, "💾 **Saving entry to Obsidian vault...**")
            ctx = PipelineContext(chat_id=chat_id, config=settings)
            ctx.payload = dict(session)

            try:
                res_ctx = await run_pipeline(APPROVE_PIPELINE_STEPS, ctx)
                final_md_path = res_ctx.payload.get("final_markdown_path", "Vault")
                
                session_manager.set_status(chat_id, "approved")
                session_manager.pop_session(chat_id)
                _cleanup_temp_dir(chat_id)

                await bot.send_message(
                    chat_id,
                    f"🎉 **Entry Approved & Saved!**\n\n📄 **Saved to:** `{final_md_path}`\n🏷️ **Tags updated.** Temp files cleaned up.",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error("Approval pipeline failed", chat_id=chat_id, error=str(e))
                await bot.send_message(chat_id, f"❌ Failed to save entry to vault: `{e}`", parse_mode="Markdown")

        elif data == "review_discard":
            session_manager.pop_session(chat_id)
            _cleanup_temp_dir(chat_id)
            await bot.send_message(chat_id, "🗑️ **Draft discarded and session cancelled.**")

        elif data == "review_edit_menu":
            edit_kb = build_edit_keyboard()
            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=edit_kb,
            )

        elif data == "review_back":
            main_kb = build_review_keyboard()
            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=main_kb,
            )

        elif data == "edit_speakers":
            session_manager.update_payload(chat_id, awaiting_edit="speakers")
            await bot.send_message(
                chat_id,
                "🗣️ **Label Speakers:**\nReply with speaker names, e.g.:\n`Speaker 1 = Me, Speaker 2 = Mom`",
                parse_mode="Markdown",
            )

        elif data == "edit_date":
            session_manager.update_payload(chat_id, awaiting_edit="date")
            await bot.send_message(
                chat_id,
                "📅 **Change Date:**\nReply with new date in `YYYY-MM-DD` format (or `yesterday`, `today`):",
                parse_mode="Markdown",
            )

        elif data == "edit_prompt":
            session_manager.update_payload(chat_id, awaiting_edit="prompt")
            await bot.send_message(
                chat_id,
                "✍️ **Free-text Correction:**\nReply with instructions for the LLM (e.g. `Fix spelling of Kyiv and add mention of dinner`):",
                parse_mode="Markdown",
            )

    @bot.message_handler(func=lambda msg: True, content_types=["text"])
    async def handle_text_edits(message: Message) -> None:
        chat_id = message.chat.id
        session = session_manager.get_session(chat_id)

        if not session or session.get("status") != "draft_pending":
            return

        text = message.text.strip() if message.text else ""
        awaiting_edit = session.get("awaiting_edit")

        # 1. Speaker map input
        if awaiting_edit == "speakers" or text.lower().startswith("speaker"):
            speaker_map = parse_speaker_map_input(text)
            if speaker_map:
                current_map = session.get("speaker_map") or {}
                current_map.update(speaker_map)
                session_manager.update_payload(chat_id, speaker_map=current_map)
                await bot.reply_to(
                    message,
                    f"✅ Updated speakers: `{current_map}`. Re-processing draft...",
                    parse_mode="Markdown",
                )
                updated_session = session_manager.get_session(chat_id)
                if updated_session:
                    await _reprocess_draft(chat_id, updated_session)
                return

        # 2. Date override input
        if awaiting_edit == "date" or parse_date_input(text):
            parsed_date = parse_date_input(text)
            if parsed_date:
                session_manager.update_payload(chat_id, entry_date=parsed_date)
                await bot.reply_to(message, f"📅 Entry date updated to `{parsed_date}`. Re-rendering...", parse_mode="Markdown")
                updated_session = session_manager.get_session(chat_id)
                if updated_session and settings:
                    ctx = PipelineContext(chat_id=chat_id, config=settings)
                    ctx.payload = dict(updated_session)
                    step_render = get_step("vault.render_markdown")
                    ctx = await step_render(ctx)
                    session_manager.update_payload(
                        chat_id,
                        draft_markdown=ctx.payload.get("draft_markdown"),
                        awaiting_edit=None,
                    )
                    final_session = session_manager.get_session(chat_id)
                    if final_session:
                        review_text, review_kb = build_review_message(final_session)
                        await bot.send_message(chat_id, review_text, reply_markup=review_kb, parse_mode="Markdown")
                return

        # 3. Free-text prompt correction
        if awaiting_edit == "prompt":
            session_manager.update_payload(chat_id, correction_prompt=text)
            await bot.reply_to(message, f"✍️ Re-processing draft with instructions: _{text}_...", parse_mode="Markdown")
            updated_session = session_manager.get_session(chat_id)
            if updated_session:
                await _reprocess_draft(chat_id, updated_session)
            return

    @bot.message_handler(commands=["backup", "sync_tags", "retry"])
    async def handle_stub_commands(message: Message) -> None:
        cmd = message.text.split()[0] if message.text else "command"
        logger.info("Stub command received", command=cmd, chat_id=message.chat.id)
        await bot.reply_to(message, f"🚧 Command `{cmd}` active.", parse_mode="Markdown")
