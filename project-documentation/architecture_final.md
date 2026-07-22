# vlog-journal — Final Architecture Specification

> Compiled from architecture review & 6 rounds of brainstorming (2026-07-22).
> This is the **authoritative reference** for implementation.

---

## 1. Executive Summary

**vlog-journal** is a self-hosted, modular Python application that automates the lifecycle of daily video and audio diaries. It:

1. Ingests video clips, Telegram voice memos, or multi-item sessions via Telegram
2. Normalizes and stitches media with configurable quality presets
3. Transcribes audio locally with speaker diarization (Whisper + pyannote)
4. Uses a local LLM (with cloud fallback) to generate a maximalist structured note — including health tracking, mood, action items, and more
5. Presents an interactive draft in Telegram for review and speaker labeling
6. Saves the final note to an Obsidian vault with rich YAML frontmatter
7. Auto-extracts GPS location, weather, and media metadata
8. Manages encrypted Google Drive backups via Rclone

### Target Hardware

| Component | Spec |
|---|---|
| Host Machine | Acer Nitro 5 — AMD Ryzen CPU + NVIDIA RTX 30-series (8 GB VRAM) |
| OS | Windows 11 + WSL2 (Ubuntu Linux) |
| Dev Shell | Nix Flakes (`flake.nix` via `nix develop`) |
| Python Packages | `uv` (pyproject.toml) |
| Mobile Capture | Samsung Galaxy S24 Ultra + Galaxy Buds 3 Pro |

### Deployment Model

| Component | Runs Where |
|---|---|
| Python application | Directly on WSL2 host (Nix devShell) |
| Telegram Bot API server | Docker container (raises 20 MB → 2 GB file limit) |
| Ollama | WSL2 host (via Nix devShell) |
| Whisper + pyannote | WSL2 host (Python, CUDA) |

---

## 2. Prerequisites & Dependency Management

### 2.1 Dependency Division Matrix

| Component | Manager | Method |
|---|---|---|
| FFmpeg (CLI & codecs) | Nix Flake | `nix develop` devShell |
| Rclone & 7-Zip | Nix Flake | `nix develop` devShell |
| NVIDIA CUDA Drivers | WSL2 Host | Windows NVIDIA Driver + WSL CUDA passthrough |
| Ollama Daemon | Nix Flake | `ollama serve` in devShell |
| Telegram Bot API | Docker Compose | `aiogram/telegram-bot-api` container |
| uv Package Manager | Nix Flake | `nix develop` devShell |
| Faster-Whisper | uv (pyproject.toml) | `faster-whisper` + CTranslate2 |
| pyannote-audio | uv (pyproject.toml) | Speaker diarization pipeline |
| LLM Client | uv (pyproject.toml) | `litellm` + `pydantic` |
| Bot Framework | uv (pyproject.toml) | `pyTelegramBotAPI` |
| Reverse Geocoder | uv (pyproject.toml) | `reverse_geocoder` (offline, ~100 MB data) |
| Markdown Templates | uv (pyproject.toml) | `jinja2` |
| Logging | uv (pyproject.toml) | `structlog` |

### 2.2 Nix Flake DevShell

```nix
{
  description = "vlog-journal developer environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            uv
            ffmpeg-full
            rclone
            p7zip
            ollama
            jq
            git
          ];

          shellHook = ''
            echo "🚀 vlog-journal dev environment loaded!"
            echo "Run 'uv sync' to initialize Python dependencies."
          '';
        };
      }
    );
}
```

### 2.3 Docker Compose (Telegram Bot API Only)

The Python application runs directly on the host. Docker is used **only** for the local Telegram Bot API server (raises upload/download limit from 20 MB to 2 GB).

```yaml
services:
  telegram-bot-api:
    image: aiogram/telegram-bot-api:latest
    container_name: telegram_local_server
    restart: unless-stopped
    environment:
      TELEGRAM_API_ID: "${TELEGRAM_API_ID}"
      TELEGRAM_API_HASH: "${TELEGRAM_API_HASH}"
      TELEGRAM_LOCAL: "true"
    volumes:
      - telegram-bot-data:/var/lib/telegram-bot-api
    ports:
      - "8081:8081"

volumes:
  telegram-bot-data:
```

The Python app and the Telegram Bot API server share the `telegram-bot-data` volume via a bind mount so downloaded files are accessible via local disk paths.

### 2.4 GPU VRAM Strategy — Sequential Offloading (8 GB Limit)

**Critical design principle**: Whisper and Ollama never share VRAM. The pipeline is strictly sequential with explicit memory cleanup between phases.

