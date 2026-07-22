import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
import pytest

from vlog_journal.pipeline.registry import PipelineContext
from vlog_journal.processors.media import (
    RESOLUTION_MAP,
    cleanup_temp_files,
    extract_audio,
    get_item_creation_date,
    prepare_and_stitch,
)

@pytest.fixture
def synthetic_media(tmp_path: Path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not available")

    v1 = tmp_path / "v1.mp4"
    v2 = tmp_path / "v2.mp4"
    a1 = tmp_path / "a1.mp3"
    a2 = tmp_path / "a2.mp3"

    # Generate 1s test videos with audio
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30", "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=44100", "-t", "1", "-c:v", "libx264", "-c:a", "aac", str(v1)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30", "-f", "lavfi", "-i", "sine=frequency=800:sample_rate=44100", "-t", "1", "-c:v", "libx264", "-c:a", "aac", str(v2)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )

    # Generate 1s test voice audio
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "1", "-c:a", "libmp3lame", str(a1)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=44100", "-t", "1", "-c:a", "libmp3lame", str(a2)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )

    return {"v1": str(v1), "v2": str(v2), "a1": str(a1), "a2": str(a2)}

def test_resolution_map():
    assert "original" in RESOLUTION_MAP
    assert RESOLUTION_MAP["720p"] == "1280:720"
    assert RESOLUTION_MAP["360p"] == "640:360"
    assert len(RESOLUTION_MAP) == 7

@pytest.mark.asyncio
async def test_get_item_creation_date(synthetic_media):
    dt = await get_item_creation_date(synthetic_media["v1"])
    assert isinstance(dt, datetime)
    assert dt.tzinfo is not None

@pytest.mark.asyncio
async def test_voice_only_processing(synthetic_media):
    ctx = PipelineContext(
        chat_id=8888,
        config=None,
        payload={
            "clips": [
                {"path": synthetic_media["a1"], "type": "voice", "caption": None},
                {"path": synthetic_media["a2"], "type": "voice", "caption": None},
            ]
        },
    )

    # 1. Prepare and stitch
    ctx = await prepare_and_stitch(ctx)
    assert ctx.payload["is_voice_memo"] is True
    assert "raw_audio_path" in ctx.payload
    assert os.path.exists(ctx.payload["raw_audio_path"])
    assert "audio_wav_path" in ctx.payload
    assert os.path.exists(ctx.payload["audio_wav_path"])

    # 2. Extract audio (should be no-op)
    ctx = await extract_audio(ctx)
    assert os.path.exists(ctx.payload["audio_wav_path"])

    # 3. Cleanup
    ctx = await cleanup_temp_files(ctx)
    assert not os.path.exists(Path("data/temp/8888"))

@pytest.mark.asyncio
async def test_video_processing(synthetic_media):
    ctx = PipelineContext(
        chat_id=9999,
        config=None,
        payload={
            "clips": [
                {"path": synthetic_media["v1"], "type": "video", "caption": "Clip 1"},
                {"path": synthetic_media["v2"], "type": "video", "caption": "Clip 2"},
            ]
        },
    )

    # 1. Prepare and stitch
    ctx = await prepare_and_stitch(ctx)
    assert ctx.payload["is_voice_memo"] is False
    assert "raw_video_path" in ctx.payload
    assert os.path.exists(ctx.payload["raw_video_path"])

    # 2. Extract audio for Whisper
    ctx = await extract_audio(ctx)
    assert "audio_wav_path" in ctx.payload
    assert os.path.exists(ctx.payload["audio_wav_path"])

    # 3. Cleanup
    ctx = await cleanup_temp_files(ctx)
    assert not os.path.exists(Path("data/temp/9999"))

@pytest.mark.asyncio
async def test_single_video_original_resolution(synthetic_media):
    ctx = PipelineContext(
        chat_id=7777,
        config=None,
        payload={
            "clips": [
                {"path": synthetic_media["v1"], "type": "video", "caption": "Single"},
            ]
        },
    )

    ctx = await prepare_and_stitch(ctx)
    assert ctx.payload["is_voice_memo"] is False
    assert os.path.exists(ctx.payload["raw_video_path"])

    await cleanup_temp_files(ctx)
