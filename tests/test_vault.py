from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vlog_journal.pipeline.registry import PipelineContext
from vlog_journal.vault.renderer import render_markdown
from vlog_journal.vault.storage import resolve_collision_filename, save_entry
from vlog_journal.vault.tags import TagManager, generate_tags, slugify, update_tag_cache


def test_slugify():
    assert slugify("New York City") == "new-york-city"
    assert slugify("Mom's Recipe!") == "moms-recipe"
    assert slugify("  hello   world  ") == "hello-world"
    assert slugify("") == ""


def test_generate_tags():
    tags = generate_tags(
        people=["Mom", "Dima"],
        topics=["cooking", "family recipes"],
        primary_location="Brooklyn, New York",
        category="conversation",
        is_voice_memo=False,
    )

    assert "journal/vlog" in tags
    assert "category/conversation" in tags
    assert "people/mom" in tags
    assert "people/dima" in tags
    assert "topic/cooking" in tags
    assert "topic/family-recipes" in tags
    assert "location/brooklyn" in tags


def test_generate_tags_voice_memo():
    tags = generate_tags(
        people=[],
        topics=["quick thought"],
        primary_location=None,
        category="reflection",
        is_voice_memo=True,
    )

    assert "journal/voice" in tags
    assert "category/reflection" in tags
    assert "topic/quick-thought" in tags


def test_tag_manager_add_and_load(tmp_path):
    cache_file = tmp_path / "tags.json"
    tm = TagManager(cache_file=cache_file)

    assert tm.get_tags() == []

    tm.add_tags(["journal/vlog", "topic/cooking"])
    assert tm.get_tags() == ["journal/vlog", "topic/cooking"]

    # Deduplication and sorting
    tm.add_tags(["topic/cooking", "people/mom"])
    assert tm.get_tags() == ["journal/vlog", "people/mom", "topic/cooking"]


def test_tag_manager_sync_from_vault(tmp_path):
    vault_dir = tmp_path / "Vault"
    vlogs_dir = vault_dir / "Journal" / "Vlogs"
    vlogs_dir.mkdir(parents=True)

    note1 = vlogs_dir / "2026-07-20.md"
    note1.write_text(
        "---\ntitle: Note 1\ntags:\n  - journal/vlog\n  - people/mom\n---\nBody",
        encoding="utf-8",
    )

    note2 = vlogs_dir / "2026-07-21.md"
    note2.write_text(
        "---\ntitle: Note 2\ntags:\n  - journal/vlog\n  - topic/cooking\n---\nBody",
        encoding="utf-8",
    )

    cache_file = tmp_path / "tags.json"
    tm = TagManager(cache_file=cache_file)
    synced = tm.sync_from_vault(vault_dir)

    assert synced == ["journal/vlog", "people/mom", "topic/cooking"]
    assert tm.get_tags() == ["journal/vlog", "people/mom", "topic/cooking"]


def test_resolve_collision_filename(tmp_path):
    journal_dir = tmp_path / "Journal" / "Vlogs"
    journal_dir.mkdir(parents=True)

    # 1. No collision
    stem1, media1 = resolve_collision_filename(journal_dir, "2026-07-20", ".mp4")
    assert stem1 == "2026-07-20"
    assert media1 == "2026-07-20.mp4"

    # Create 2026-07-20.md
    (journal_dir / "2026-07-20.md").write_text("content")

    # 2. First collision -> 2026-07-20-02
    stem2, media2 = resolve_collision_filename(journal_dir, "2026-07-20", ".mp4")
    assert stem2 == "2026-07-20-02"
    assert media2 == "2026-07-20-02.mp4"

    # Create 2026-07-20-02.md
    (journal_dir / "2026-07-20-02.md").write_text("content")

    # 3. Second collision -> 2026-07-20-03
    stem3, media3 = resolve_collision_filename(journal_dir, "2026-07-20", ".mp4")
    assert stem3 == "2026-07-20-03"
    assert media3 == "2026-07-20-03.mp4"