```
Phase 1: Transcription + Diarization
┌─────────────────────────────────────────────┐
│  Faster-Whisper large-v3     ~3.0 GB        │
│  + pyannote diarization      ~1.5 GB        │
│  ────────────────────────────────            │
│  Peak: ~4.5 GB                              │
└─────────────────────────────────────────────┘
         │
         ▼  del model; gc.collect(); torch.cuda.empty_cache()

Phase 2: LLM Summarization
┌─────────────────────────────────────────────┐
│  Ollama qwen2.5:14b-q3_K_M  ~8.0 GB        │
│  (partial CPU offload if tight)             │
│  ────────────────────────────────            │
│  Peak: up to ~8.0 GB                       │
└─────────────────────────────────────────────┘
         │
         ▼  Ollama auto-unloads after idle timeout
```

**Graceful degradation**: If Ollama's model exceeds available VRAM, it automatically offloads layers to CPU RAM. Inference is slower but output quality is identical. No crashes or data loss.

**Configurable models**: Both Whisper and Ollama models are set in `config.toml` — users can experiment without code changes:

```toml
[transcription]
model = "large-v3"            # Best quality for RU/UK/EN
# model = "large-v3-turbo"    # 4x faster, slightly less accurate

[llm]
provider = "ollama/qwen2.5:14b-q3_K_M"   # Maximum quality
# provider = "ollama/qwen2.5:7b-q6_K"    # Faster, more headroom
```

---

## 3. Mobile Capture & Date Resolution

### 3.1 Earliest Item Date Anchor

When a multi-item session is processed:
- `media.py` inspects all queued items (`.mp4`, `.ogg`, `.wav`, `.mp3`, `.m4a`) for embedded creation timestamps via `ffprobe`.
- The entry date is anchored to `min(creation_times)` — the earliest recorded item.
- **Example**: Clips at 10:30 PM July 20th and 1:15 AM July 21st → entry date = `2026-07-20`.
- **Override**: Users can set date via `/start_session 2026-07-20` or during Telegram review.

### 3.2 Timezone Handling

All timestamps from `ffprobe` are parsed as timezone-aware (UTC). The `datetime.fromtimestamp()` fallback (file modification time) is also converted to timezone-aware using the system's local timezone. This prevents `TypeError` when comparing aware and naive datetimes.

### 3.3 Voice Memo Integration

Voice memos sent to the bot are:
1. Captured via Telegram's voice content type (`.ogg` Opus).
2. Transcribed and diarized alongside video audio.
3. Transcoded to MP3 (128k) for Obsidian storage.
4. Linked via native Obsidian wikilink (`![[YYYY-MM-DD-audio.mp3]]`).

### 3.4 Multi-File Voice Concatenation

When multiple voice memos are sent in a session, they are concatenated using FFmpeg's `concat` filter (not simple multi-input, which only processes the first file):

```python
# Correct: filter_complex concat for multiple audio files
filter_complex = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[outa]"
```

### 3.5 Pro Video Mode Settings

| Setting | Value |
|---|---|
| Resolution & Framerate | HD 720p @ 30 FPS (or 1080p 30 FPS) |
| Dynamic Range | HDR10+ OFF |
| Audio Input | Bluetooth (Galaxy Buds 3 Pro mic) |

---

## 4. Processing Pipeline

### 4.1 Pipeline Architecture

The pipeline is defined in `config.toml` as an ordered list of registered step names. A decorator registry maps step names to async functions. A `PipelineContext` object carries payload data and configuration metadata through the chain.

```
┌─────────────────────────────────────────────────────────┐
│                    Telegram Interface                   │
└────────────────────────────┬────────────────────────────┘
                             │
            ┌────────────────┴────────────────┐
            │ Video / Voice / /finish_session │
            └────────────────┬────────────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │ Session Manager & Media Prep │ ──► Persist State (data/sessions.json)
              │ - Supports Video & Voice     │ ──► Date Anchor (min(creation_time))
              │ - Live Telegram Progress     │ ──► GPS Extraction (ffprobe)
              └──────────────┬───────────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │ Faster-Whisper (CUDA)        │ ──► Raw Timestamped Transcript
              │ + pyannote Diarization       │ ──► Speaker-Labeled Segments
              └──────────────┬───────────────┘
                             │
                             ▼  VRAM cleanup (del model + empty_cache)
                             │
                             ▼
              ┌──────────────────────────────┐
              │ LiteLLM (Ollama → Gemini)    │ ◄── Master Tags (tags.json)
              │ + Pydantic NoteSchema        │ ◄── Caption Context
              └──────────────┬───────────────┘ ◄── Speaker Labels (if provided)
                             │
                             ▼
              ┌──────────────────────────────┐
              │ Auto-Enrichment              │ ──► GPS → Reverse Geocode (offline)
              │ (no LLM needed)             │ ──► Weather (Open-Meteo API)
              └──────────────┬───────────────┘ ──► Media Stats (duration, word count, etc.)
                             │
                             ▼
              ┌──────────────────────────────┐
              │ Jinja2 Markdown Renderer     │ ──► Frontmatter + Body from template
              └──────────────┬───────────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │ Telegram Review Loop         │ ◄── User: [Approve] [Edit] [Cancel]
              │ - Edit note fields           │ ◄── User: "Speaker 1 = Mom"
              │ - Relabel speakers → re-LLM  │ ◄── User: "Set date to yesterday"
              └──────────────┬───────────────┘
                             │ Approved
                             ▼
              ┌──────────────────────────────┐
              │ Obsidian Vault Storage       │ ──► Collision Protection (suffix -02)
              │ + Tag Cache Update           │ ──► Write-through to tags.json
              └──────────────┬───────────────┘ ──► Cleanup temp files
                             │
                             ▼
              ┌──────────────────────────────┐
              │ Rclone Encrypted Backup      │ ──► AES-256 7z Archive
              └──────────────────────────────┘ ──► Upload GDrive & Prune (2D + 1W)
```

