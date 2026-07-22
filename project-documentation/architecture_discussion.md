# Architecture Discussion & Improvement Proposals

Based on your answers to the 13 questions. Each section has my analysis and a concrete proposal where applicable.

---

## 1. Docker vs. Host — Trade-off Analysis

You said: *Python app in a folder, not Docker, but debatable.*

| Factor | Host (WSL direct) | Docker (with GPU passthrough) |
|---|---|---|
| GPU access | ✅ Trivial — CUDA just works | ⚠️ Requires `nvidia-container-toolkit`, `deploy.resources` config |
| Reproducibility | ⚠️ Depends on Nix Flake | ✅ Full isolation, works on any Docker host |
| Dev iteration speed | ✅ Fast — edit & run | ❌ Rebuild image on dependency changes |
| Deployment to another machine | ⚠️ Need Nix + uv setup | ✅ `docker compose up` and done |
| Complexity | ✅ Simple | ❌ GPU passthrough + volume mounts + networking |
| Ollama access | ✅ `localhost:11434` directly | ⚠️ Need `--network host` or explicit networking |

**My recommendation**: **Host for the Python app, Docker only for Telegram Bot API server.** Here's why:

- Your Nix Flake already gives you reproducibility. Adding Docker on top adds complexity for zero gain during development.
- GPU passthrough in Docker on WSL2 is finicky and adds a failure mode.
- You only need Docker for the Telegram Bot API server (the 20MB→2GB limit upgrade). Everything else runs natively.

**Revised `docker-compose.yml`** would shrink to just:

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

> [!IMPORTANT]
> One thing to verify: do you actually need the local Bot API server? Your S24 Ultra at 720p/30fps produces roughly 15-25 MB/minute of video. If your typical diary clips are under 90 seconds each, they might fit under 20MB and you could skip Docker entirely. What's your typical clip length?

---

## 2. Multi-User Concurrency (Small Friend Group)

You said: *Self-hosted, for me and friends to test.*

Since multiple people could submit entries simultaneously, we need a **GPU mutex** — only one transcription/LLM job at a time, others queued:

```python
import asyncio

class GPUScheduler:
    """Ensures only one GPU-heavy operation runs at a time."""
    def __init__(self):
        self._lock = asyncio.Lock()
    
    async def run_exclusive(self, coro):
        async with self._lock:
            return await coro
```

Pipeline steps that touch CUDA (`whisper_transcribe`, `structure_transcript`) would go through this scheduler. Other users get a Telegram message: *"⏳ Your entry is queued — 1 ahead of you."*

**Question**: Do friends get their own Obsidian vault paths, or does everything go into your single vault? If shared vault, do entries get prefixed with the user's name?

---

## 3. Session Persistence — `SessionManager` Redesign

You said: *sessions.json persists draft payloads with `"draft_pending"` status.*

The current `SessionManager` code doesn't support this — it only stores `clips` and `captions`. It needs to be extended to hold the full pipeline state:

```python
# Proposed session schema
{
  "123456789": {
    "status": "collecting",         # collecting | processing | draft_pending | approved
    "clips": [
      {"path": "/tmp/abc.mp4", "type": "video", "caption": "With Mom"},
      {"path": "/tmp/def.ogg", "type": "voice", "caption": null}
    ],
    "entry_date": "2026-07-20",
    "draft_markdown": "---\ntitle: ...",   # populated after LLM step
    "note_schema": { ... },                # raw Pydantic NoteSchema dict
    "speaker_map": {},                     # user-provided label map
    "created_at": "2026-07-20T22:30:00",
    "updated_at": "2026-07-20T22:45:00"
  }
}
```

Key changes:
- **Status field** drives the restart recovery logic (re-send review keyboard for `draft_pending`).
- **Clips become objects** with type + caption paired together (fixes the parallel-list issue I flagged).
- **Speaker map** stored here so the user can label speakers during review and re-trigger LLM.

---

## 4. Speaker Diarization — This Is the Biggest Design Decision

You said: *Whisper identifies Speaker 1 / Speaker 2, then I label them during review.*

**The problem**: Faster-Whisper does **not** do speaker diarization. It produces a single stream of text with timestamps but no speaker labels. To get "Speaker 1" / "Speaker 2", you need a separate diarization model.

### Options

