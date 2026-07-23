import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from vlog_journal.pipeline.registry import PipelineContext
from vlog_journal.enrichment.gps import (
    extract_gps,
    haversine_distance,
    parse_iso6709_gps,
    reverse_geocode,
)
from vlog_journal.enrichment.weather import fetch_weather, map_wmo_code
from vlog_journal.enrichment.stats import compute_media_stats, format_duration


def test_parse_iso6709_gps():
    assert parse_iso6709_gps("+40.7128-074.0060/") == (40.7128, -74.0060)
    assert parse_iso6709_gps("-33.8688+151.2093+010.000/") == (-33.8688, 151.2093)
    assert parse_iso6709_gps("invalid") is None
    assert parse_iso6709_gps("") is None


def test_haversine_distance():
    # Distance between two points ~100m apart
    d = haversine_distance(40.7128, -74.0060, 40.7137, -74.0060)
    assert 90 < d < 110


@pytest.mark.asyncio
async def test_extract_gps_deduplication():
    ctx = PipelineContext(chat_id=123, config=MagicMock())
    ctx.payload = {
        "clips": [
            {"path": "/tmp/clip1.mp4"},
            {"path": "/tmp/clip2.mp4"},
            {"path": "/tmp/clip3.mp4"},
        ]
    }

    # Mock _extract_clip_gps to return coords: clip1 & clip2 close (~100m), clip3 far (NYC vs LA)
    async def mock_extract(path):
        if "clip1" in path:
            return (40.7128, -74.0060)
        elif "clip2" in path:
            return (40.7135, -74.0060)
        elif "clip3" in path:
            return (34.0522, -118.2437)
        return None

    with patch("vlog_journal.enrichment.gps._extract_clip_gps", side_effect=mock_extract), \
         patch("pathlib.Path.exists", return_value=True):
        res_ctx = await extract_gps(ctx)

    locs = res_ctx.payload["locations_visited"]
    assert len(locs) == 2  # NYC cluster and LA cluster
    assert locs[0]["clips"] == [1, 2]
    assert locs[1]["clips"] == [3]


@pytest.mark.asyncio
async def test_reverse_geocode():
    ctx = PipelineContext(chat_id=123, config=MagicMock())
    ctx.payload = {
        "locations_visited": [
            {"gps": [40.7128, -74.0060], "clips": [1, 2]},
            {"gps": [34.0522, -118.2437], "clips": [3]},
        ]
    }

    res_ctx = await reverse_geocode(ctx)
    assert res_ctx.payload["primary_location"] is not None
    assert "New York" in res_ctx.payload["primary_location"] or "NYC" in res_ctx.payload["primary_location"]
    locs = res_ctx.payload["locations_visited"]
    assert "name" in locs[0]
    assert "name" in locs[1]


def test_map_wmo_code():
    assert map_wmo_code(0) == "clear sky"
    assert map_wmo_code(3) == "overcast"
    assert map_wmo_code(95) == "thunderstorm"
    assert map_wmo_code(999) == "partly cloudy"


@pytest.mark.asyncio
async def test_fetch_weather_success():
    ctx = PipelineContext(chat_id=123, config=MagicMock())
    ctx.payload = {
        "entry_date": "2026-07-20",
        "locations_visited": [
            {"gps": [40.7128, -74.0060], "name": "New York City", "clips": [1]},
        ],
        "primary_location": "New York City",
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "daily": {
            "temperature_2m_max": [24.3],
            "temperature_2m_min": [18.1],
            "weathercode": [2],
        }
    }

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
        res_ctx = await fetch_weather(ctx)

    assert res_ctx.payload["primary_weather"] == "24°C, partly cloudy"
    assert res_ctx.payload["locations_visited"][0]["weather"] == "24°C, partly cloudy"


@pytest.mark.asyncio
async def test_fetch_weather_network_failure_non_fatal():
    ctx = PipelineContext(chat_id=123, config=MagicMock())
    ctx.payload = {
        "entry_date": "2026-07-20",
        "locations_visited": [
            {"gps": [40.7128, -74.0060], "name": "New York City", "clips": [1]},
        ],
    }

    with patch("httpx.AsyncClient.get", side_effect=Exception("Network error")):
        res_ctx = await fetch_weather(ctx)

    assert res_ctx.payload["primary_weather"] is None
    assert res_ctx.payload["locations_visited"][0]["weather"] is None


def test_format_duration():
    assert format_duration(65) == "01:05"
    assert format_duration(3665) == "01:01:05"


@pytest.mark.asyncio
async def test_compute_media_stats():
    ctx = PipelineContext(chat_id=123, config=MagicMock())
    ctx.payload = {
        "clips": [{"path": "/tmp/clip1.mp4"}, {"path": "/tmp/clip2.mp4"}],
        "labeled_segments": [
            {"speaker": "Speaker 1", "start": 0.0, "end": 10.0, "text": "Hello world"},
            {"speaker": "Speaker 2", "start": 10.0, "end": 60.0, "text": "This is a vlog entry"},
        ],
        "is_voice_memo": False,
        "detected_language": "en",
        "confidence_avg": 0.95,
        "llm_model": "ollama/qwen2.5:14b-q3_K_M",
    }

    with patch("vlog_journal.enrichment.stats._get_media_metadata", return_value={"resolution": "1920x1080", "recording_device": "Samsung SM-S928U"}):
        res_ctx = await compute_media_stats(ctx)

    stats = res_ctx.payload["media_stats"]
    assert stats["duration"] == "01:00"
    assert stats["clip_count"] == 2
    assert stats["word_count"] == 7
    assert stats["speakers"] == 2
    assert stats["speaking_pace_wpm"] == 7
    assert stats["recording_device"] == "Samsung SM-S928U"
    assert stats["original_resolution"] == "1920x1080"
    assert stats["language"] == "en"
    assert stats["media_type"] == "video"
