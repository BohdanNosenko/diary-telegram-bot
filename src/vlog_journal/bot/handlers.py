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
        logger.info("Command /status received", chat_id=chat_id)
        session = session_manager.get_session(chat_id)

        # 1. Ollama Health Check
        ollama_status = "🔴 Offline"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get("http://localhost:11434/api/tags")
                if resp.status_code == 200:
                    models = [m.get("name") for m in resp.json().get("models", [])]
                    model_str = ", ".join(models[:2]) if models else "Online"
                    ollama_status = f"🟢 Online (`{model_str}`)"
        except Exception:
            ollama_status = "🔴 Offline / Unreachable"

        # 2. CUDA GPU VRAM Check
        cuda_status = "💻 CPU Mode (CUDA unavailable)"
        try:
            import torch
            if torch.cuda.is_available():
                dev_name = torch.cuda.get_device_name(0)
                alloc_mb = round(torch.cuda.memory_allocated(0) / (1024 * 1024), 1)
                total_mb = round(torch.cuda.get_device_properties(0).total_memory / (1024 * 1024), 1)
                cuda_status = f"⚡ **GPU:** `{dev_name}`\n• **VRAM:** `{alloc_mb} MB` / `{total_mb} MB`"
        except Exception as e:
            cuda_status = f"⚠️ VRAM query error: `{e}`"

        # 3. Disk Space Check
        disk_status = "Unknown"
        vault_path = settings.app.vault_path if settings else "/tmp"
        try:
            usage = shutil.disk_usage(vault_path)
            free_gb = round(usage.free / (1024**3), 2)
            total_gb = round(usage.total / (1024**3), 2)
            disk_status = f"`{free_gb} GB` free of `{total_gb} GB`"
        except Exception:
            pass

        # 4. Rclone Check
        rclone_status = "🔴 Not Installed"
        if shutil.which("rclone"):
            rclone_status = "🟢 Installed & Ready"

        # 5. Active Session Summary
        session_text = "No active session"
        if session:
            session_text = (
                f"`{session['status']}` ({len(session['clips'])} clip(s), "
                f"step {session.get('pipeline_progress', 0)})"
            )
            if session.get("error"):
                session_text += f"\n⚠️ **Error:** `{session['error']}`"

        report = (
            f"📊 **System Status Report:**\n\n"
            f"🤖 **Ollama:** {ollama_status}\n"
            f"{cuda_status}\n"
            f"💾 **Vault Disk Space:** {disk_status}\n"
            f"☁️ **Rclone Status:** {rclone_status}\n\n"
            f"🎬 **Active Session:** {session_text}"
        )

        await bot.reply_to(message, report, parse_mode="Markdown")

    @bot.message_handler(commands=["retry"])
    async def handle_retry(message: Message) -> None:
        chat_id = message.chat.id
        logger.info("Command /retry received", chat_id=chat_id)
        session = session_manager.get_session(chat_id)

        if not session:
            await bot.reply_to(message, "ℹ️ No active session to retry.")
            return

        if not settings:
            await bot.reply_to(message, "❌ System error: Settings missing.")
            return

        full_retry = "full" in (message.text or "").lower()
        start_index = 0 if full_retry else session.get("pipeline_progress", 0)

        session_manager.set_status(chat_id, "processing")
        session_manager.update_payload(chat_id, error=None)

        mode_str = "from scratch" if full_retry else f"from step {start_index}"
        await bot.reply_to(
            message,
            f"🔄 **Retrying pipeline {mode_str}...**",
            parse_mode="Markdown",
        )

        ctx = PipelineContext(chat_id=chat_id, config=settings)
        ctx.payload = dict(session)

        async def custom_notify(msg: str) -> None:
            await _notify_user(chat_id, f"⚙️ {msg}")

        ctx.notify = custom_notify

        try:
            from vlog_journal.pipeline.runner import run_pipeline_from
            res_ctx = await run_pipeline_from(DRAFT_PIPELINE_STEPS, ctx, start_index=start_index)

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
                error=None,
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
            logger.error("Retry pipeline execution failed", chat_id=chat_id, error=str(e))
            session_manager.update_payload(chat_id, error=str(e))
            await bot.send_message(
                chat_id,
                f"❌ **Pipeline Retry Failed!**\n• **Error:** `{e}`\n\n💡 *Use `/retry` to try again or `/retry full` to restart.*",
                parse_mode="Markdown",
            )

    @bot.message_handler(commands=["sync_tags"])
    async def handle_sync_tags(message: Message) -> None:
        chat_id = message.chat.id
        logger.info("Command /sync_tags received", chat_id=chat_id)

        if not settings:
            await bot.reply_to(message, "❌ System error: Settings missing.")
            return

        await bot.reply_to(message, "🏷️ **Scanning Obsidian vault to sync tags...**", parse_mode="Markdown")

        try:
            from vlog_journal.vault.tags import TagManager
            tag_mgr = TagManager(settings.app.tags_cache_file)
            tags = tag_mgr.sync_from_vault(settings.app.vault_path)
            await bot.reply_to(
                message,
                f"✅ **Tag Cache Synchronized!**\n\n🏷️ **Total unique tags in cache:** `{len(tags)}`",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error("Failed to sync tags from vault", chat_id=chat_id, error=str(e))
            await bot.reply_to(message, f"❌ Failed to sync tags: `{e}`", parse_mode="Markdown")

    @bot.message_handler(commands=["backup"])
    async def handle_backup(message: Message) -> None:
        chat_id = message.chat.id
        logger.info("Command /backup received", chat_id=chat_id)

        if not settings:
            await bot.reply_to(message, "❌ System error: Settings missing.")
            return

        await bot.reply_to(message, "📦 **Starting encrypted vault backup...**", parse_mode="Markdown")

        backup_steps = [
            "vault.create_encrypted_archive",
            "vault.upload_and_prune_remote",
        ]

        ctx = PipelineContext(chat_id=chat_id, config=settings)

        async def custom_notify(msg: str) -> None:
            try:
                await bot.send_message(chat_id, f"⚙️ {msg}", parse_mode="Markdown")
            except Exception:
                pass

        ctx.notify = custom_notify

        try:
            res_ctx = await run_pipeline(backup_steps, ctx)
            archive_name = res_ctx.payload.get("archive_name", "archive.7z")
            tag = res_ctx.payload.get("backup_tag", "daily")
            await bot.send_message(
                chat_id,
                f"✅ **Backup Complete!**\n\n📦 **Archive:** `{archive_name}` ({tag})\n☁️ Uploaded to remote cloud storage and pruned old backups.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error("Manual backup failed", chat_id=chat_id, error=str(e))
            await bot.send_message(
                chat_id,
                f"❌ **Backup Failed!**\nError: `{e}`",
                parse_mode="Markdown",
            )


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