| Approach | VRAM Cost | Accuracy | Complexity |
|---|---|---|---|
| **A. pyannote-audio** diarization pipeline | +1.5–2 GB | High | Medium — separate HuggingFace model, needs auth token |
| **B. WhisperX** (replaces faster-whisper) | Same as Whisper + ~1 GB for diarization | High | Medium — drop-in replacement with built-in diarization |
| **C. LLM inference only** | 0 extra | Low–Medium | Low — LLM guesses from transcript context |
| **D. Simple energy-based VAD** | 0 extra | Low | Low — detect pauses/volume changes, no real speaker ID |

**My recommendation**: **Option B (WhisperX)** — it wraps faster-whisper and adds pyannote diarization in one package. Your VRAM budget becomes:

```
WhisperX medium.en     ~3.0 GB
+ pyannote diarization ~1.5 GB  (loaded briefly, then unloaded)
+ Ollama qwen2.5:7b   ~4.5 GB
─────────────────────────────────
Sequential total:      ~4.5 GB peak (Whisper+pyannote run first, unload, then Ollama)
```

This fits in 8 GB because the operations are **sequential, not concurrent** — your GPU mutex guarantees this.

> [!WARNING]
> pyannote requires accepting a license on HuggingFace and providing a `HF_TOKEN`. This is a one-time setup but needs to be documented.

**Question**: How many speakers do you typically have in a diary entry? Just you (monologue), or regularly 2+ people?

---

## 5. Structured Output — `NoteSchema`

You said: *title, summary, mood, tags, cleaned_transcript*

Here's a concrete Pydantic model with the LLM prompt in mind:

```python
from pydantic import BaseModel, Field
from typing import Literal

class TranscriptSegment(BaseModel):
    speaker: str = Field(description="Speaker label (e.g. 'Speaker 1' or resolved name)")
    text: str = Field(description="What the speaker said")
    timestamp: str | None = Field(default=None, description="Approximate timestamp HH:MM:SS")

class NoteSchema(BaseModel):
    title: str = Field(description="Short descriptive title for the diary entry")
    summary: str = Field(description="2-3 sentence summary of the entry")
    mood: str = Field(description="Primary mood/emotion (e.g. 'reflective', 'energetic', 'tired')")
    tags: list[str] = Field(description="Hierarchical tags: #journal/vlog, #people/<name>, #topic/<subject>, #location/<city>")
    key_highlights: list[str] = Field(description="3-5 bullet point highlights")
    cleaned_transcript: list[TranscriptSegment] = Field(description="Cleaned transcript with speaker labels")
```

**Questions**:
- Should `mood` be free-text or constrained to a fixed set (e.g., `Literal["happy", "reflective", "tired", ...]`)?
- Should `key_highlights` be a separate field or derived from the summary?
- Do you want a `people` field separate from tags? (e.g., `people: ["Mom", "Dima"]` in frontmatter, independent of `#people/mom` tags)

---

## 6. LLM Fallback via LiteLLM

You said: *Ollama primary, Gemini fallback on failure/timeout.*

LiteLLM supports this natively with the `fallbacks` parameter:

```python
response = await litellm.acompletion(
    model="ollama/qwen2.5:7b",
    messages=messages,
    response_format=NoteSchema,  # structured output
    timeout=120,
    fallbacks=["gemini/gemini-2.5-flash"],
)
```

**Suggestion**: Add a `[llm.fallback]` section to `config.toml`:

```toml
[llm]
provider = "ollama/qwen2.5:7b"
api_base = "http://localhost:11434"
temperature = 0.3
timeout = 120

[llm.fallback]
provider = "gemini/gemini-2.5-flash"
temperature = 0.3
```

> [!TIP]
> When fallback fires, the Telegram notification should tell you: *"⚠️ Ollama unavailable, used Gemini for this entry."* — so you know which entries used cloud processing.

---

## 7. Markdown Template — Concrete Proposal

You said: *YAML frontmatter + header + wikilink + summary + highlights + transcript — but debatable.*

Here's a concrete template. Tell me what to change:

```markdown
---
date: 2026-07-20
type: vlog
people:
  - Mom
  - Dima
tags:
  - journal/vlog
  - people/mom
  - topic/cooking
  - location/home
mood: relaxed
title: Evening cooking session with Mom
---

# Vlog — 2026-07-20

![[2026-07-20.mp4]]

> **Summary:** Spent the evening cooking borscht with Mom. Discussed weekend plans and reminisced about childhood summers. Tried a new recipe variation with roasted beets.

## Key Highlights

- Tried roasting beets instead of boiling — turned out great
- Mom shared her grandmother's original recipe adjustments
- Planned a family dinner for next Saturday

## Transcript

**Me** *(00:00)*
So today we're making borscht, Mom's recipe but with a twist...

**Mom** *(00:15)*
You know, your grandmother always said the secret is in the beets...
```

