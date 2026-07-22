# Architecture Discussion — Round 6

## Whisper Model — You're Right, I Misspoke

You asked: *"Why can't we use large-v3-turbo right away? We're offloading before Ollama."*

**You're absolutely correct.** Since Whisper fully unloads before Ollama loads, VRAM is not shared — you can use **any** Whisper model that fits in 8 GB on its own. My "cloud-only" comment was misleading. Here's the actual picture:

| Whisper Model | VRAM | Speed (5 min audio) | Accuracy (RU/UK) | Available Now? |
|---|---|---|---|---|
| `large-v3` | ~3.0 GB | ~60–90 sec | ⭐⭐⭐⭐⭐ Best | ✅ Yes |
| `large-v3-turbo` | ~1.6 GB | ~15–25 sec | ⭐⭐⭐⭐ Slightly lower | ✅ Yes |
| `large-v2` | ~3.0 GB | ~60–90 sec | ⭐⭐⭐⭐ | ✅ Yes |
| `medium` | ~1.5 GB | ~30–45 sec | ⭐⭐⭐ | ✅ Yes |

All of these fit in 8 GB with room to spare (pyannote adds ~1.5 GB on top).

**Recommendation**: Default to `large-v3` for best Russian/Ukrainian accuracy. Make it configurable so you can switch to `large-v3-turbo` if you want speed on a night with many clips:

```toml
[transcription]
model = "large-v3"            # best quality for RU/UK
# model = "large-v3-turbo"    # 4x faster, slightly less accurate
```

No code changes needed — just a config swap.

---

## 🌐 Language Policy — Finalized

| Field | Language | Rationale |
|---|---|---|
| `title` | English | Consistent search & Dataview |
| `summary` | **Main spoken language** | Natural feel, preserves nuance |
| `mood`, `energy_level`, `category` | English | Standardized for queries |
| `people`, `topics` | English | Consistent tag generation |
| `key_highlights` | English | Scannable |
| `action_items`, `questions_raised`, etc. | English | Queryable |
| `health.*` | English | Medical terminology consistency |
| `notable_quotes` | **Original language** | Preserves the actual words |
| `tags` | English | `#people/mom` not `#люди/мама` |
| `cleaned_transcript` | **Spoken language(s)** | Verbatim — if mixed, then mixed |

### LLM Prompt Implications

The system prompt needs explicit language instructions:

```
You will receive a transcript that may be in Russian, Ukrainian, English, or a mix.

Output rules:
- "summary": Write in the SAME language as the majority of the transcript
- "notable_quotes": Keep in the ORIGINAL spoken language
- "cleaned_transcript": Keep in the ORIGINAL spoken language(s), preserving any code-switching
- ALL OTHER FIELDS: Write in English regardless of transcript language
- For "people": Use English transliterations (e.g., "Mom" not "Мама", "Dima" not "Дима")

If the transcript mixes languages, the summary should be in whichever language
comprises >50% of the spoken content.
```

**One question**: For `people`, should names be transliterated to English (`Dima`, `Mom`) or kept in original (`Дима`, `Мама`)? Transliteration keeps tags clean (`#people/dima` vs `#people/дима`), but original preserves how you actually refer to them.

**My recommendation**: English transliterations. Tags like `#people/дима` break on some systems and are harder to type in search.

---

## 🏥 Health Data Visibility — Frontmatter Only

You asked: *"Can this be hidden? I don't want health to dominate the note."*

**Great instinct.** The answer: **keep health data exclusively in YAML frontmatter.** Here's why this works perfectly:

### How Obsidian Handles Frontmatter

- **Reading mode**: Frontmatter is completely hidden. You see only the note body.
- **Edit/Source mode**: Frontmatter appears as a collapsed YAML block at the top.
- **Dataview queries**: Can access all frontmatter fields programmatically.
- **Properties panel**: Obsidian's built-in Properties view shows frontmatter in a clean sidebar.

So the note body stays clean:

```markdown
---
(50+ lines of rich frontmatter — hidden in reading mode)
---

# Vlog — 2026-07-20

![[2026-07-20.mp4]]

> Провёл вечер готовя борщ с мамой. Обсудили планы на выходные
> и вспоминали летние каникулы из детства.

## Highlights

- Tried roasting beets instead of boiling — turned out great
- Mom shared her grandmother's original recipe adjustments
- Planned a family dinner for next Saturday

## Transcript

> [!NOTE]- Full Transcript
> 
> **Me** *(00:00)*
> Ну что, сегодня готовим борщ, мамин рецепт но с изменениями...
> 
> **Mom** *(00:15)*
> Знаешь, бабушка всегда говорила что секрет в свёкле...
```

Notice:
- **Summary in Russian** (main spoken language) ✅
- **Highlights in English** ✅
- **Transcript in Russian** (spoken language) ✅
- **Health data hidden in frontmatter** — only visible via Dataview or Properties panel ✅

All the health, GPS, weather, system metadata — it's all in the YAML frontmatter block. The note body is just: embed → summary → highlights → transcript. Clean and readable.

---

## 🌤️ Combined Location + Weather

You said: *"Maybe combine weather with the location field?"*

Makes sense — weather is tied to where you were and when:

```yaml
locations_visited:
  - name: "Brooklyn, New York"
    gps: [40.7128, -74.0060]
    weather: "24°C, partly cloudy"
    clips: [1, 2]
    time_range: "10:30–10:45"
  - name: "Manhattan, New York"
    gps: [40.7580, -73.9855]
    weather: "24°C, partly cloudy"
    clips: [3]
    time_range: "19:00"

primary_location: "Brooklyn, New York"
primary_weather: "24°C, partly cloudy"
```

`primary_weather` is a top-level shortcut so Dataview queries stay simple:
```
TABLE primary_location, primary_weather, mood
WHERE type = "vlog"
SORT date DESC
```

Weather is fetched from Open-Meteo using GPS coordinates + date — zero configuration, free, no API key.

---

## Where We Stand — Schema Readiness

I think we're close to finalizing. Here's a summary of everything locked in:

### Confirmed Architecture
- ✅ Nix + Python on host, Docker only for Telegram Bot API
- ✅ Whisper `large-v3` (multilingual) + pyannote diarization → sequential offload → Ollama `14b-q3_K_M`
- ✅ LiteLLM with Ollama→Gemini fallback
- ✅ Maximalist time capsule schema
- ✅ GPS extraction + offline reverse geocoding + weather from Open-Meteo
- ✅ Health & wellness in frontmatter only (hidden in reading mode)
- ✅ Location + weather combined
- ✅ Language policy: English for all structured fields, source language for transcript + summary
- ✅ `structlog` logging, progress notifications, `/cancel`, session timeout, `/status` health check
- ✅ Jinja2 templates for markdown rendering
- ✅ SessionManager redesign with full pipeline state

### Remaining Minor Decisions

| # | Question |
|---|---|
| 1 | **People transliteration**: `Dima` and `Mom` (English) or `Дима` and `Мама` (original)? |
| 2 | **Collapsible transcript**: Use Obsidian's `> [!NOTE]- Full Transcript` callout? Test both later? |
| 3 | Ready to compile the **final architecture spec** and start building? |
