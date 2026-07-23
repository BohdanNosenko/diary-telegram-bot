import asyncio
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import structlog

from vlog_journal.pipeline.registry import PipelineContext, register_step

logger = structlog.get_logger(__name__)

def format_duration(seconds: float) -> str:
    """Format total seconds into HH:MM:SS or MM:SS."""
    total_sec = int(round(seconds))
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"

async def _get_media_metadata(file_path: str) -> dict[str, str]:
    """Read resolution and recording_device metadata via ffprobe."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not file_path or not Path(file_path).exists():
        return {}

    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_entries",
        "stream=width,height,codec_type:format_tags=encoder,make,model",
        file_path,
    ]

    res = {}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        data = json.loads(stdout.decode("utf-8", errors="replace"))

        # Streams
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                w = s.get("width")
                h = s.get("height")
                if w and h:
                    res["resolution"] = f"{w}x{h}"
                break

        # Device
        tags = data.get("format", {}).get("tags", {})
        make = tags.get("make", "")
        model = tags.get("model", "")
        if make or model:
            res["recording_device"] = f"{make} {model}".strip()
        elif tags.get("encoder"):
            res["recording_device"] = tags.get("encoder")
    except Exception as e:
        logger.warning("ffprobe stats metadata failed", path=file_path, error=str(e))

    return res

@register_step("enrichment.compute_media_stats")
async def compute_media_stats(ctx: PipelineContext) -> PipelineContext:
    """Compute auto-extracted media statistics for final frontmatter."""
    clips = ctx.payload.get("clips", [])
    labeled_segments = ctx.payload.get("labeled_segments", [])
    raw_video_path = ctx.payload.get("raw_video_path")
    raw_audio_path = ctx.payload.get("raw_audio_path")
    is_voice_memo = ctx.payload.get("is_voice_memo", False)

    media_file = (
        raw_video_path
        if (raw_video_path and Path(raw_video_path).exists())
        else raw_audio_path
    )

    # Duration & Word count
    duration_sec = 0.0
    if labeled_segments:
        last_seg = labeled_segments[-1]
        duration_sec = float(last_seg.get("end", 0.0))

    word_count = sum(len(seg.get("text", "").split()) for seg in labeled_segments)
    duration_min = duration_sec / 60.0 if duration_sec > 0 else 1.0
    wpm = round(word_count / duration_min) if duration_sec > 0 else 0

    # Speakers
    unique_speakers = set(seg.get("speaker", "Speaker 1") for seg in labeled_segments)
    speaker_count = len(unique_speakers) if unique_speakers else 1

    # File size
    file_size_mb = 0.0
    if media_file and Path(media_file).exists():
        file_size_mb = round(os.path.getsize(media_file) / (1024 * 1024), 2)

    # Resolution & device
    first_clip_path = clips[0]["path"] if (clips and "path" in clips[0]) else media_file
    meta = await _get_media_metadata(first_clip_path) if first_clip_path else {}

    resolution = meta.get("resolution", "N/A" if is_voice_memo else "1920x1080")
    device = meta.get("recording_device", "Unknown Device")

    # Languages
    det_lang = ctx.payload.get("detected_language", "en")
    lang_dist = {det_lang: 100}

    # Store all stats in context payload
    stats = {
        "duration": format_duration(duration_sec),
        "duration_seconds": duration_sec,
        "clip_count": len(clips),
        "word_count": word_count,
        "speaking_pace_wpm": wpm,
        "speakers": speaker_count,
        "media_type": "voice" if is_voice_memo else ("video" if len(clips) >= 1 else "mixed"),
        "language": det_lang,
        "languages_detected": lang_dist,
        "confidence": ctx.payload.get("confidence_avg", 1.0),
        "recording_device": device,
        "original_resolution": resolution,
        "file_size_mb": file_size_mb,
        "whisper_model": getattr(getattr(ctx.config, "transcription", None), "model", "large-v3")
        if ctx.config
        else "large-v3",
        "llm_model": ctx.payload.get("llm_model", "ollama/qwen2.5:14b-q3_K_M"),
        "llm_fallback_used": ctx.payload.get("llm_fallback_used", False),
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "entry_version": 1,
    }

    ctx.payload["media_stats"] = stats
    logger.info("Media stats computed", duration=stats["duration"], word_count=word_count, wpm=wpm)
    return ctx