### 4.2 Pipeline Steps (config.toml)

```toml
[pipelines]
video_diary = [
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
  "vault.save_entry",
  "vault.update_tag_cache",
  "media.cleanup_temp_files"
]

backup_vault = [
  "vault.create_encrypted_archive",
  "vault.upload_and_prune_remote"
]
```

### 4.3 Pipeline Error Handling

- If any step fails, the bot sends a Telegram alert: *"❌ Pipeline failed at `transcription.whisper_transcribe`: CUDA out of memory"*
- Intermediate results are preserved in `sessions.json` so the user can retry from the failed step via `/retry`.
- The cleanup step only runs on success or explicit discard.

### 4.4 Progress Notifications

Long operations send Telegram updates:

```
📹 Stitching 4 clips...
🎙️ Transcribing audio (this takes ~2 min)...
🔍 Identifying speakers...
🤖 Generating summary...
🗺️ Resolving location & weather...
✅ Draft ready for review!
```

---

## 5. Structured Output — NoteSchema

### 5.1 Language Policy

| Field | Language | Rule |
|---|---|---|
| `title` | English | Always English |
| `summary` | Main spoken language | If >50% Russian → Russian summary |
| `mood`, `energy_level`, `category` | English | Standardized for queries |
| `people` | English transliterations | `Mom` not `Мама`, `Dima` not `Дима` |
| `topics`, `locations_mentioned` | English | Consistent tags |
| `key_highlights` | English | Scannable |
| `action_items`, `questions_raised`, etc. | English | Queryable |
| `health.*` | English | Medical terminology |
| `notable_quotes` | Original spoken language | Preserves actual words |
| `tags` | English | `#people/mom` not `#люди/мама` |
| `cleaned_transcript` | Spoken language(s) | Verbatim, preserving code-switching |

### 5.2 Pydantic Schema

```python
from pydantic import BaseModel, Field
from typing import Literal

# ─── Health Sub-Models ───

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
    speaker: str                     # "Speaker 1" → resolved name after labeling
    timestamp: str                   # "00:15"
    text: str

# ─── Main Schema ───

class NoteSchema(BaseModel):
    # ── Tier 1: Always generated ──
    title: str = Field(description="Short descriptive title in English (5-10 words)")
    summary: str = Field(description="2-3 sentence summary in the main spoken language")
    mood: str = Field(description="Primary mood/emotion, free text, English")
    energy_level: Literal["low", "medium", "high"]
    category: str = Field(description="Entry type: reflection, conversation, adventure, routine, creative, work")
    people: list[str] = Field(description="People mentioned/present, English transliterations")
    topics: list[str] = Field(description="Key topics discussed, English")
    locations_mentioned: list[str] = Field(description="Places mentioned in speech, English")
    key_highlights: list[str] = Field(description="3-5 bullet point highlights, English")
    cleaned_transcript: list[TranscriptSegment]

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
```

### 5.3 Auto-Extracted Fields (No LLM)

These are computed by code and injected into frontmatter directly:

| Field | Source | Example |
|---|---|---|
| `date` | ffprobe creation_time | `2026-07-20` |
| `time` | ffprobe creation_time | `"22:30"` |
| `day_of_week` | computed | `Sunday` |
| `duration` | ffprobe/computed | `"00:05:32"` |
| `media_type` | file extension | `video` / `voice` / `mixed` |
| `clip_count` | session data | `3` |
| `word_count` | transcript length | `847` |
| `speaking_pace_wpm` | word_count / duration | `154` |
| `speakers` | pyannote | `2` |
| `language` | Whisper detection | `"ru"` |
| `languages_detected` | Whisper per-segment | `{ru: 78%, en: 22%}` |
| `confidence` | Whisper avg confidence | `0.89` |
| `recording_device` | ffprobe metadata | `Samsung SM-S928U` |
| `original_resolution` | ffprobe | `1920x1080` |
| `file_size_mb` | computed | `45.2` |
| `primary_location` | reverse_geocoder | `"Brooklyn, New York"` |
| `locations_visited` | GPS per clip + dedup | *(see section 7)* |
| `primary_weather` | Open-Meteo | `"24°C, partly cloudy"` |
| `whisper_model` | config | `large-v3` |
| `llm_model` | runtime | `ollama/qwen2.5:14b-q3_K_M` |
| `llm_fallback_used` | runtime | `false` |
| `processed_at` | system time | `2026-07-21T01:15:00` |
| `entry_version` | auto-increment | `1` |

---

## 6. Obsidian Integration

### 6.1 Vault Structure & Collision Protection

```
PersonalVault/
├── Journal/
│   └── Vlogs/
│       ├── 2026-07-20.md
│       └── 2026-07-21.md
└── Attachments/
    └── Vlogs/
        ├── 2026-07-20.mp4
        └── 2026-07-21-audio.mp3
```

**Collision protection**: If `YYYY-MM-DD.md` already exists, the system appends `-02`, `-03`, etc. (e.g., `2026-07-21-02.md` and `2026-07-21-02.mp4`). The Jinja2 template is aware of the collision suffix to generate correct wikilinks.

### 6.2 Maximalist Frontmatter Example

All health, GPS, weather, and system metadata lives **exclusively in YAML frontmatter** — hidden in Obsidian's reading mode, queryable via Dataview.

```yaml
---
# === Core ===
date: 2026-07-20
time: "22:30"
day_of_week: Sunday
type: vlog
media_type: video
title: Evening cooking session with Mom

# === LLM-Generated (Tier 1) ===
mood: relaxed
energy_level: medium
category: conversation
summary: >
  Провёл вечер готовя борщ с мамой. Обсудили планы на выходные
  и вспоминали летние каникулы из детства.
people:
  - Mom
topics:
  - cooking
  - family
  - recipes
locations_mentioned:
  - grandmother's house
  - farmer's market
key_highlights:
  - Tried roasting beets instead of boiling — turned out great
  - Mom shared her grandmother's original recipe adjustments
  - Planned a family dinner for next Saturday

# === LLM-Generated (Tier 2) ===
action_items:
  - Buy beets at farmer's market Saturday
  - Call brother about Saturday dinner
questions_raised:
  - Should I try adding dill next time?
gratitude:
  - Mom sharing grandmother's recipe
concerns: []
notable_quotes:
  - "Секрет всегда в свёкле" — Мама
media_mentioned: []
food_and_drink:
  - borscht
  - rye bread
plans_future:
  - Family dinner next Saturday
references_past:
  - Last week's failed soup attempt
dreams: []
creative_ideas: []
learning: []
financial_mentions: []
social_quality: fulfilling

# === Health & Wellness ===
health:
  sleep:
    quality: good
    hours: 7.5
    notes: null
  exercise:
    - activity: gym
      duration: "1h 15min"
      intensity: heavy
      notes: "New PR on deadlift — 140 kg"
  pain_or_discomfort:
    - location: right shoulder
      severity: mild
      notes: "Still sore from Monday"
  symptoms: []
  medications: []
  mental_state:
    stress_level: low
    notes: null
  nutrition:
    - protein shake post-workout
    - big dinner — borscht with rye bread
  substances:
    - 2 cups of coffee
  body_metrics: {}

# === Location & Weather ===
primary_location: "Brooklyn, New York"
primary_weather: "24°C, partly cloudy"
locations_visited:
  - name: "Brooklyn, New York"
    gps: [40.7128, -74.0060]
    weather: "24°C, partly cloudy"
    clips: [1, 2, 3]
    time_range: "22:30–23:02"

# === Media Stats ===
duration: "00:05:32"
clip_count: 3
word_count: 847
speakers: 2
speaking_pace_wpm: 154
language: ru
languages_detected:
  ru: 78
  en: 22
confidence: 0.89
recording_device: "Samsung SM-S928U"
original_resolution: "1920x1080"
file_size_mb: 45.2

# === System ===
whisper_model: large-v3
llm_model: "ollama/qwen2.5:14b-q3_K_M"
llm_fallback_used: false
processed_at: "2026-07-21T01:15:00"
entry_version: 1

# === Tags ===
tags:
  - journal/vlog
  - people/mom
  - topic/cooking
  - topic/family
  - topic/recipes
  - location/brooklyn
  - category/conversation
---
```