**Discussion points**:
- **Transcript formatting**: Full transcript can be long. Should it be in a collapsible `<details>` block to keep notes scannable?
- **People in frontmatter vs. tags**: I included both `people: [Mom]` and `#people/mom`. Redundant? Or useful for different Obsidian query patterns (Dataview vs. tag search)?
- **Mood field**: Single word, or should it support multiple? (`mood: [relaxed, nostalgic]`)

---

## 9. Additional Improvement Suggestions

### A. Structured Logging

Add `structlog` to dependencies. Every pipeline step logs entry/exit with context:

```
[2026-07-20 22:30:01] INFO  pipeline.step_start  step=media.prepare_and_stitch  chat_id=123456  clips=3
[2026-07-20 22:30:15] INFO  pipeline.step_done   step=media.prepare_and_stitch  duration=14.2s
[2026-07-20 22:30:15] INFO  pipeline.step_start  step=audio.whisper_transcribe  chat_id=123456
```

This is invaluable when debugging FFmpeg failures or slow LLM responses at 11 PM.

### B. Progress Notifications

Long operations should send Telegram progress updates:

- *"📹 Stitching 4 clips..."* → *"🎙️ Transcribing (this takes ~2 min)..."* → *"🤖 Generating summary..."* → *"✅ Draft ready for review!"*

This keeps the user informed instead of staring at silence for 3-5 minutes.

### C. `/cancel` Command

Users should be able to cancel an active session (`/cancel`) to discard all queued clips and reset state. The current spec has no abort path.

### D. Session Timeout

If a session is in `collecting` state for more than N hours (configurable, say 12h), the bot should either auto-process or send a reminder: *"You have 3 unprocessed clips from 10 hours ago. /finish_session or /cancel?"*

### E. Jinja2 Markdown Templates

Instead of hardcoding the markdown format in Python, use a Jinja2 template file. This lets you tweak the output format without touching code:

```
templates/
  vlog_note.md.j2
  voice_note.md.j2
```

### F. Pipeline Error Recovery

If `whisper_transcribe` fails but `prepare_and_stitch` succeeded, the user shouldn't have to re-upload clips. Store intermediate results so you can retry from the failed step:

```
/retry          # retry from the failed step
/retry full     # re-run entire pipeline
```

### G. Health Check Command

A `/status` Telegram command that reports:
- Ollama model loaded? (check `/api/tags`)
- GPU VRAM usage
- Disk space on vault path
- Rclone connectivity
- Active sessions count

### H. Configurable Whisper Language

The spec hardcodes `medium.en`. For bilingual diaries (e.g., English + Russian/Ukrainian), consider `medium` (multilingual) with a config option:

```toml
[transcription]
model = "medium"       # multilingual
language = "auto"      # or "en", "ru", etc.
```

### I. Entry Edit After Approval

Once a note is saved to the vault, can the user edit it via Telegram? Or is post-save editing done directly in Obsidian? If Telegram-only, you might want an `/edit 2026-07-20` command.

---

## Summary of Decisions Needed

| # | Decision | Options |
|---|---|---|
| 1 | Typical clip length | Under 90s (skip Docker) vs. longer (need local Bot API) |
| 2 | Friends get own vault path? | Shared vault vs. per-user paths |
| 3 | Diarization approach | WhisperX (recommended) vs. pyannote standalone vs. LLM-only |
| 4 | Typical speaker count | Monologue vs. multi-speaker |
| 5 | Mood field type | Free text vs. fixed set vs. list |
| 6 | Separate `people` frontmatter field? | Yes (redundant but useful) vs. tags only |
| 7 | Transcript in collapsible block? | `<details>` vs. always visible |
| 8 | Jinja2 templates? | Yes (flexible) vs. hardcoded (simpler) |
| 9 | Session timeout behavior | Auto-process vs. reminder vs. none |
| 10 | Post-save editing via Telegram? | Yes (`/edit`) vs. Obsidian-only |
| 11 | Multilingual support? | English-only vs. configurable language |