@pytest.mark.asyncio
async def test_render_markdown():
    ctx = PipelineContext(chat_id=123, config=MagicMock())
    ctx.payload = {
        "entry_date": "2026-07-20",
        "entry_time": "22:30",
        "is_voice_memo": False,
        "primary_location": "Brooklyn, New York",
        "primary_weather": "24°C, partly cloudy",
        "note_schema": {
            "title": "Evening cooking session with Mom",
            "summary": "Cooked borscht with Mom. Discussed weekend plans.",
            "mood": "relaxed",
            "energy_level": "medium",
            "category": "conversation",
            "people": ["Mom"],
            "topics": ["cooking", "family"],
            "locations_mentioned": ["grandmother's house"],
            "key_highlights": [
                "Tried roasting beets instead of boiling",
                "Planned family dinner for Saturday",
            ],
            "action_items": ["Buy beets at farmer's market"],
            "notable_quotes": ['"The secret is always in the beet" — Mom'],
            "health": {
                "sleep": {"quality": "good", "hours": 7.5, "notes": None},
                "exercise": [
                    {"activity": "gym", "duration": "1h", "intensity": "heavy", "notes": "PR deadlift"}
                ],
            },
            "cleaned_transcript": [
                {"speaker": "Speaker 1", "timestamp": "00:15", "text": "Let us make borscht."},
                {"speaker": "Speaker 2", "timestamp": "00:30", "text": "Sure, I have the beets."},
            ],
        },
        "media_stats": {
            "duration": "00:05:32",
            "clip_count": 3,
            "word_count": 847,
            "speakers": 2,
            "speaking_pace_wpm": 154,
            "language": "ru",
            "languages_detected": {"ru": 78, "en": 22},
            "confidence": 0.89,
            "recording_device": "Samsung SM-S928U",
            "original_resolution": "1920x1080",
            "file_size_mb": 45.2,
            "whisper_model": "large-v3",
            "llm_model": "ollama/qwen2.5:14b-q3_K_M",
            "llm_fallback_used": False,
            "processed_at": "2026-07-21T01:15:00",
            "entry_version": 1,
            "media_type": "video",
        },
    }

    res_ctx = await render_markdown(ctx)

    md = res_ctx.payload["draft_markdown"]
    assert "title: \"Evening cooking session with Mom\"" in md
    assert "mood: relaxed" in md
    assert "primary_location: Brooklyn, New York" in md
    assert "primary_weather: 24°C, partly cloudy" in md
    assert "![[2026-07-20.mp4]]" in md
    assert "# Vlog — 2026-07-20" in md
    assert "- [ ] Buy beets at farmer's market" in md
    assert "> [!NOTE]- Full Transcript (847 words, 00:05:32)" in md
    assert "**Speaker 1** *(00:15)*" in md
    assert "journal/vlog" in res_ctx.payload["tags"]
    assert "- journal/vlog" in md
    assert "- people/mom" in md


@pytest.mark.asyncio
async def test_save_entry(tmp_path):
    vault_dir = tmp_path / "PersonalVault"

    mock_app_cfg = MagicMock()
    mock_app_cfg.vault_path = vault_dir
    mock_app_cfg.vlogs_relative_path = "Journal/Vlogs"
    mock_app_cfg.media_relative_path = "Attachments/Vlogs"

    mock_config = MagicMock()
    mock_config.app = mock_app_cfg

    # Create dummy raw video file
    temp_video = tmp_path / "raw_video.mp4"
    temp_video.write_bytes(b"dummy video data")

    ctx = PipelineContext(chat_id=123, config=mock_config)
    ctx.payload = {
        "entry_date": "2026-07-20",
        "is_voice_memo": False,
        "raw_video_path": str(temp_video),
        "draft_markdown": "---\ntitle: Test\n---\n\n![[2026-07-20.mp4]]\n\n# Test Body",
    }

    res_ctx = await save_entry(ctx)

    md_path = Path(res_ctx.payload["final_markdown_path"])
    media_path = Path(res_ctx.payload["final_media_path"])

    assert md_path.exists()
    assert media_path.exists()
    assert md_path.name == "2026-07-20.md"
    assert media_path.name == "2026-07-20.mp4"
    assert media_path.read_bytes() == b"dummy video data"


@pytest.mark.asyncio
async def test_update_tag_cache(tmp_path):
    cache_file = tmp_path / "tags.json"
    mock_app_cfg = MagicMock()
    mock_app_cfg.tags_cache_file = cache_file

    mock_config = MagicMock()
    mock_config.app = mock_app_cfg

    ctx = PipelineContext(chat_id=123, config=mock_config)
    ctx.payload = {"tags": ["journal/vlog", "people/mom"]}

    await update_tag_cache(ctx)

    tm = TagManager(cache_file=cache_file)
    assert tm.get_tags() == ["journal/vlog", "people/mom"]