### 6.3 Markdown Body Template (Jinja2)

Template file: `templates/vlog_note.md.j2`

```markdown
# Vlog — {{ entry_date }}

![[{{ media_filename }}]]

> {{ summary }}

## Highlights

{% for highlight in key_highlights -%}
- {{ highlight }}
{% endfor %}

{% if action_items -%}
## Action Items

{% for item in action_items -%}
- [ ] {{ item }}
{% endfor %}
{% endif -%}

{% if notable_quotes -%}
## Notable Quotes

{% for quote in notable_quotes -%}
> {{ quote }}
{% endfor %}
{% endif -%}

## Transcript

> [!NOTE]- Full Transcript ({{ word_count }} words, {{ duration }})
>
{% for seg in cleaned_transcript -%}
> **{{ seg.speaker }}** *({{ seg.timestamp }})*
> {{ seg.text }}
>
{% endfor %}
```

**Note**: The collapsible `> [!NOTE]-` callout is the default. If the user prefers always-visible transcript, the template is modified to use a plain `## Transcript` section without the callout wrapper.

### 6.4 Dynamic Tag Manager

| Feature | Behavior |
|---|---|
| **Local Cache** (`data/tags.json`) | Sorted list of unique vault tags for instant LLM prompt construction |
| **Write-Through** | New tags appended to `tags.json` when a note is approved and saved |
| **Reconciliation** (`/sync_tags`) | On startup + manual command: scans ALL `.md` files under `vault_path`, parses frontmatter, updates `tags.json`, prunes deleted tags |
| **Tag Generation** | LLM outputs `people`, `topics`, `locations_mentioned` → code generates hierarchical tags (`#people/mom`, `#topic/cooking`, `#location/brooklyn`) |

### 6.5 Tag Namespaces

| Namespace | Source | Example |
|---|---|---|
| `journal/vlog` | Hardcoded | Always present |
| `people/<name>` | LLM `people` field | `#people/mom` |
| `topic/<subject>` | LLM `topics` field | `#topic/cooking` |
| `location/<city>` | Reverse geocoder | `#location/brooklyn` |
| `category/<type>` | LLM `category` field | `#category/conversation` |

---

## 7. GPS, Location & Weather

### 7.1 GPS Extraction

Samsung S24 Ultra embeds GPS coordinates in video metadata. Extracted via `ffprobe`:

```bash
ffprobe -v quiet -print_format json -show_entries format_tags=location video.mp4
```

### 7.2 Multi-Location Handling

For sessions with clips from different places:

1. Extract GPS from **every clip** in the session.
2. Deduplicate by proximity (~500 m radius → same location).
3. Resolve each unique GPS coordinate to a city name via `reverse_geocoder` (offline, instant).
4. Set `primary_location` to the location with the most clips or longest duration.
5. Store all locations with clip indices and time ranges.

### 7.3 Weather Integration

Weather is fetched from **Open-Meteo** (free, no API key) using GPS coordinates + date:

```python
url = (
    f"https://api.open-meteo.com/v1/forecast"
    f"?latitude={lat}&longitude={lon}"
    f"&daily=temperature_2m_max,temperature_2m_min,weathercode"
    f"&timezone=auto&start_date={date}&end_date={date}"
)
```

Weather is stored per-location in `locations_visited` and as a top-level `primary_weather` shortcut for Dataview queries.

---

## 8. Session Management

### 8.1 Session Schema

```python
# data/sessions.json
{
  "123456789": {
    "status": "collecting",         # collecting | processing | draft_pending | approved
    "clips": [
      {"path": "/tmp/abc.mp4", "type": "video", "caption": "With Mom"},
      {"path": "/tmp/def.ogg", "type": "voice", "caption": null}
    ],
    "entry_date": "2026-07-20",     # set after date resolution
    "draft_markdown": "---\n...",   # populated after LLM step
    "note_schema": { ... },         # raw NoteSchema dict
    "speaker_map": {},              # user-provided: {"Speaker 1": "Me", "Speaker 2": "Mom"}
    "pipeline_progress": 5,         # index of last completed step
    "created_at": "2026-07-20T22:30:00",
    "updated_at": "2026-07-20T22:45:00",
    "error": null                   # set if pipeline fails
  }
}
```

### 8.2 Session Lifecycle

| State | Meaning | Actions Available |
|---|---|---|
| `collecting` | Accepting clips & voice memos | Send media, `/finish_session`, `/cancel` |
| `processing` | Pipeline running | Wait (progress notifications sent) |
| `draft_pending` | Review draft in Telegram | `[Approve]`, `[Edit]`, `[Cancel]`, speaker labeling |
| `approved` | Note saved to vault | Entry complete |

