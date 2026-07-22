import gc
import structlog
import torch
from faster_whisper import WhisperModel

from vlog_journal.pipeline.registry import PipelineContext, register_step

logger = structlog.get_logger(__name__)

@register_step("transcription.whisper_transcribe")
async def whisper_transcribe(ctx: PipelineContext) -> PipelineContext:
    """Transcribe audio WAV using Faster-Whisper and immediately unload model from VRAM."""
    audio_wav_path = ctx.payload.get("audio_wav_path")
    if not audio_wav_path:
        raise ValueError("audio_wav_path missing from pipeline context")

    # Read configuration
    tx_cfg = getattr(ctx.config, "transcription", None) if ctx.config else None
    model_name = getattr(tx_cfg, "model", "base") if tx_cfg else "base"
    device = getattr(tx_cfg, "device", "cpu") if tx_cfg else "cpu"
    compute_type = getattr(tx_cfg, "compute_type", "float32") if tx_cfg else "float32"
    language = getattr(tx_cfg, "language", "auto") if tx_cfg else "auto"

    if device == "cuda" and not torch.cuda.is_available():
        logger.warning("CUDA requested but not available, falling back to CPU")
        device = "cpu"
        compute_type = "float32"

    lang_opt = None if language == "auto" else language

    logger.info(
        "Loading Whisper model",
        model_name=model_name,
        device=device,
        compute_type=compute_type,
        audio_wav_path=audio_wav_path,
    )

    model = None
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        segments, info = model.transcribe(audio_wav_path, language=lang_opt)

        raw_segments = []
        logprobs = []

        for seg in segments:
            conf = float(getattr(seg, "avg_logprob", 0.0))
            logprobs.append(conf)
            raw_segments.append(
                {
                    "start": round(float(seg.start), 2),
                    "end": round(float(seg.end), 2),
                    "text": seg.text.strip(),
                    "confidence": round(conf, 3),
                }
            )

        avg_conf = sum(logprobs) / len(logprobs) if logprobs else 0.0
        detected_lang = getattr(info, "language", "en")

        ctx.payload["raw_segments"] = raw_segments
        ctx.payload["detected_language"] = detected_lang
        ctx.payload["confidence_avg"] = round(float(avg_conf), 3)

        logger.info(
            "Whisper transcription complete",
            segment_count=len(raw_segments),
            detected_language=detected_lang,
            avg_confidence=round(float(avg_conf), 3),
        )
    finally:
        # Unload model and free VRAM immediately
        if model is not None:
            del model
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("Whisper model unloaded and VRAM cache cleared")

    return ctx
