import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import py7zr

from vlog_journal.pipeline.registry import PipelineContext
from vlog_journal.pipeline.runner import run_pipeline
from vlog_journal.vault.tags import TagManager

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


@pytest.fixture
def mock_app_config(tmp_path: Path):
    vault_dir = tmp_path / "PersonalVault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    (vault_dir / "Journal" / "Vlogs").mkdir(parents=True, exist_ok=True)
    (vault_dir / "Attachments" / "Vlogs").mkdir(parents=True, exist_ok=True)

    tags_cache = tmp_path / "data" / "tags.json"
    tags_cache.parent.mkdir(parents=True, exist_ok=True)

    cfg = MagicMock()
    cfg.app.vault_path = str(vault_dir)
    cfg.app.vlogs_relative_path = "Journal/Vlogs"
    cfg.app.media_relative_path = "Attachments/Vlogs"
    cfg.app.tags_cache_file = str(tags_cache)
    cfg.app.sessions_state_file = str(tmp_path / "data" / "sessions.json")
    cfg.backup_encryption_passphrase.get_secret_value.return_value = "TestSecret123"
    cfg.backup.remote_name = "gdrive"
    cfg.backup.remote_folder = "vlog-backups"
    cfg.backup.retention_daily_days = 2
    cfg.backup.retention_weekly_weeks = 1
    return cfg


