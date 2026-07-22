# Architecture Discussion — Round 4

## VRAM Risk Analysis: Option A Deep Dive

You asked: *"What are the risks if we're slightly short on VRAM?"*

### The Actual Numbers

I need to be more precise than my earlier table. Model **file size** ≠ **runtime VRAM**. Ollama needs extra memory for the KV cache (stores conversation context during inference):

| Model | Weights on disk | VRAM (weights loaded) | VRAM (+ KV cache, 4K ctx) | Fits 8 GB? |
|---|---|---|---|---|
| `qwen2.5:14b-q4_K_M` | ~8.1 GB | ~8.3 GB | **~9.5–10 GB** | ❌ No |
| `qwen2.5:14b-q4_0` | ~7.6 GB | ~7.8 GB | **~9.0 GB** | ❌ No |
| `qwen2.5:14b-q3_K_M` | ~6.6 GB | ~6.8 GB | **~8.0 GB** | ⚠️ Barely |
| `qwen2.5:14b-IQ4_XS` | ~7.2 GB | ~7.4 GB | **~8.5 GB** | ⚠️ Tight |
| `qwen2.5:7b-q8_0` | ~7.7 GB | ~7.9 GB | **~8.5 GB** | ⚠️ Tight |
| `qwen2.5:7b-q6_K` | ~6.0 GB | ~6.2 GB | **~7.0 GB** | ✅ Yes |
| `qwen2.5:7b-q4_K_M` | ~4.4 GB | ~4.6 GB | **~5.5 GB** | ✅ Comfortable |

Also: WSL2 + CUDA runtime itself reserves ~300–500 MB of your 8 GB, leaving **~7.5–7.7 GB usable**.

### What Happens When You're Short

Here's the good news — **it doesn't crash**. Ollama handles this gracefully:

1. **Partial GPU offload**: Ollama automatically splits model layers between GPU and CPU RAM. If only 7 GB of VRAM is available for a 9 GB model, it loads ~75% of layers on GPU and ~25% on CPU.
2. **Performance impact**: Each CPU-offloaded layer adds latency. Rough estimates:
   - 100% GPU: ~15 tokens/sec for 14b-q4
   - 75% GPU / 25% CPU: ~8–10 tokens/sec
   - 50% GPU / 50% CPU: ~4–6 tokens/sec
3. **No data loss**: The output quality is identical regardless of offloading. It's just slower.
4. **You can control it**: `OLLAMA_NUM_GPU` env var or `num_gpu` parameter lets you set how many layers to put on GPU.

### Revised Recommendation

