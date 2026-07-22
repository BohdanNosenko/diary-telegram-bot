# Architecture Discussion — Round 3

## Decisions Locked In ✅

| # | Decision | Status |
|---|---|---|
| 1 | Nix + Python on host, Docker only for Telegram Bot API | ✅ Confirmed |
| 2 | GPU mutex | ⏭️ Skip for now, add later (modular) |
| 3 | SessionManager redesign (status, draft, speaker map) | ✅ Confirmed |
| 6 | LiteLLM fallback (Ollama → Gemini) | ✅ Confirmed |
| 8 | Structured logging with `structlog` | ✅ Confirmed |
| 8 | Progress notifications, `/cancel`, session timeout, health check, etc. | ✅ All confirmed |

### Quick Note on Resolution Config

You already have `target_resolution` in `config.toml` with options `"original"`, `"4k"`, `"1080p"`, `"720p"`, `"480p"`, `"240p"`. You mentioned wanting `360p` as well. I'll add that to the `RESOLUTION_MAP`:

```python
RESOLUTION_MAP = {
    "4k": "3840:2160",
    "1080p": "1920:1080",
    "720p": "1280:720",
    "480p": "854:480",
    "360p": "640:360",    # ← new
    "240p": "426:240",
}
```

---

## 4. VRAM — You're Absolutely Right About Offloading

You asked: *"Can't we offload Whisper before starting Ollama?"*

**Yes, 100%.** And this changes everything about which models we can use. The pipeline is strictly sequential (transcribe → then summarize), so we never need both in VRAM simultaneously.

### Sequential VRAM Strategy

```
Phase 1: Transcription + Diarization
┌─────────────────────────────────────────────┐
│  Whisper large-v3        ~3.0 GB            │
│  + pyannote diarization  ~1.5 GB            │
│  ─────────────────────────────────           │
│  Peak: ~4.5 GB                              │
└─────────────────────────────────────────────┘
         │
         ▼  del model; gc.collect(); torch.cuda.empty_cache()
         
Phase 2: LLM Summarization  
┌─────────────────────────────────────────────┐
│  Ollama qwen2.5:14b-q4   ~8.0 GB           │
│  OR qwen2.5:7b-q8        ~7.5 GB           │
│  OR qwen2.5:7b-q4        ~4.5 GB           │
│  ─────────────────────────────────           │
│  Peak: up to ~8.0 GB                       │
└─────────────────────────────────────────────┘
         │
         ▼  Ollama auto-unloads after timeout
```

### Model Upgrade Options

| Combo | Whisper | LLM | Quality | Fits 8GB? |
|---|---|---|---|---|
| **A. Maximum quality** | `large-v3` (3.0 GB) | `qwen2.5:14b-q4_K_M` (~8 GB) | ⭐⭐⭐⭐⭐ | ✅ Tight but works |
| **B. Balanced** | `large-v3` (3.0 GB) | `qwen2.5:7b-q8_0` (~7.5 GB) | ⭐⭐⭐⭐ | ✅ Comfortable |
| **C. Safe** | `large-v3` (3.0 GB) | `qwen2.5:7b-q4_K_M` (~4.5 GB) | ⭐⭐⭐ | ✅ Plenty of room |
| **D. Original spec** | `medium.en` (1.5 GB) | `qwen2.5:7b-q4` (~4.5 GB) | ⭐⭐ | ✅ Conservative |

**My recommendation: Option B.** `large-v3` gives dramatically better transcription (especially for accented speech, background noise, and multi-speaker), and `qwen2.5:7b-q8` gives better structured output than q4 quantization. Option A works too but leaves zero headroom.

> [!TIP]
> The config should let you pick the model independently:
> ```toml
> [transcription]
> model = "large-v3"       # upgraded from medium.en
> 
> [llm]
> provider = "ollama/qwen2.5:7b"  # user picks the model tag with quantization
> ```
> This way you can experiment without code changes.

**Question**: Do you want `large-v3` (multilingual) or `large-v3-en` (English-only, slightly faster)? Depends on whether your diary entries might include other languages.

---

