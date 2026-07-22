from typing import Literal
from pydantic import BaseModel, Field

# ─── Health Sub-Models ───

class SleepNote(BaseModel):
    quality: str | None = None  # "good", "poor", "restless"
    hours: float | None = None  # 7.5
    notes: str | None = None  # "woke up at 3 AM"

class ExerciseNote(BaseModel):
    activity: str  # "gym", "running", "yoga"
    duration: str | None = None  # "45 min"
    intensity: str | None = None  # "heavy", "light", "moderate"
    notes: str | None = None  # "increased squat weight"

class PainNote(BaseModel):
    location: str  # "lower back", "right knee"
    severity: str | None = None  # "mild", "moderate", "severe"
    notes: str | None = None  # "worse after sitting"

class MentalNote(BaseModel):
    stress_level: str | None = None  # "low", "moderate", "high"
    anxiety: str | None = None  # noted if mentioned
    notes: str | None = None  # "feeling overwhelmed with deadlines"

class HealthWellness(BaseModel):
    sleep: SleepNote | None = None
    exercise: list[ExerciseNote] = Field(default_factory=list)
    pain_or_discomfort: list[PainNote] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)
    mental_state: MentalNote | None = None
    nutrition: list[str] = Field(default_factory=list)
    substances: list[str] = Field(default_factory=list)
    body_metrics: dict[str, str] = Field(default_factory=dict)

# ─── Transcript ───

class TranscriptSegment(BaseModel):
    speaker: str  # "Speaker 1" → resolved name after labeling
    timestamp: str  # "00:15"
    text: str

# ─── Main Schema ───

class NoteSchema(BaseModel):
    # ── Tier 1: Always generated ──
    title: str = Field(description="Short descriptive title in English (5-10 words)")
    summary: str = Field(description="2-3 sentence summary in the main spoken language")
    mood: str = Field(description="Primary mood/emotion, free text, English")
    energy_level: Literal["low", "medium", "high"] = Field(description="Energy level: low, medium, or high")
    category: str = Field(description="Entry type: reflection, conversation, adventure, routine, creative, work")
    people: list[str] = Field(default_factory=list, description="People mentioned/present, English transliterations")
    topics: list[str] = Field(default_factory=list, description="Key topics discussed, English")
    locations_mentioned: list[str] = Field(default_factory=list, description="Places mentioned in speech, English")
    key_highlights: list[str] = Field(default_factory=list, description="3-5 bullet point highlights, English")
    cleaned_transcript: list[TranscriptSegment] = Field(default_factory=list)

    # ── Tier 2: Best-effort (empty if not mentioned) ──
    action_items: list[str] = Field(default_factory=list)
    questions_raised: list[str] = Field(default_factory=list)
    gratitude: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    notable_quotes: list[str] = Field(default_factory=list, description="In original spoken language")
    media_mentioned: list[str] = Field(default_factory=list)
    food_and_drink: list[str] = Field(default_factory=list)
    plans_future: list[str] = Field(default_factory=list)
    references_past: list[str] = Field(default_factory=list)
    dreams: list[str] = Field(default_factory=list)
    creative_ideas: list[str] = Field(default_factory=list)
    learning: list[str] = Field(default_factory=list)
    financial_mentions: list[str] = Field(default_factory=list)
    social_quality: str | None = Field(default=None, description="fulfilling / draining / neutral")

    # ── Health & Wellness ──
    health: HealthWellness = Field(default_factory=HealthWellness)
