# Architecture Discussion — Round 5

## Decisions Locked In ✅

- VRAM: Option A (`large-v3` + `14b-q3_K_M`), tweak if issues arise
- Schema: Maximalist time capsule — start with everything, trim later
- GPS: Yes, with offline `reverse_geocoder`
- Cloud path: LiteLLM makes this a config swap (confirmed)

---

## 🗣️ Language Config — Important Change

You said: *"Most vlogs will be in Russian/Ukrainian, occasionally English."*

This changes two things:

### 1. Whisper Model Must Be Multilingual

`large-v3` (not `large-v3-en`) — confirmed. But the config needs a language setting:

```toml
[transcription]
model = "large-v3"          # multilingual, NOT large-v3-en
language = "auto"           # auto-detect per segment
# language = "ru"           # force Russian if auto-detect struggles
```

Whisper's auto-detection works well for Russian and Ukrainian, but there's a nuance: **if you code-switch mid-sentence** (mixing Russian and English), Whisper may struggle with the transition words. Setting `language = "auto"` lets it detect per-segment.

### 2. LLM Must Handle Cyrillic Transcript

The LLM receives a Russian/Ukrainian transcript and must generate:
- **English** structured fields (title, summary, tags) — or should these be in Russian too?
- Speaker labels, mood, highlights — in which language?

**Question**: Do you want the output note (title, summary, highlights) in **English**, **Russian**, or **the same language as the vlog**? This affects the LLM prompt significantly.

Options:
- **A. Always English** — consistent, easy to search, Dataview-friendly
- **B. Always Russian** — natural for a personal diary
- **C. Match source language** — Russian vlog → Russian note, English vlog → English note
- **D. Bilingual** — Russian summary + English summary side by side

### 3. New Auto-Extracted Field: `language`

```yaml
language: "ru"               # or "uk", "en", "mixed"
languages_detected:           # if code-switching
  - ru: 78%
  - en: 22%
```

Whisper returns a language detection confidence per segment — we can aggregate this.

---

## 🗺️ Multi-Location GPS Handling

You asked: *"If videos will be shot in different places, how do we manage multi-places?"*

### Strategy: Extract All, Deduplicate by Proximity, Pick Primary

```python
# For each clip in the session:
clip_locations = [
    {"clip": 1, "lat": 40.7128, "lon": -74.0060, "time": "10:30", "name": "Brooklyn, NY"},
    {"clip": 2, "lat": 40.7128, "lon": -74.0061, "time": "10:45", "name": "Brooklyn, NY"},  # same spot
    {"clip": 3, "lat": 40.7580, "lon": -73.9855, "time": "19:00", "name": "Manhattan, NY"},  # different
]

# Deduplicate within ~500m radius → 2 unique locations
# Primary = location with most clips OR longest total duration
```

### Frontmatter Result

```yaml
# === Location ===
primary_location: "Brooklyn, New York"
locations_visited:
  - name: "Brooklyn, New York"
    gps: [40.7128, -74.0060]
    clips: [1, 2]
    time_range: "10:30–10:45"
  - name: "Manhattan, New York"
    gps: [40.7580, -73.9855]
    clips: [3]
    time_range: "19:00"
```

This gives you:
- **Map building**: Every entry has GPS coordinates you can plot
- **Location frequency**: Dataview query: "How often do I vlog from home vs. other places?"
- **Travel detection**: If locations are >50 km apart, the entry could auto-tag `#travel`
- **Timeline within entry**: You can see where you were at what time

> [!TIP]
> For the map visualization later — Obsidian has a `map-view` community plugin that reads GPS coordinates from frontmatter and plots them on a map. With this schema, it would work out of the box.

---

## 🏥 Health & Wellness — Expanded Schema

You said: *"This can be a great source of info for doc visits — physical/mental state, sleep, activity, energy."*

This is an excellent use case. Let me design a health sub-schema that a doctor would actually find useful:

### Health Fields (All LLM Tier 2 — Best-Effort)

