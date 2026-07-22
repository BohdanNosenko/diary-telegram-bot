import pytest
from unittest.mock import AsyncMock, patch
from pydantic import ValidationError

from vlog_journal.pipeline.registry import PipelineContext
from vlog_journal.processors.llm import build_prompt_contexts, format_transcript_block, structure_transcript
from vlog_journal.processors.schemas import (
    NoteSchema,
)

def test_note_schema_full():
    data = {
        "title": "Evening Walk and Health Check",
        "summary": "Вечерняя прогулка в парке и размышления о здоровье.",
        "mood": "relaxed",
        "energy_level": "medium",
        "category": "reflection",
        "people": ["Mom", "Dima"],
        "topics": ["health", "nature"],
        "locations_mentioned": ["Central Park"],
        "key_highlights": ["Walked 5km", "Discussed vacation plans"],
        "cleaned_transcript": [
            {"speaker": "Speaker 1", "timestamp": "00:00 - 00:15", "text": "Hello world"}
        ],
        "action_items": ["Buy groceries"],
        "notable_quotes": ["Прекрасный вечер"],
        "health": {
            "sleep": {"quality": "good", "hours": 8.0, "notes": "Slept deeply"},
            "exercise": [{"activity": "walking", "duration": "45 min", "intensity": "light"}],
            "pain_or_discomfort": [{"location": "knee", "severity": "mild"}],
            "mental_state": {"stress_level": "low"},
        },
    }
    schema = NoteSchema(**data)
    assert schema.title == "Evening Walk and Health Check"
    assert schema.energy_level == "medium"
    assert len(schema.people) == 2
    assert schema.health.sleep.hours == 8.0
    assert len(schema.health.exercise) == 1

def test_note_schema_minimal():
    data = {
        "title": "Short Reflection",
        "summary": "Короткая заметка.",
        "mood": "neutral",
        "energy_level": "low",
        "category": "routine",
        "people": [],
        "topics": ["daily"],
        "locations_mentioned": [],
        "key_highlights": ["Started day early"],
        "cleaned_transcript": [],
    }
    schema = NoteSchema(**data)
    assert schema.title == "Short Reflection"
    assert schema.action_items == []
    assert schema.gratitude == []
    assert schema.health.exercise == []

def test_note_schema_invalid_energy():
    data = {
        "title": "Invalid Energy Test",
        "summary": "Test",
        "mood": "happy",
        "energy_level": "extreme",  # Invalid
        "category": "routine",
        "key_highlights": ["Test"],
    }
    with pytest.raises(ValidationError):
        NoteSchema(**data)

def test_build_prompt_contexts(tmp_path):
    ctx = PipelineContext(
        chat_id=123,
        config=None,
        payload={
            "clips": [{"caption": "With Mom in park"}],
            "speaker_map": {"Speaker 1": "Me", "Speaker 2": "Mom"},
        },
    )
    prompt_ctx = build_prompt_contexts(ctx)
    assert "With Mom in park" in prompt_ctx["caption_context"]
    assert "Speaker 1 -> Me" in prompt_ctx["speaker_context"]

def test_format_transcript_block():
    segments = [
        {"speaker": "Speaker 1", "timestamp": "00:00 - 00:05", "text": "Hello"},
        {"speaker": "Speaker 2", "timestamp": "00:06 - 00:10", "text": "Hi there"},
    ]
    block = format_transcript_block(segments)
    assert "[Speaker 1] (00:00 - 00:05): Hello" in block
    assert "[Speaker 2] (00:06 - 00:10): Hi there" in block

@pytest.mark.asyncio
async def test_structure_transcript_primary_success():
    ctx = PipelineContext(
        chat_id=555,
        config=None,
        payload={
            "labeled_segments": [
                {"speaker": "Speaker 1", "timestamp": "00:00 - 00:05", "text": "Hello world"}
            ]
        },
    )

    mock_llm_response = {
        "title": "Hello World Note",
        "summary": "Приветственное видео.",
        "mood": "cheerful",
        "energy_level": "high",
        "category": "creative",
        "people": ["Me"],
        "topics": ["intro"],
        "locations_mentioned": [],
        "key_highlights": ["Said hello"],
        "cleaned_transcript": [
            {"speaker": "Speaker 1", "timestamp": "00:00 - 00:05", "text": "Hello world"}
        ],
    }

    with patch("vlog_journal.processors.llm._call_litellm_structured", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_llm_response
        ctx = await structure_transcript(ctx)

        assert "note_schema" in ctx.payload
        assert ctx.payload["note_schema"]["title"] == "Hello World Note"
        assert ctx.payload["llm_fallback_used"] is False

@pytest.mark.asyncio
async def test_structure_transcript_fallback_trigger():
    ctx = PipelineContext(
        chat_id=666,
        config=None,
        payload={
            "labeled_segments": [
                {"speaker": "Speaker 1", "timestamp": "00:00 - 00:05", "text": "Fallback test"}
            ]
        },
    )

    mock_fallback_response = {
        "title": "Fallback Response Note",
        "summary": "Заметка после падения первого провайдера.",
        "mood": "calm",
        "energy_level": "medium",
        "category": "reflection",
        "key_highlights": ["Fallback worked"],
        "cleaned_transcript": [],
    }

    async def mock_litellm_side_effect(model, **kwargs):
        if "ollama" in model:
            raise RuntimeError("Ollama connection refused")
        return mock_fallback_response

    with patch("vlog_journal.processors.llm._call_litellm_structured", side_effect=mock_litellm_side_effect):
        ctx = await structure_transcript(ctx)

        assert "note_schema" in ctx.payload
        assert ctx.payload["note_schema"]["title"] == "Fallback Response Note"
        assert ctx.payload["llm_fallback_used"] is True