### 8.3 Crash Recovery

On startup, the bot reloads `sessions.json` and:
- **`collecting`**: Sends reminder — *"You have N unprocessed clips. /finish_session or /cancel?"*
- **`processing`**: Resumes from `pipeline_progress` (retry from failed step).
- **`draft_pending`**: Re-sends the draft with inline keyboard for approval.

### 8.4 Session Timeout

If a session remains in `collecting` state for >12 hours (configurable), the bot sends a reminder:
*"⏰ You have 3 unprocessed clips from 10 hours ago. /finish_session or /cancel?"*

---

## 9. Bot Commands & UX

### 9.1 Telegram Commands

| Command | Description |
|---|---|
| `/start` | Welcome message with bot capabilities (standard Telegram convention) |
| `/start_session [YYYY-MM-DD]` | Start a new session (optional date override) |
| `/finish_session` | Finalize session and trigger pipeline |
| `/cancel` | Discard active session and all queued clips |
| `/retry` | Retry pipeline from the failed step |
| `/retry full` | Re-run entire pipeline from scratch |
| `/backup` | Trigger manual encrypted backup |
| `/sync_tags` | Reconcile `tags.json` with vault contents |
| `/status` | System health check (Ollama, GPU VRAM, disk space, Rclone) |
| `/help` | List available commands |

### 9.2 Inline Interaction (During Review)

- **`[✅ Approve]`** — Save note to vault, trigger tag update & cleanup.
- **`[✏️ Edit]`** — Opens sub-menu:
  - *"Set date to YYYY-MM-DD"* — Change entry date.
  - *"Speaker 1 = Mom"* — Label speakers → re-triggers LLM with resolved names.
  - Free-text edit instructions → re-triggers LLM with user corrections.
- **`[❌ Discard]`** — Cancel and delete all temp files.

### 9.3 Error Notifications

If any pipeline step fails, the bot sends a Telegram alert:
```
❌ Pipeline failed at step: transcription.whisper_transcribe
Error: CUDA out of memory. Try closing other GPU applications.
Use /retry to retry from this step.
```

### 9.4 Middleware: User ID Whitelist

`middleware.py` checks every incoming message against `ALLOWED_USER_IDS` from `.env`. Unauthorized users receive no response.

---

## 10. LLM Configuration & Fallback

### 10.1 LiteLLM with Automatic Fallback

Primary: local Ollama. If Ollama fails or times out, LiteLLM retries with Gemini:

```python
response = await litellm.acompletion(
    model="ollama/qwen2.5:14b-q3_K_M",
    messages=messages,
    response_format=NoteSchema,
    timeout=120,
    fallbacks=["gemini/gemini-2.5-flash"],
)
```

When fallback fires, the bot notifies via Telegram: *"⚠️ Ollama unavailable, used Gemini for this entry."*

The `llm_fallback_used` field in frontmatter records whether cloud was used.

### 10.2 Config

```toml
[llm]
provider = "ollama/qwen2.5:14b-q3_K_M"
api_base = "http://localhost:11434"
temperature = 0.3
timeout = 120

[llm.fallback]
provider = "gemini/gemini-2.5-flash"
temperature = 0.3
```

### 10.3 Cloud Migration Path

To switch to full cloud LLM, change only the config:

```toml
[llm]
provider = "gemini/gemini-2.5-flash"
# api_base not needed — uses GEMINI_API_KEY from .env
```

Everything else (Pydantic schema, pipeline, templates) remains identical.

---

## 11. Encrypted Backup & Retention

### 11.1 Trigger

- **Automatic**: 4:00 AM daily cron schedule (via APScheduler).
- **Manual**: `/backup` Telegram command.

### 11.2 Process

1. **Archive**: Compress and encrypt `vault_path` into AES-256 7z file using `py7zr` (pure Python) with `BACKUP_ENCRYPTION_PASSPHRASE`.
   - Passphrase stays in Python process memory — never exposed in process arguments or `/proc`.
   - *Note: Standard `p7zip`'s `-si` flag reads archive data from stdin, not the password. `py7zr` avoids this issue entirely.*
2. **Upload**: `rclone copy` to Google Drive using `RCLONE_CONFIG_*` env vars.
3. **Prune**: Retention policy — 2 daily + 1 weekly (Sunday).
   - Deletion subprocesses properly awaited (`proc.wait()`).
4. **Cleanup**: Local archive deleted after successful upload.

### 11.3 Config

```toml
[backup]
enabled = true
schedule_cron = "0 4 * * *"
remote_name = "gdrive"
remote_folder = "vlog-journal-backups"
retention_daily_days = 2
retention_weekly_weeks = 1
```