```python
class HealthWellness(BaseModel):
    """Extracted only when mentioned in the vlog. Empty when not discussed."""
    
    sleep: SleepNote | None = Field(default=None, 
        description="Sleep quality/duration if mentioned")
    exercise: list[ExerciseNote] = Field(default_factory=list,
        description="Physical activity if mentioned")
    pain_or_discomfort: list[PainNote] = Field(default_factory=list,
        description="Any pain, soreness, or physical discomfort mentioned")
    symptoms: list[str] = Field(default_factory=list,
        description="Any symptoms mentioned: headache, fatigue, nausea, etc.")
    medications: list[str] = Field(default_factory=list,
        description="Medications or supplements mentioned")
    mental_state: MentalNote | None = Field(default=None,
        description="Mental health observations if mentioned")
    nutrition: list[str] = Field(default_factory=list,
        description="Meals, diet notes, water intake if mentioned")
    substances: list[str] = Field(default_factory=list,
        description="Coffee, alcohol, etc. if mentioned")
    body_metrics: dict[str, str] = Field(default_factory=dict,
        description="Weight, blood pressure, etc. if mentioned")

class SleepNote(BaseModel):
    quality: str | None = None       # "good", "poor", "restless"
    hours: float | None = None       # 7.5
    notes: str | None = None         # "woke up at 3 AM"

class ExerciseNote(BaseModel):
    activity: str                    # "gym", "running", "yoga"
    duration: str | None = None      # "45 min"
    intensity: str | None = None     # "heavy", "light", "moderate"
    notes: str | None = None         # "increased squat weight"

class PainNote(BaseModel):
    location: str                    # "lower back", "right knee"
    severity: str | None = None      # "mild", "moderate", "severe"
    notes: str | None = None         # "worse after sitting"

class MentalNote(BaseModel):
    stress_level: str | None = None  # "low", "moderate", "high"
    anxiety: str | None = None       # noted if mentioned
    notes: str | None = None         # "feeling overwhelmed with deadlines"
```

### Frontmatter Example (Health-Rich Entry)

```yaml
# === Health & Wellness ===
health:
  sleep:
    quality: poor
    hours: 5.5
    notes: "Woke up twice, couldn't fall back asleep after 4 AM"
  exercise:
    - activity: gym
      duration: "1h 15min"
      intensity: heavy
      notes: "New PR on deadlift — 140 kg"
  pain_or_discomfort:
    - location: right shoulder
      severity: mild
      notes: "Still sore from Monday's bench press"
  symptoms:
    - mild headache in the afternoon
  medications:
    - ibuprofen 400mg
  mental_state:
    stress_level: moderate
    notes: "Work deadline pressure but manageable"
  nutrition:
    - skipped breakfast
    - protein shake post-workout
    - big dinner with Mom
  substances:
    - 3 cups of coffee
  body_metrics:
    weight: "82 kg"
```

### Why This Structure Is Doctor-Friendly

Imagine showing your doctor a Dataview dashboard:

```dataview
TABLE health.sleep.quality AS "Sleep", 
      health.sleep.hours AS "Hours",
      health.exercise[0].activity AS "Exercise",
      health.pain_or_discomfort AS "Pain"
WHERE health != null
SORT date DESC
LIMIT 30
```

This produces a 30-day health timeline from your diary entries — sleep patterns, exercise frequency, pain recurrence, medication use. No manual health tracking app needed.

> [!TIP]
> You could even create an Obsidian note `Health Dashboard.md` with Dataview queries that auto-aggregate:
> - Average sleep hours this month
> - Exercise frequency (days/week)
> - Recurring pain locations
> - Medication usage timeline
> - Stress level trend

---

## 🧠 Additional Life-Tracking Fields

Since we're going maximalist, here are more fields that could be valuable for "weird research later":

| Field | Type | Example | Use Case |
|---|---|---|---|
| `dreams` | `list[str]` | `["flying over a city"]` | Dream journaling (morning vlogs) |
| `financial_mentions` | `list[str]` | `["bought new headphones $200"]` | Spending awareness |
| `creative_ideas` | `list[str]` | `["app idea for meal planning"]` | Idea capture |
| `learning` | `list[str]` | `["started Rust tutorial"]` | Skill development tracking |
| `social_quality` | `str` | `"fulfilling"` / `"draining"` / `"neutral"` | Social battery tracking |
| `environment` | `str` | `"indoors"` / `"outdoors"` / `"car"` | Context |
| `weather_actual` | `str` | `"28°C, sunny"` | Auto-fetched from weather API by GPS+date |