Given your use case (running in the evening, no background load, you don't mind waiting an extra 30 seconds):

**Option A★ (Revised Maximum Quality)**:
```
Phase 1: Whisper large-v3 (3.0 GB) + pyannote (1.5 GB) = ~4.5 GB peak ✅
Phase 2: qwen2.5:14b-q3_K_M (~8.0 GB) = fits tightly, or partially offloads
```

This gives you 14B parameter quality with negligible quality loss from q3 vs q4 quantization. If it partially offloads a few layers to CPU, you're looking at maybe 20-30 seconds for summary generation instead of 15 — totally fine for an evening diary workflow.

**Alternatively**, keep Option B as a safe default and make the model configurable:

```toml
[llm]
provider = "ollama/qwen2.5:14b-q3_K_M"  # try this first
# provider = "ollama/qwen2.5:7b-q8_0"   # fallback if 14b is too slow
```

You can experiment and find the sweet spot. The config-driven approach means zero code changes.

> [!TIP]
> Run `ollama run qwen2.5:14b-q3_K_M` manually once to see how it feels on your hardware. If the speed is acceptable, lock it in. If not, drop to 7b-q6_K which will fly.

---

## The Time Capsule — Maximum Metadata Brainstorm

You said: *"The more the better! I might want to do weird research on these memos later."*

Love this philosophy. Let's think about every piece of data we can capture, organized by **source** — whether it comes from the media file, the transcription, the LLM, or computed by code.

### Layer 1: Auto-Extractable (Code Only, No LLM)

These come from `ffprobe`, file metadata, and GPS — **free, deterministic, always accurate**:

| Field | Source | Example | Why It's Useful |
|---|---|---|---|
| `date` | ffprobe creation_time | `2026-07-20` | Core anchor |
| `time` | ffprobe creation_time | `22:30` | "What do I do at 10 PM?" patterns |
| `day_of_week` | computed from date | `Sunday` | Weekend vs. weekday patterns |
| `duration` | ffprobe/computed | `"00:05:32"` | "Am I journaling more or less?" |
| `media_type` | file extension | `video` / `voice` / `mixed` | Filter by type |
| `clip_count` | session data | `3` | Session complexity |
| `recording_device` | ffprobe metadata | `Samsung SM-S928U` | Device tracking |
| `original_resolution` | ffprobe | `1920x1080` | Quality record |
| `file_size_mb` | computed | `45.2` | Storage tracking |
| **`gps_lat`** | ffprobe/EXIF | `40.7128` | Raw coordinates |
| **`gps_lon`** | ffprobe/EXIF | `-74.0060` | Raw coordinates |
| **`location_name`** | reverse geocode | `Brooklyn, New York` | Human-readable place |
| `word_count` | computed from transcript | `847` | Verbosity tracking |
| `processing_date` | system time | `2026-07-21` | When note was generated |
| `whisper_model` | config | `large-v3` | Reproducibility |
| `llm_model` | config/runtime | `ollama/qwen2.5:14b-q3_K_M` | Track which model made the summary |
| `llm_fallback_used` | runtime | `false` | Know if cloud was used |
| `confidence_avg` | Whisper output | `0.89` | Transcription reliability |
| `speakers_detected` | pyannote | `2` | Speaker count |
| `language_detected` | Whisper output | `en` | Language tracking |

#### GPS Deep Dive 🗺️

Your Samsung S24 Ultra embeds GPS coordinates in video metadata. We can extract them via `ffprobe`:

```bash
ffprobe -v quiet -print_format json -show_entries format_tags=location video.mp4
# Returns: "+40.7128-074.0060/"
```

For reverse geocoding (coordinates → "Brooklyn, New York"), we have options:

| Method | Pros | Cons |
|---|---|---|
| **Nominatim (OpenStreetMap)** | Free, no API key, self-hostable | Rate-limited (1 req/sec) |
| **Offline `reverse_geocoder`** | No network needed, instant | Python package, ~100 MB data file, city-level only |
| **Google Maps API** | Most accurate, venue names | Costs money, needs API key |

**My recommendation**: Use the `reverse_geocoder` Python package for offline city/country resolution. It's instant, no API key, and gives you city-level accuracy which is enough for diary entries. Add it to `pyproject.toml`:

```toml
"reverse_geocoder>=1.5.1",
```

If you're at home, it'll say "Dallas, Texas" (or wherever). If you're traveling, it captures the city automatically. No network needed.

### Layer 2: LLM-Generated (Requires Understanding Content)

These require the LLM to actually understand what was said:

| Field | Example | Why It's Useful |
|---|---|---|
| `title` | `"Evening cooking session with Mom"` | Quick identification |
| `summary` | `"Spent the evening cooking borscht..."` | Scannable overview |
| `mood` | `"relaxed"` | Emotional tracking over time |
| `energy_level` | `"low"` / `"medium"` / `"high"` | Energy patterns |
| `people` | `["Mom", "Dima"]` | Who you spend time with |
| `topics` | `["cooking", "family", "recipes"]` | What you talk about |
| `locations_mentioned` | `["grandmother's house", "farmer's market"]` | Places discussed (not GPS) |
| `key_highlights` | `["Tried roasting beets...", ...]` | Quick recap |
| `action_items` | `["Buy beets at farmer's market Saturday"]` | Things you said you'd do |
| `questions_raised` | `["Should I try adding dill?"]` | Wonderings to revisit |
| `gratitude` | `["Mom sharing family recipe"]` | What you appreciated |
| `concerns` | `["Running low on freezer space"]` | Worries mentioned |
| `references_past` | `["Last week's failed soup attempt"]` | Links to history |
| `plans_future` | `["Family dinner next Saturday"]` | Forward-looking intentions |
| `notable_quotes` | `["The secret is in the beets" — Mom"]` | Memorable things said |
| `media_mentioned` | `["that YouTube recipe by Joshua Weissman"]` | Books, shows, music, etc. |
| `food_and_drink` | `["borscht", "rye bread"]` | What you ate/cooked |
| `weather_mentioned` | `["hot and humid"]` | Weather context |
| `health_notes` | `["shoulder still sore from gym"]` | Health tracking |
| `category` | `"conversation"` / `"reflection"` / `"adventure"` / `"routine"` | Entry type classification |

### Layer 3: Computed/Derived (Post-Processing)

These could be computed later or in real-time:

| Field | How | Why |
|---|---|---|
| `speaking_pace_wpm` | word_count / duration | Speech pattern tracking |
| `silence_ratio` | Whisper VAD timestamps | How much you pause |
| `sentiment_score` | LLM or simple model | Positive/negative trending |
| `entry_version` | Auto-increment on edits | Edit history |
| `similar_entries` | Embedding search (future) | "Related memories" |

### What's Realistic for the LLM?

Here's the tension: **the more fields we ask the LLM to fill, the higher the chance of hallucination or low-quality output.** A 7B model asked to fill 20 fields might produce garbage on some of them.

**Proposed approach — two tiers:**

**Tier 1 (always generated):** title, summary, mood, people, topics, key_highlights, cleaned_transcript
- These are the core fields. The LLM can do these reliably.

**Tier 2 (best-effort, may be empty):** action_items, questions_raised, gratitude, concerns, notable_quotes, food_and_drink, health_notes, category, energy_level
- The LLM attempts these but returns empty lists if nothing relevant was mentioned.
- A 3-minute monologue about work might have no `food_and_drink` or `gratitude` — and that's fine.

**Auto-extracted (always present, no LLM):** date, time, day_of_week, duration, media_type, clip_count, word_count, gps, location_name, speakers_detected, confidence_avg, whisper_model, llm_model, etc.

> [!TIP]
> The auto-extracted fields are always reliable. The LLM fields degrade gracefully (empty lists). This means even if the LLM misses something, you still have a rich metadata record.

---

## Proposed Maximalist NoteSchema

```python
from pydantic import BaseModel, Field

class TranscriptSegment(BaseModel):
    speaker: str
    timestamp: str
    text: str

class NoteSchema(BaseModel):
    """Tier 1 — always generated"""
    title: str = Field(description="Short descriptive title (5-10 words)")
    summary: str = Field(description="2-3 sentence summary")
    mood: str = Field(description="Primary mood/emotion (free text)")
    energy_level: str = Field(description="Energy: 'low', 'medium', or 'high'")
    category: str = Field(description="Entry type: reflection, conversation, adventure, routine, creative, work")
    people: list[str] = Field(description="People mentioned or present")
    topics: list[str] = Field(description="Key topics discussed")
    locations_mentioned: list[str] = Field(description="Places mentioned in speech (not GPS)")
    key_highlights: list[str] = Field(description="3-5 bullet point highlights")
    cleaned_transcript: list[TranscriptSegment]
    
    """Tier 2 — best-effort, may be empty"""
    action_items: list[str] = Field(default_factory=list, description="Things the speaker said they'd do")
    questions_raised: list[str] = Field(default_factory=list, description="Questions or wonderings mentioned")
    gratitude: list[str] = Field(default_factory=list, description="Things appreciated or thanked")
    concerns: list[str] = Field(default_factory=list, description="Worries or problems mentioned")
    notable_quotes: list[str] = Field(default_factory=list, description="Memorable or significant quotes")
    media_mentioned: list[str] = Field(default_factory=list, description="Books, shows, music, videos referenced")
    food_and_drink: list[str] = Field(default_factory=list, description="Food or drinks mentioned")
    health_notes: list[str] = Field(default_factory=list, description="Health, exercise, or wellness mentions")
    plans_future: list[str] = Field(default_factory=list, description="Future plans or intentions stated")
    references_past: list[str] = Field(default_factory=list, description="References to past events")
```

## Proposed Maximalist Frontmatter

```yaml
---
# === Core ===
date: 2026-07-20
time: "22:30"
day_of_week: Sunday
type: vlog
media_type: video            # video | voice | mixed
title: Evening cooking session with Mom

# === LLM-Generated ===
mood: relaxed
energy_level: medium
category: conversation
summary: >
  Spent the evening cooking borscht with Mom. Discussed weekend plans
  and reminisced about childhood summers. Tried a new recipe variation.
people:
  - Mom
topics:
  - cooking
  - family
  - recipes

# === Location ===
gps: [40.7128, -74.0060]
location: "Brooklyn, New York"
locations_mentioned:
  - grandmother's house
  - farmer's market

# === Media Stats ===
duration: "00:05:32"
clip_count: 3
word_count: 847
speakers: 2
speaking_pace_wpm: 154

# === Tier 2 (Best-Effort) ===
action_items:
  - Buy beets at farmer's market Saturday
  - Call brother about Saturday dinner
questions_raised:
  - Should I try adding dill next time?
gratitude:
  - Mom sharing grandmother's recipe
notable_quotes:
  - "The secret is always in the beets" — Mom
food_and_drink:
  - borscht
  - rye bread
health_notes: []
concerns: []
plans_future:
  - Family dinner next Saturday
references_past:
  - Last week's failed soup attempt

# === System ===
whisper_model: large-v3
llm_model: ollama/qwen2.5:14b-q3_K_M
llm_fallback_used: false
confidence: 0.89
processed_at: 2026-07-21T01:15:00
entry_version: 1

# === Tags ===
tags:
  - journal/vlog
  - people/mom
  - topic/cooking
  - topic/family
  - topic/recipes
  - location/brooklyn
  - mood/relaxed
  - category/conversation
---
```

> [!NOTE]
> This is intentionally maximalist. Some entries will have most Tier 2 fields empty — that's expected and fine. The point is the **schema is always there**, so when you do mention a book or have a health note, it's captured and queryable.

---

## Decisions for This Round

| # | Question |
|---|---|
| 1 | VRAM model: Go with `14b-q3_K_M` (tight fit, configurable) or `7b-q6_K` (safe, fast)? |
| 2 | GPS extraction + offline reverse geocoding — include this? |
| 3 | Is the maximalist frontmatter too much, or "yes, all of it"? |
| 4 | Any fields in the brainstorm you want to add or remove? |
| 5 | Tier 2 fields via the same LLM call, or a separate cheaper/faster pass? |
| 6 | `weather_mentioned` — should this be auto-fetched from a weather API based on GPS+date instead of relying on LLM? |
| 7 | `mood` tag namespace (`#mood/relaxed`) — useful or tag pollution? |