---

## 12. Logging

Structured logging via `structlog` throughout all pipeline steps:

```
[2026-07-20 22:30:01] INFO  pipeline.step_start  step=media.prepare_and_stitch  chat_id=123456  clips=3
[2026-07-20 22:30:15] INFO  pipeline.step_done   step=media.prepare_and_stitch  duration=14.2s
[2026-07-20 22:30:15] INFO  pipeline.step_start  step=transcription.whisper_transcribe  chat_id=123456
[2026-07-20 22:31:45] INFO  vram.cleanup         freed_mb=3072
[2026-07-20 22:31:46] INFO  pipeline.step_start  step=llm.structure_transcript  model=ollama/qwen2.5:14b-q3_K_M
[2026-07-20 22:32:10] WARN  llm.fallback         reason=timeout  fallback_model=gemini/gemini-2.5-flash
```

---

## 13. Directory Layout

```
vlog-journal/
├── flake.nix                  # Nix Flake dev environment spec
├── flake.lock                 # Nix dependency lockfile
├── config.example.toml        # Template configuration file
├── .env.example               # Secrets template
├── docker-compose.yml         # Telegram Bot API server only
├── pyproject.toml             # uv package build spec
├── uv.lock                    # Universal lockfile
├── README.md
├── .envrc                     # direnv integration ("use flake")
├── templates/
│   ├── vlog_note.md.j2        # Jinja2 template for vlog entries
│   └── voice_note.md.j2       # Jinja2 template for voice-only entries
├── src/
│   └── vlog_journal/
│       ├── __init__.py
│       ├── cli.py             # App entrypoint CLI
│       ├── config.py          # Pydantic Settings & TOML loader
│       ├── logging.py         # structlog configuration
│       ├── bot/
│       │   ├── __init__.py
│       │   ├── app.py         # AsyncTeleBot engine & state reloader
│       │   ├── state.py       # SessionManager (data/sessions.json)
│       │   ├── handlers.py    # Message & callback handlers
│       │   ├── review.py      # Review message builder & inline callbacks
│       │   └── middleware.py   # User ID whitelist guard
│       ├── pipeline/
│       │   ├── __init__.py
│       │   ├── registry.py    # @register_step decorator & PipelineContext
│       │   └── runner.py      # TOML pipeline interpreter & error handler
│       ├── processors/
│       │   ├── __init__.py
│       │   ├── media.py       # Video/audio transcode, stitch & concat
│       │   ├── transcriber.py # Faster-Whisper + VRAM manager
│       │   ├── diarizer.py    # pyannote speaker diarization & segment merger
│       │   ├── schemas.py     # Pydantic NoteSchema & health sub-models
│       │   └── llm.py         # LiteLLM + language-aware prompts
│       ├── enrichment/
│       │   ├── __init__.py
│       │   ├── gps.py         # GPS extraction & reverse geocoding
│       │   ├── weather.py     # Open-Meteo weather fetcher
│       │   └── stats.py       # Duration, word count, speaking pace, etc.
│       └── vault/
│           ├── __init__.py
│           ├── renderer.py    # Jinja2 markdown builder
│           ├── tags.py        # TagManager & tags.json cache reconciler
│           ├── backup.py      # Encrypted Rclone archive & retention
│           └── storage.py     # File mover, collision resolver & vault writer
├── data/
│   ├── sessions.json          # Persistent session state (auto-created)
│   └── tags.json              # Tag cache (auto-created)
└── tests/
    ├── conftest.py            # Shared pytest fixtures
    ├── test_config.py
    ├── test_pipeline.py
    ├── test_state.py
    ├── test_media.py
    ├── test_transcriber.py
    ├── test_llm.py
    ├── test_enrichment.py
    ├── test_vault.py
    ├── test_backup.py
    └── test_integration.py
```

---

## 14. Configuration Specifications

### 14.1 pyproject.toml

```toml
[project]
name = "vlog-journal"
version = "0.1.0"
description = "Automated video/voice diary pipeline with local transcription, speaker diarization, and Obsidian sync"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "pyTelegramBotAPI>=4.20.0",
    "faster-whisper>=1.0.0",
    "pyannote.audio>=3.1.0",
    "pydantic>=2.7.0",
    "pydantic-settings>=2.2.0",
    "litellm>=1.35.0",
    "aiofiles>=23.2.1",
    "torch>=2.2.0",
    "apscheduler>=3.10.0",
    "jinja2>=3.1.0",
    "structlog>=24.1.0",
    "reverse_geocoder>=1.5.1",
    "httpx>=0.27.0",
    "py7zr>=0.22.0",
]

[project.scripts]
vlog-journal = "vlog_journal.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
dev-dependencies = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "ruff>=0.4.0",
]
```