### Weather API vs. LLM Mention

For `weather_actual`, we have two options:
- **LLM extraction**: Only if you mention weather ("it's so hot today") — unreliable
- **Auto-fetch**: Use GPS coordinates + date to call a free weather API (Open-Meteo, no key needed) and get actual conditions

**Recommendation**: Auto-fetch from Open-Meteo. It's free, no API key, and gives you objective weather data tied to your location and time. This is especially useful for correlating mood/energy with weather patterns.

```python
# Open-Meteo is free, no API key
url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=temperature_2m_max&timezone=auto&start_date={date}&end_date={date}"
```

---

## ☁️ Cloud Migration Path

You said: *"If it turns out well, I might migrate to full cloud Gemini."*

LiteLLM makes this a one-line config change:

```toml
# Local (current)
[llm]
provider = "ollama/qwen2.5:14b-q3_K_M"
api_base = "http://localhost:11434"

# Cloud (future — just change these two lines)
[llm]
provider = "gemini/gemini-2.5-flash"
# api_base not needed, uses GEMINI_API_KEY from .env
```

Everything else stays identical — same Pydantic schema, same pipeline, same output. The structured output format (`response_format`) works with both Ollama and Gemini through LiteLLM.

> [!NOTE]
> If you go cloud-only, you can skip Ollama entirely and free up all GPU VRAM for a larger Whisper model or even `large-v3-turbo` (faster inference). Though at that point, you might also consider cloud transcription (Gemini can do audio transcription natively).

---

## Updated Complete NoteSchema

```python
from pydantic import BaseModel, Field
from typing import Literal

# === Health Sub-Models ===
class SleepNote(BaseModel):
    quality: str | None = None
    hours: float | None = None
    notes: str | None = None

class ExerciseNote(BaseModel):
    activity: str
    duration: str | None = None
    intensity: str | None = None
    notes: str | None = None

class PainNote(BaseModel):
    location: str
    severity: str | None = None
    notes: str | None = None

class MentalNote(BaseModel):
    stress_level: str | None = None
    anxiety: str | None = None
    notes: str | None = None

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

# === Transcript ===
class TranscriptSegment(BaseModel):
    speaker: str
    timestamp: str
    text: str

# === Main Schema ===
class NoteSchema(BaseModel):
    # --- Tier 1: Always generated ---
    title: str
    summary: str
    mood: str
    energy_level: Literal["low", "medium", "high"]
    category: str  # reflection, conversation, adventure, routine, creative, work
    people: list[str]
    topics: list[str]
    locations_mentioned: list[str]
    key_highlights: list[str]
    cleaned_transcript: list[TranscriptSegment]
    
    # --- Tier 2: Best-effort ---
    action_items: list[str] = Field(default_factory=list)
    questions_raised: list[str] = Field(default_factory=list)
    gratitude: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    notable_quotes: list[str] = Field(default_factory=list)
    media_mentioned: list[str] = Field(default_factory=list)
    food_and_drink: list[str] = Field(default_factory=list)
    plans_future: list[str] = Field(default_factory=list)
    references_past: list[str] = Field(default_factory=list)
    dreams: list[str] = Field(default_factory=list)
    creative_ideas: list[str] = Field(default_factory=list)
    learning: list[str] = Field(default_factory=list)
    financial_mentions: list[str] = Field(default_factory=list)
    social_quality: str | None = None
    
    # --- Health & Wellness ---
    health: HealthWellness = Field(default_factory=HealthWellness)
```

---

## Decisions for This Round

| # | Question |
|---|---|
| 1 | **Output language**: English, Russian, or match-source for title/summary/highlights? |
| 2 | **Weather auto-fetch** from Open-Meteo (free, GPS+date) — include? |
| 3 | Any health fields to add/remove? (`dreams`, `financial_mentions`, `substances` — too personal or valuable?) |
| 4 | Social quality tracking (`fulfilling` / `draining` / `neutral`) — useful? |
| 5 | Are we ready to finalize the schema, or another brainstorm round? |
