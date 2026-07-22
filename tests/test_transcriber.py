import shutil
import subprocess
from pathlib import Path
import pytest

from vlog_journal.pipeline.registry import PipelineContext
from vlog_journal.processors.diarizer import diarize_speakers, format_timestamp, merge_segments
from vlog_journal.processors.transcriber import whisper_transcribe

@pytest.fixture
def sample_wav(tmp_path: Path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not available")

    wav_path = tmp_path / "sample.wav"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            "2",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(wav_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    return str(wav_path)

def test_format_timestamp():
    assert format_timestamp(5.2) == "00:05"
    assert format_timestamp(125.0) == "02:05"
    assert format_timestamp(3665.0) == "01:01:05"

@pytest.mark.asyncio
async def test_whisper_transcribe(sample_wav):
    ctx = PipelineContext(
        chat_id=111,
        config=None,
        payload={"audio_wav_path": sample_wav},
    )

    ctx = await whisper_transcribe(ctx)
    assert "raw_segments" in ctx.payload
    assert "detected_language" in ctx.payload
    assert "confidence_avg" in ctx.payload
    assert isinstance(ctx.payload["raw_segments"], list)

@pytest.mark.asyncio
async def test_diarization_disabled(sample_wav):
    ctx = PipelineContext(
        chat_id=222,
        config=None,
        payload={"audio_wav_path": sample_wav},
    )

    ctx = await diarize_speakers(ctx)
    assert ctx.payload["diarization_segments"] == []

@pytest.mark.asyncio
async def test_merge_segments_fallback():
    ctx = PipelineContext(
        chat_id=333,
        config=None,
        payload={
            "raw_segments": [
                {"start": 0.0, "end": 2.5, "text": "Hello world", "confidence": 0.95},
                {"start": 3.0, "end": 5.0, "text": "Testing diary", "confidence": 0.90},
            ],
            "diarization_segments": [],
        },
    )

    ctx = await merge_segments(ctx)
    labeled = ctx.payload["labeled_segments"]
    assert len(labeled) == 2
    assert labeled[0]["speaker"] == "Speaker 1"
    assert labeled[0]["timestamp"] == "00:00 - 00:02"
    assert labeled[0]["text"] == "Hello world"
    assert labeled[1]["speaker"] == "Speaker 1"

@pytest.mark.asyncio
async def test_merge_segments_with_diarization():
    ctx = PipelineContext(
        chat_id=444,
        config=None,
        payload={
            "raw_segments": [
                {"start": 0.0, "end": 2.0, "text": "First speaker phrase"},
                {"start": 3.0, "end": 6.0, "text": "Second speaker phrase"},
            ],
            "diarization_segments": [
                {"start": 0.0, "end": 2.5, "speaker": "Speaker 1"},
                {"start": 2.8, "end": 6.5, "speaker": "Speaker 2"},
            ],
        },
    )

    ctx = await merge_segments(ctx)
    labeled = ctx.payload["labeled_segments"]
    assert len(labeled) == 2
    assert labeled[0]["speaker"] == "Speaker 1"
    assert labeled[1]["speaker"] == "Speaker 2"