### 14.2 .env.example

```bash
# Telegram Credentials
TELEGRAM_BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_API_ID="YOUR_TELEGRAM_API_ID"
TELEGRAM_API_HASH="YOUR_TELEGRAM_API_HASH"
ALLOWED_USER_IDS="123456789,987654321"
TELEGRAM_LOCAL_API_URL="http://localhost:8081"

# HuggingFace (for pyannote diarization model — one-time license acceptance)
HF_TOKEN="hf_your_token_here"

# Rclone Google Drive Integration
RCLONE_CONFIG_GDRIVE_TYPE="drive"
RCLONE_CONFIG_GDRIVE_SCOPE="drive.file"
RCLONE_CONFIG_GDRIVE_TOKEN='{"access_token":"...","token_type":"Bearer","refresh_token":"...","expiry":"..."}'

# Backup Encryption Passphrase
BACKUP_ENCRYPTION_PASSPHRASE="your-super-secret-passphrase"

# Cloud LLM Keys (Optional — used for fallback)
GEMINI_API_KEY=""
```

### 14.3 config.example.toml

```toml
[app]
vault_name = "PersonalVault"
vault_path = "/home/user/Obsidian/PersonalVault"
vlogs_relative_path = "Journal/Vlogs"
media_relative_path = "Attachments/Vlogs"
tags_cache_file = "data/tags.json"
sessions_state_file = "data/sessions.json"
session_timeout_hours = 12

[media]
target_resolution = "720p"      # "original", "4k", "1080p", "720p", "480p", "360p", "240p"
target_fps = 30                 # 24, 30, 60, or "keep"
video_codec = "libsvtav1"       # "libsvtav1", "hevc_nvenc", "h264"
audio_codec = "libopus"         # "libopus", "aac"
audio_bitrate = "128k"
crf = 32

[transcription]
engine = "faster-whisper"
model = "large-v3"              # "large-v3", "large-v3-turbo", "medium", etc.
language = "auto"               # "auto", "ru", "uk", "en"
device = "cuda"
compute_type = "float16"

[diarization]
enabled = true
min_speakers = 1
max_speakers = 5

[llm]
provider = "ollama/qwen2.5:14b-q3_K_M"
api_base = "http://localhost:11434"
temperature = 0.3
timeout = 120

[llm.fallback]
provider = "gemini/gemini-2.5-flash"
temperature = 0.3

[enrichment]
gps_extraction = true
reverse_geocode = true          # requires reverse_geocoder package
weather_fetch = true            # uses Open-Meteo (free, no API key)
proximity_dedup_meters = 500    # GPS deduplication radius

[backup]
enabled = true
schedule_cron = "0 4 * * *"
remote_name = "gdrive"
remote_folder = "vlog-journal-backups"
retention_daily_days = 2
retention_weekly_weeks = 1

[pipelines]
video_diary = [
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
  "vault.save_entry",
  "vault.update_tag_cache",
  "media.cleanup_temp_files"
]

backup_vault = [
  "vault.create_encrypted_archive",
  "vault.upload_and_prune_remote"
]
```

---

## 15. Deployment & Execution

```bash
# 1. Enter dev environment
nix develop

# 2. Start background services
ollama serve &
docker compose up -d

# 3. Pull the LLM model (first time only)
ollama pull qwen2.5:14b-q3_K_M

# 4. Initialize Python environment
uv sync

# 5. Launch the bot
uv run vlog-journal --config config.toml
```

---

## 16. Bugs Fixed from Original Spec

| # | Issue | Fix |
|---|---|---|
| 1 | Docker GPU passthrough missing | Python app runs on host, not in Docker |
| 2 | Voice concat broken for multi-file | Use `concat` filter, not multi-input |
| 3 | `media.extract_audio` step missing | Added to pipeline with implementation |
| 4 | `transcriber.py` / `llm.py` unimplemented | Full specs defined, code to be implemented |
| 5 | Timezone-aware vs naive datetime mixing | All datetimes forced to timezone-aware |
| 6 | `audio_wav_path` never set for video entries | Set by `media.extract_audio` step |
| 7 | SessionManager too simple | Redesigned with full pipeline state |
| 8 | Backup passphrase visible in `ps` | Passed via stdin |
| 9 | `pyyaml` unused dependency | Removed |
| 10 | Retention pruning not awaited | `proc.wait()` added |
| 11 | No TOML parser in deps | Using `tomllib` (Python 3.11+ stdlib) |
| 12 | Collision suffix breaks wikilinks | Jinja2 template receives resolved filename |
| 13 | `docker-compose.yml` version deprecated | Removed `version` key |
