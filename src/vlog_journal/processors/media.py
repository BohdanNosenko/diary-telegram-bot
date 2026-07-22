import asyncio
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from vlog_journal.pipeline.registry import PipelineContext, register_step

logger = structlog.get_logger(__name__)

RESOLUTION_MAP: dict[str, str | None] = {
    "original": None,
    "4k": "3840:2160",
    "1080p": "1920:1080",
    "720p": "1280:720",
    "480p": "854:480",
    "360p": "640:360",
    "240p": "426:240",
}

def _get_ffmpeg_bin() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg executable not found on PATH")
    return ffmpeg

def _get_ffprobe_bin() -> str:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe executable not found on PATH")
    return ffprobe

async def _run_command(cmd: list[str]) -> tuple[str, str]:
    logger.debug("Executing command", cmd=" ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    out_str = stdout.decode("utf-8", errors="replace")
    err_str = stderr.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        logger.error("Command failed", cmd=" ".join(cmd), returncode=proc.returncode, stderr=err_str)
        raise RuntimeError(f"Command execution failed ({proc.returncode}):\n{err_str}")

    return out_str, err_str

async def get_item_creation_date(path: str | Path) -> datetime:
    """Extract embedded creation_time timestamp using ffprobe, fallback to file mtime."""
    ffprobe = _get_ffprobe_bin()
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format_tags=creation_time:stream_tags=creation_time",
        "-of",
        "json",
        str(path),
    ]

    try:
        stdout, _ = await _run_command(cmd)
        data = json.loads(stdout)

        # Check format tags
        creation_time_str = data.get("format", {}).get("tags", {}).get("creation_time")
        if not creation_time_str:
            # Check stream tags
            for stream in data.get("streams", []):
                creation_time_str = stream.get("tags", {}).get("creation_time")
                if creation_time_str:
                    break

        if creation_time_str:
            # Parse ISO 8601 string
            creation_time_str = creation_time_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(creation_time_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except Exception as e:
        logger.warning("ffprobe timestamp extraction failed, falling back to mtime", path=str(path), error=str(e))

    # Fallback to file mtime
    mtime = os.path.getmtime(path)
    return datetime.fromtimestamp(mtime, tz=timezone.utc)

@register_step("media.prepare_and_stitch")
async def prepare_and_stitch(ctx: PipelineContext) -> PipelineContext:
    """Stitch clips, normalize resolution/FPS, and anchor session entry_date."""
    clips: list[dict[str, Any]] = ctx.payload.get("clips", [])
    if not clips:
        raise ValueError("No media clips provided in pipeline context")

    ffmpeg = _get_ffmpeg_bin()
    temp_dir = Path("data/temp") / str(ctx.chat_id)
    temp_dir.mkdir(parents=True, exist_ok=True)

    # 1. Calculate entry_date from min(creation_times) if not already explicitly set
    if not ctx.payload.get("entry_date"):
        creation_times = []
        for clip in clips:
            dt = await get_item_creation_date(clip["path"])
            creation_times.append(dt)
        min_dt = min(creation_times)
        ctx.payload["entry_date"] = min_dt.strftime("%Y-%m-%d")
        logger.info("Resolved entry_date from min creation time", entry_date=ctx.payload["entry_date"])

    # 2. Discriminate media types (voice-only vs contains video)
    has_video = any(c.get("type") == "video" for c in clips)
    ctx.payload["is_voice_memo"] = not has_video

    # Read media settings from ctx.config if present
    media_cfg = getattr(ctx.config, "media", None) if ctx.config else None
    target_res = getattr(media_cfg, "target_resolution", "720p") if media_cfg else "720p"
    target_fps = getattr(media_cfg, "target_fps", 30) if media_cfg else 30
    v_codec = getattr(media_cfg, "video_codec", "libx264") if media_cfg else "libx264"
    a_codec = getattr(media_cfg, "audio_codec", "aac") if media_cfg else "aac"
    a_bitrate = getattr(media_cfg, "audio_bitrate", "128k") if media_cfg else "128k"
    crf = getattr(media_cfg, "crf", 32) if media_cfg else 32

    if not has_video:
        # Voice-only path: concatenate audio clips to raw_audio.mp3
        output_audio = temp_dir / "raw_audio.mp3"
        if len(clips) == 1:
            cmd = [ffmpeg, "-y", "-i", clips[0]["path"], "-c:a", "libmp3lame", "-b:a", a_bitrate, str(output_audio)]
        else:
            inputs = []
            for c in clips:
                inputs.extend(["-i", c["path"]])
            n = len(clips)
            filter_complex = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[outa]"
            cmd = [ffmpeg, "-y", *inputs, "-filter_complex", filter_complex, "-map", "[outa]", "-c:a", "libmp3lame", "-b:a", a_bitrate, str(output_audio)]

        await _run_command(cmd)
        ctx.payload["raw_audio_path"] = str(output_audio)

        # Also prepare WAV for Whisper directly
        output_wav = temp_dir / "audio.wav"
        cmd_wav = [ffmpeg, "-y", "-i", str(output_audio), "-ar", "16000", "-ac", "1", str(output_wav)]
        await _run_command(cmd_wav)
        ctx.payload["audio_wav_path"] = str(output_wav)
        logger.info("Voice memos concatenated", raw_audio_path=str(output_audio), audio_wav_path=str(output_wav))
    else:
        # Video path: normalize and stitch clips to raw_video.mp4
        output_video = temp_dir / "raw_video.mp4"
        res_scale = RESOLUTION_MAP.get(target_res)

        if len(clips) == 1 and target_res == "original":
            # Pass-through single clip
            cmd = [ffmpeg, "-y", "-i", clips[0]["path"], "-c", "copy", str(output_video)]
            await _run_command(cmd)
        else:
            # Build filter_complex with scaling/padding and FPS normalization
            inputs = []
            filter_parts = []
            n = len(clips)

            width, height = (1280, 720)
            if res_scale:
                w_str, h_str = res_scale.split(":")
                width, height = int(w_str), int(h_str)

            for i, c in enumerate(clips):
                inputs.extend(["-i", c["path"]])
                filter_parts.append(
                    f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={target_fps},setsar=1[v{i}];"
                )

            concat_str = "".join(f"[v{i}][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[outv][outa]"
            filter_complex = "".join(filter_parts) + concat_str

            cmd = [
                ffmpeg,
                "-y",
                *inputs,
                "-filter_complex",
                filter_complex,
                "-map",
                "[outv]",
                "-map",
                "[outa]",
                "-c:v",
                v_codec,
                "-crf",
                str(crf),
                "-c:a",
                a_codec,
                "-b:a",
                a_bitrate,
                str(output_video),
            ]
            await _run_command(cmd)

        ctx.payload["raw_video_path"] = str(output_video)
        logger.info("Video clips stitched", raw_video_path=str(output_video))

    return ctx

@register_step("media.extract_audio")
async def extract_audio(ctx: PipelineContext) -> PipelineContext:
    """Extract 16 kHz mono WAV from stitched video file for Whisper transcription."""
    if ctx.payload.get("is_voice_memo"):
        if "audio_wav_path" in ctx.payload and os.path.exists(ctx.payload["audio_wav_path"]):
            logger.info("Voice memo audio WAV already extracted", path=ctx.payload["audio_wav_path"])
            return ctx

    raw_video_path = ctx.payload.get("raw_video_path")
    if not raw_video_path or not os.path.exists(raw_video_path):
        raise ValueError("raw_video_path missing or file does not exist")

    ffmpeg = _get_ffmpeg_bin()
    temp_dir = Path("data/temp") / str(ctx.chat_id)
    temp_dir.mkdir(parents=True, exist_ok=True)
    audio_wav_path = temp_dir / "audio.wav"

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        raw_video_path,
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(audio_wav_path),
    ]
    await _run_command(cmd)
    ctx.payload["audio_wav_path"] = str(audio_wav_path)
    logger.info("Audio extracted for Whisper", audio_wav_path=str(audio_wav_path))
    return ctx

@register_step("media.cleanup_temp_files")
async def cleanup_temp_files(ctx: PipelineContext) -> PipelineContext:
    """Clean up intermediate files in temp directory upon pipeline completion."""
    temp_dir = Path("data/temp") / str(ctx.chat_id)
    if temp_dir.exists():
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info("Cleaned up temp files", chat_id=ctx.chat_id)
        except Exception as e:
            logger.warning("Failed to cleanup temp files", chat_id=ctx.chat_id, error=str(e))
    return ctx