## Diarization Clarification — Faster-Whisper vs. Buzz

You said: *"I've used faster-whisper large-v3 in Buzz and it did mark speakers."*

Here's what's actually happening: **Buzz uses pyannote-audio behind the scenes** on top of faster-whisper. The [Buzz source code](https://github.com/chidiwilliams/buzz) integrates `pyannote.audio`'s speaker diarization pipeline to assign speaker labels to whisper segments. Faster-whisper itself only outputs timestamped text segments — no speaker IDs.

So the good news is: **you've already seen the quality of pyannote diarization and liked it.** We'll replicate exactly that:

```python
# Simplified flow in transcriber.py:

# 1. Transcribe with faster-whisper (GPU)
segments = whisper_model.transcribe(audio_path)

# 2. Diarize with pyannote (GPU) 
diarization = pyannote_pipeline(audio_path)

# 3. Merge: assign speaker labels to whisper segments based on timestamp overlap
labeled_segments = merge_segments_with_speakers(segments, diarization)

# 4. Unload both models
del whisper_model, pyannote_pipeline
gc.collect(); torch.cuda.empty_cache()
```

The output to the LLM would look like:
```
[Speaker 1, 00:00-00:15] So today we're making borscht...
[Speaker 2, 00:15-00:32] You know, your grandmother always said...
[Speaker 1, 00:32-00:45] Right, I remember that...
```

Then during Telegram review, you label: *"Speaker 1 = Me, Speaker 2 = Mom"* → LLM re-generates with real names.

> [!IMPORTANT]
> pyannote requires a HuggingFace token (free, one-time license acceptance). We'll need `HF_TOKEN` in `.env`.

---

## 5 + 7. Output Structure & Template — Brainstorming Session

You said: *"This is a big topic for discussion"* and *"people section seems redundant with tags."*

Let's think about this from **how you'll actually use these notes in Obsidian**. The structure should serve your query patterns.

### How Will You Query/Browse These Notes?

Different Obsidian use patterns drive different schema decisions:

| Use Pattern | What You Need |
|---|---|
| **Calendar/timeline browsing** | `date` in frontmatter, daily notes plugin |
| **"What did I do with Mom?"** | `#people/mom` tag OR `people` frontmatter field |
| **"Show me all cooking entries"** | `#topic/cooking` tag |
| **"How was I feeling last month?"** | `mood` field in frontmatter → Dataview query |
| **"Find that thing I said about beets"** | Full-text search on transcript |
| **Dataview dashboards** | Structured frontmatter fields (queryable) |
| **Graph view connections** | Tags and wikilinks to people/topic pages |

### The Key Tension: Tags vs. Frontmatter Fields

- **Tags** (`#people/mom`): Great for graph view, tag pane, quick filtering. But hard to query with Dataview for complex logic.
- **Frontmatter fields** (`people: [Mom]`): Great for Dataview tables and dashboards. Not visible in graph view by default.
- **Both**: Redundant but covers all use cases.

### Three Schema Options

**Option A: Tags-Only (Minimal)**
```yaml
---
date: 2026-07-20
type: vlog
mood: relaxed
title: Evening cooking session with Mom
tags:
  - journal/vlog
  - people/mom
  - topic/cooking
  - location/home
---
```
- ✅ Simple, no redundancy
- ❌ Can't easily do `WHERE contains(people, "Mom")` in Dataview
- ❌ Mood is a string, not easily aggregated

**Option B: Rich Frontmatter (Dataview-Optimized)**
```yaml
---
date: 2026-07-20
type: vlog
title: Evening cooking session with Mom
mood: relaxed
people:
  - Mom
  - Dima
topics:
  - cooking
  - family
locations:
  - home
duration: "00:05:32"
speakers: 2
llm_model: ollama/qwen2.5:7b
tags:
  - journal/vlog
---
```
- ✅ Rich Dataview queries: `TABLE mood, people WHERE type = "vlog" AND contains(people, "Mom")`
- ✅ Can track metadata like duration, speaker count, which LLM was used
- ❌ More verbose, tags are minimal (just `journal/vlog` for type filtering)
- ❌ LLM has more fields to fill → higher chance of errors

**Option C: Hybrid (My Recommendation)**
```yaml
---
date: 2026-07-20
type: vlog
title: Evening cooking session with Mom
mood: relaxed
people:
  - Mom
duration: "00:05:32"
tags:
  - journal/vlog
  - people/mom
  - topic/cooking
  - location/home
---
```
- ✅ `people` as a structured field for Dataview queries
- ✅ Tags for graph view and browsing
- ✅ `duration` is useful metadata
- ⚠️ `people` and `#people/x` overlap — but serve different query mechanisms
- The tags are auto-generated by the LLM from context; `people` is the authoritative list

### Markdown Body — Two Proposals

**Format 1: Clean & Scannable**
```markdown
# Vlog — 2026-07-20

![[2026-07-20.mp4]]

> **Summary:** Spent the evening cooking borscht with Mom. Discussed weekend plans
> and reminisced about childhood summers.

## Highlights

- Tried roasting beets instead of boiling — turned out great
- Mom shared her grandmother's original recipe adjustments
- Planned a family dinner for next Saturday

## Transcript

> [!NOTE]- Full Transcript
> 
> **Me** *(00:00)*
> So today we're making borscht, Mom's recipe but with a twist...
> 
> **Mom** *(00:15)*
> You know, your grandmother always said the secret is in the beets...
```

Uses a collapsible callout for transcript (Obsidian native, looks clean).

**Format 2: Flat & Simple**
```markdown
# Vlog — 2026-07-20

![[2026-07-20.mp4]]

> **Summary:** Spent the evening cooking borscht with Mom. Discussed weekend plans
> and reminisced about childhood summers.

## Highlights

- Tried roasting beets instead of boiling — turned out great
- Mom shared her grandmother's original recipe adjustments
- Planned a family dinner for next Saturday

---

## Transcript

**Me** *(00:00)*
So today we're making borscht, Mom's recipe but with a twist...

**Mom** *(00:15)*
You know, your grandmother always said the secret is in the beets...
```

No collapsible block — transcript always visible.

---

## Updated NoteSchema Based on Discussion

```python
from pydantic import BaseModel, Field

class TranscriptSegment(BaseModel):
    speaker: str      # "Speaker 1" initially, resolved name after user labeling
    timestamp: str    # "00:15"
    text: str

class NoteSchema(BaseModel):
    title: str = Field(description="Short descriptive title (5-10 words)")
    summary: str = Field(description="2-3 sentence summary of the entry")
    mood: str = Field(description="Free-text mood/emotion descriptor")
    people: list[str] = Field(description="People mentioned or present")
    topics: list[str] = Field(description="Key topics discussed")
    locations: list[str] = Field(description="Locations mentioned") 
    key_highlights: list[str] = Field(description="3-5 bullet point highlights")
    tags: list[str] = Field(description="Hierarchical tags: #journal/vlog, #people/<name>, #topic/<subject>, #location/<city>")
    cleaned_transcript: list[TranscriptSegment]
```

The LLM generates `people`, `topics`, `locations` as structured data → code maps them to both frontmatter fields AND hierarchical tags. This way the LLM doesn't have to understand your tag naming convention — it just lists "Mom, cooking, home" and the code produces `#people/mom`, `#topic/cooking`, `#location/home`.

---

## Decisions Needed

| # | Question | Your call |
|---|---|---|
| 1 | Whisper model: `large-v3` (multilingual) vs `large-v3-en`? | Depends on diary language |
| 2 | LLM model: Option A (14b-q4), B (7b-q8), or C (7b-q4)? | Quality vs. headroom |
| 3 | Frontmatter schema: Option A (tags-only), B (rich), or C (hybrid)? | Query patterns |
| 4 | Transcript display: collapsible callout or always visible? | Personal preference |
| 5 | Do you use Dataview in Obsidian? | Drives schema complexity |
| 6 | Jinja2 templates for markdown? | Flexibility vs. simplicity |
| 7 | `duration` and `llm_model` in frontmatter? | Useful metadata or noise? |