@pytest.mark.asyncio
async def test_e2e_single_video_pipeline(tmp_path: Path, mock_app_config):
    clip_file = tmp_path / "clip1.mp4"
    clip_file.write_bytes(b"dummy video clip content")

    ctx = PipelineContext(chat_id=123, config=mock_app_config)
    ctx.payload = {
        "clips": [{"path": str(clip_file), "type": "video", "caption": "Cooking with Mom"}],
        "entry_date": "2026-07-22",
        "speaker_map": {},
    }

    # Mock step implementations
    async def mock_prepare(c: PipelineContext) -> PipelineContext:
        temp_dir = Path("data/temp") / str(c.chat_id)
        temp_dir.mkdir(parents=True, exist_ok=True)
        raw_video = temp_dir / "raw_video.mp4"
        raw_video.write_bytes(b"dummy video content")
        c.payload["raw_video_path"] = str(raw_video)
        c.payload["is_voice_memo"] = False
        return c

    async def mock_extract_audio(c: PipelineContext) -> PipelineContext:
        temp_dir = Path("data/temp") / str(c.chat_id)
        temp_dir.mkdir(parents=True, exist_ok=True)
        audio_wav = temp_dir / "audio.wav"
        audio_wav.write_bytes(b"dummy audio wav content")
        c.payload["audio_wav_path"] = str(audio_wav)
        return c

    async def mock_whisper(c: PipelineContext) -> PipelineContext:
        c.payload["raw_segments"] = [{"start": 0.0, "end": 10.0, "text": "Cooking borscht with Mom"}]
        c.payload["detected_language"] = "en"
        c.payload["confidence_avg"] = 0.95
        return c

    async def mock_diarize(c: PipelineContext) -> PipelineContext:
        c.payload["diarization"] = None
        return c

    async def mock_merge(c: PipelineContext) -> PipelineContext:
        c.payload["labeled_segments"] = [
            {"speaker": "Speaker 1", "start": 0.0, "end": 10.0, "text": "Cooking borscht with Mom"}
        ]
        return c

    async def mock_llm(c: PipelineContext) -> PipelineContext:
        c.payload["note_schema"] = {
            "title": "Cooking borscht",
            "summary": "Cooked borscht with family.",
            "category": "personal",
            "mood": "happy",
            "energy_level": "medium",
            "people": ["Mom"],
            "topics": ["cooking"],
            "locations_mentioned": [],
            "key_highlights": ["Tried new recipe"],
            "action_items": ["Buy beets"],
        }
        return c

    async def mock_gps(c: PipelineContext) -> PipelineContext:
        c.payload["locations_visited"] = [{"gps": [40.7128, -74.0060], "clips": [1]}]
        return c

    async def mock_geocode(c: PipelineContext) -> PipelineContext:
        c.payload["primary_location"] = "New York, NY"
        c.payload["locations_visited"][0]["name"] = "New York, NY"
        return c

    async def mock_weather(c: PipelineContext) -> PipelineContext:
        c.payload["primary_weather"] = "24°C, partly cloudy"
        return c

    with patch.dict("vlog_journal.pipeline.registry._REGISTRY", {
             "media.prepare_and_stitch": mock_prepare,
             "media.extract_audio": mock_extract_audio,
             "transcription.whisper_transcribe": mock_whisper,
             "transcription.diarize_speakers": mock_diarize,
             "transcription.merge_segments": mock_merge,
             "llm.structure_transcript": mock_llm,
             "enrichment.extract_gps": mock_gps,
             "enrichment.reverse_geocode": mock_geocode,
             "enrichment.fetch_weather": mock_weather,
         }):

        # 1. Run Draft Pipeline
        draft_ctx = await run_pipeline(DRAFT_PIPELINE_STEPS, ctx)
        assert draft_ctx.payload.get("draft_markdown") is not None
        assert "# Vlog — 2026-07-22" in draft_ctx.payload["draft_markdown"]

        # Ensure raw_video_path exists for storage move
        if not Path(draft_ctx.payload["raw_video_path"]).exists():
            Path(draft_ctx.payload["raw_video_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(draft_ctx.payload["raw_video_path"]).write_bytes(b"video content")

        # 2. Run Approve Pipeline
        approve_ctx = await run_pipeline(APPROVE_PIPELINE_STEPS, draft_ctx)
        final_md_path = Path(approve_ctx.payload["final_markdown_path"])
        final_media_path = Path(approve_ctx.payload["final_media_path"])

        assert final_md_path.exists()
        assert final_media_path.exists()
        assert final_md_path.name == "2026-07-22.md"
        assert final_media_path.name == "2026-07-22.mp4"

        # Check tag cache updated
        tag_mgr = TagManager(mock_app_config.app.tags_cache_file)
        cached_tags = tag_mgr.get_tags()
        assert "journal/vlog" in cached_tags
        assert "people/mom" in cached_tags


@pytest.mark.asyncio
async def test_e2e_voice_memo_pipeline(tmp_path: Path, mock_app_config):
    voice_file = tmp_path / "note.ogg"
    voice_file.write_bytes(b"dummy ogg audio")

    ctx = PipelineContext(chat_id=456, config=mock_app_config)
    ctx.payload = {
        "clips": [{"path": str(voice_file), "type": "voice", "caption": None}],
        "entry_date": "2026-07-22",
        "is_voice_memo": True,
        "raw_audio_path": str(voice_file),
        "labeled_segments": [{"speaker": "Speaker 1", "start": 0.0, "end": 5.0, "text": "Voice note thought"}],
        "note_schema": {
            "title": "Quick voice note",
            "summary": "Thought about project.",
            "category": "reflection",
            "mood": "thoughtful",
            "energy_level": "low",
            "people": [],
            "topics": ["reflection"],
            "key_highlights": ["Thought about project"],
        },
    }

    from vlog_journal.vault.renderer import render_markdown
    from vlog_journal.vault.storage import save_entry

    rendered_ctx = await render_markdown(ctx)
    assert "# Voice Note — 2026-07-22" in rendered_ctx.payload["draft_markdown"]

    saved_ctx = await save_entry(rendered_ctx)
    final_md_path = Path(saved_ctx.payload["final_markdown_path"])
    final_media_path = Path(saved_ctx.payload["final_media_path"])

    assert final_md_path.exists()
    assert final_media_path.exists()
    assert final_media_path.name.startswith("2026-07-22")
    assert f"![[{final_media_path.name}]]" in final_md_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_e2e_same_day_collision_suffix(tmp_path: Path, mock_app_config):
    vlogs_dir = Path(mock_app_config.app.vault_path) / "Journal" / "Vlogs"
    (vlogs_dir / "2026-07-22.md").write_text("Existing note", encoding="utf-8")

    clip_file = tmp_path / "clip2.mp4"
    clip_file.write_bytes(b"dummy video clip content")

    ctx = PipelineContext(chat_id=789, config=mock_app_config)
    ctx.payload = {
        "clips": [{"path": str(clip_file), "type": "video", "caption": None}],
        "entry_date": "2026-07-22",
        "is_voice_memo": False,
        "raw_video_path": str(clip_file),
        "labeled_segments": [{"speaker": "Speaker 1", "start": 0.0, "end": 5.0, "text": "Second vlog today"}],
        "note_schema": {
            "title": "Second vlog today",
            "summary": "Evening update.",
            "category": "vlog",
            "mood": "calm",
            "energy_level": "medium",
            "people": [],
            "topics": ["vlog"],
            "key_highlights": ["Second entry today"],
        },
    }

    from vlog_journal.vault.renderer import render_markdown
    from vlog_journal.vault.storage import save_entry

    rendered_ctx = await render_markdown(ctx)
    saved_ctx = await save_entry(rendered_ctx)

    final_md_path = Path(saved_ctx.payload["final_markdown_path"])
    final_media_path = Path(saved_ctx.payload["final_media_path"])

    assert final_md_path.name == "2026-07-22-02.md"
    assert final_media_path.name == "2026-07-22-02.mp4"
    assert "![[2026-07-22-02.mp4]]" in final_md_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_e2e_speaker_relabeling(tmp_path: Path, mock_app_config):
    ctx = PipelineContext(chat_id=101, config=mock_app_config)
    ctx.payload = {
        "entry_date": "2026-07-22",
        "is_voice_memo": False,
        "speaker_map": {"Speaker 1": "Bohdan", "Speaker 2": "Mom"},
        "labeled_segments": [
            {"speaker": "Speaker 1", "start": 0.0, "end": 5.0, "text": "Hi Mom"},
            {"speaker": "Speaker 2", "start": 5.0, "end": 10.0, "text": "Hi Bohdan"},
        ],
        "note_schema": {
            "title": "Conversation with Mom",
            "summary": "Chatting at home.",
            "category": "family",
            "mood": "happy",
            "energy_level": "high",
            "people": ["Mom"],
            "topics": ["family"],
            "key_highlights": ["Chatting"],
        },
    }

    from vlog_journal.vault.renderer import render_markdown
    res_ctx = await render_markdown(ctx)

    markdown = res_ctx.payload["draft_markdown"]
    assert "Bohdan" in markdown or "Mom" in markdown


@pytest.mark.asyncio
async def test_e2e_graceful_degradation_missing_gps_and_weather(tmp_path: Path, mock_app_config):
    ctx = PipelineContext(chat_id=202, config=mock_app_config)
    ctx.payload = {
        "clips": [],
        "entry_date": "2026-07-22",
        "locations_visited": [],
    }

    from vlog_journal.enrichment.gps import reverse_geocode
    from vlog_journal.enrichment.weather import fetch_weather

    res_ctx = await reverse_geocode(ctx)
    assert res_ctx.payload.get("primary_location") is None

    res_ctx_weather = await fetch_weather(res_ctx)
    assert res_ctx_weather.payload.get("primary_weather") is None


@pytest.mark.asyncio
async def test_e2e_encrypted_backup(tmp_path: Path, mock_app_config):
    ctx = PipelineContext(chat_id=303, config=mock_app_config)

    mock_proc_copy = AsyncMock()
    mock_proc_copy.communicate.return_value = (b"", b"")
    mock_proc_copy.returncode = 0

    mock_proc_ls = AsyncMock()
    mock_proc_ls.communicate.return_value = (json.dumps([]).encode("utf-8"), b"")
    mock_proc_ls.returncode = 0

    def mock_subprocess(*args, **kwargs):
        cmd = args
        if "copy" in cmd:
            return mock_proc_copy
        elif "lsjson" in cmd:
            return mock_proc_ls
        return mock_proc_copy

    with patch("shutil.which", return_value="/usr/bin/rclone"), \
         patch("asyncio.create_subprocess_exec", side_effect=mock_subprocess):

        from vlog_journal.vault.backup import create_encrypted_archive, upload_and_prune_remote

        arch_ctx = await create_encrypted_archive(ctx)
        archive_path = Path(arch_ctx.payload["archive_path"])

        assert archive_path.exists()
        assert py7zr.is_7zfile(archive_path)

        upload_ctx = await upload_and_prune_remote(arch_ctx)
        assert upload_ctx.payload.get("backup_complete") is True
        assert not archive_path.exists()
