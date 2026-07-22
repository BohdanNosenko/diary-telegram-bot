import gc
import os
import structlog
import torch

from vlog_journal.pipeline.registry import PipelineContext, register_step

logger = structlog.get_logger(__name__)

def format_timestamp(seconds: float) -> str:
    """Format seconds into MM:SS or HH:MM:SS format."""
    total_sec = int(round(seconds))
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"

@register_step("transcription.diarize_speakers")
async def diarize_speakers(ctx: PipelineContext) -> PipelineContext:
    """Perform speaker diarization using pyannote.audio and unload model from VRAM immediately."""
    audio_wav_path = ctx.payload.get("audio_wav_path")
    if not audio_wav_path:
        raise ValueError("audio_wav_path missing from pipeline context")

    diar_cfg = getattr(ctx.config, "diarization", None) if ctx.config else None
    enabled = getattr(diar_cfg, "enabled", True) if diar_cfg else True
    min_speakers = getattr(diar_cfg, "min_speakers", 1) if diar_cfg else 1
    max_speakers = getattr(diar_cfg, "max_speakers", 5) if diar_cfg else 5

    if not enabled:
        logger.info("Diarization disabled in config, skipping pyannote step")
        ctx.payload["diarization_segments"] = []
        return ctx

    # Retrieve HF Token
    hf_token = None
    if ctx.config and getattr(ctx.config, "hf_token", None):
        hf_token = ctx.config.hf_token.get_secret_value()
    elif os.getenv("HF_TOKEN"):
        hf_token = os.getenv("HF_TOKEN")

    if not hf_token:
        logger.warning("HF_TOKEN missing, skipping diarization and defaulting to single speaker")
        ctx.payload["diarization_segments"] = []
        return ctx

    try:
        from pyannote.audio import Pipeline
    except ImportError:
        logger.warning("pyannote.audio not installed, skipping diarization")
        ctx.payload["diarization_segments"] = []
        return ctx

    logger.info("Loading pyannote diarization pipeline", min_speakers=min_speakers, max_speakers=max_speakers)

    pipeline = None
    try:
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=hf_token,
        )

        device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda":
            pipeline.to(torch.device("cuda"))

        diarization = pipeline(
            audio_wav_path,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )

        diarization_segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            # speaker string e.g. "SPEAKER_00" or "0" -> "Speaker 1"
            spk_label = str(speaker)
            if "_" in spk_label:
                try:
                    num = int(spk_label.split("_")[-1]) + 1
                    spk_label = f"Speaker {num}"
                except ValueError:
                    spk_label = f"Speaker {spk_label}"
            else:
                spk_label = f"Speaker {spk_label}"

            diarization_segments.append(
                {
                    "start": round(float(turn.start), 2),
                    "end": round(float(turn.end), 2),
                    "speaker": spk_label,
                }
            )

        ctx.payload["diarization_segments"] = diarization_segments
        logger.info("Diarization complete", turn_count=len(diarization_segments))
    except Exception as e:
        logger.error("Error during pyannote diarization", error=str(e))
        ctx.payload["diarization_segments"] = []
    finally:
        if pipeline is not None:
            del pipeline
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("pyannote pipeline unloaded and VRAM cache cleared")

    return ctx

@register_step("transcription.merge_segments")
async def merge_segments(ctx: PipelineContext) -> PipelineContext:
    """Pure Python step — merge Whisper timestamped segments with pyannote speaker labels."""
    raw_segments = ctx.payload.get("raw_segments", [])
    diarization_segments = ctx.payload.get("diarization_segments", [])

    if not raw_segments:
        logger.warning("No raw_segments found to merge")
        ctx.payload["labeled_segments"] = []
        return ctx

    labeled_segments = []

    for seg in raw_segments:
        seg_start = seg["start"]
        seg_end = seg["end"]
        best_speaker = "Speaker 1"
        max_overlap = 0.0

        if diarization_segments:
            for d in diarization_segments:
                overlap_start = max(seg_start, d["start"])
                overlap_end = min(seg_end, d["end"])
                overlap = max(0.0, overlap_end - overlap_start)

                if overlap > max_overlap:
                    max_overlap = overlap
                    best_speaker = d["speaker"]

        ts_str = f"{format_timestamp(seg_start)} - {format_timestamp(seg_end)}"
        labeled_segments.append(
            {
                "speaker": best_speaker,
                "timestamp": ts_str,
                "start": seg_start,
                "end": seg_end,
                "text": seg["text"],
            }
        )

    ctx.payload["labeled_segments"] = labeled_segments
    logger.info("Transcript segments merged with speaker labels", labeled_count=len(labeled_segments))
    return ctx
