# Architecture Review: vlog-journal

## Overall Assessment

This is a well-thought-out spec — the dependency matrix, VRAM budget, collision protection, and TOML-driven pipeline design all show maturity. Below I've organized my findings into **issues I can identify concretely** and **open questions I need your input on** before we start building.

---

## 🔴 Issues Found

### 1. Docker Compose + GPU Access Conflict

The `vlog-journal-app` container uses `network_mode: "host"` but has **no GPU passthrough configured**. Faster-Whisper and Ollama both need CUDA inside the container. You'd need either:
- A `deploy.resources.reservations.devices` block with `nvidia` capabilities, **or**
- Run the Python app **outside** Docker (on the host/WSL) and only containerize the Telegram Bot API server.

The current `docker-compose.yml` will fail on any CUDA operation. **Which approach do you intend?**

### 2. `network_mode: "host"` vs. Service Discovery

`vlog-journal-app` uses `network_mode: "host"`, which means `depends_on: telegram-bot-api` won't provide DNS resolution for `telegram-bot-api`. The bot API must be reached via `localhost:8081`, which is what the `.env` has — so it works, but `depends_on` only guarantees startup order, not readiness. There's no health check defined on `telegram-bot-api`.

### 3. Voice Memo FFmpeg Concat Is Broken for Multiple Files

In `media.py`, the voice-only branch runs:
```python
cmd = ["ffmpeg", "-y"]
for p in clip_paths: cmd.extend(["-i", str(p)])
cmd.extend(["-c:a", "libmp3lame", "-b:a", "128k", str(output_audio_path)])
```
With multiple inputs, FFmpeg won't automatically concatenate — it'll just use the first input's audio. You need a `-filter_complex` with `concat` (like the video path does) or the concat demuxer. Single-file voice memos work fine, but **multi-file voice sessions will silently drop all but the first file**.

### 4. Missing `media.extract_audio` Pipeline Step

The `video_diary` pipeline in `config.example.toml` references `"media.extract_audio"`, but there is **no implementation or registration** of this step anywhere in the spec. The code jumps from `prepare_and_stitch` directly to `audio.whisper_transcribe`. Either:
- This step needs to be implemented (extract WAV from the stitched MP4 for Whisper), or
- It should be removed from the pipeline and Whisper should consume the MP4 directly.

### 5. Missing `audio.whisper_transcribe` and `llm.structure_transcript` Implementations

These two critical pipeline steps are referenced in config but have no implementation shown. `transcriber.py` and `llm.py` are listed in the directory layout but their code is absent. This may be intentional (spec-only), but it means:
- The VRAM cleanup pattern (`del model; gc.collect(); torch.cuda.empty_cache()`) is described in prose but never shown in code.
- The Pydantic structured output schema for the LLM response is undefined.
- The multi-speaker detection logic is unspecified.

### 6. No `audio_wav_path` Set for Video Entries

The video branch of `prepare_and_stitch_step` never sets `ctx.payload["audio_wav_path"]`. If `media.extract_audio` is the step that does this, it's missing. If Whisper is supposed to read the MP4 directly, the pipeline step name is misleading.

### 7. Pipeline Registry & Context Are Referenced but Never Defined

`registry.py` is supposed to contain the `@register_step` decorator and the `PipelineContext` class, but their implementation is absent. Key unknowns:
- What does `PipelineContext.payload` vs `PipelineContext.metadata` contain?
- How does `runner.py` resolve step names to functions?
- Is there error handling / rollback if a mid-pipeline step fails?

### 8. `SessionManager` Doesn't Distinguish Media Types

`add_clip()` stores everything in a `"clips"` list regardless of whether it's a video or voice memo. There's no `"voice_memos"` key or type discriminator. The `prepare_and_stitch` step infers type from file extension, which works — but captions are stored in a parallel list with no pairing to specific clips. If a user sends 3 videos and 2 voice notes with captions on only some, the caption-to-clip association is lost.

### 9. Backup Passphrase in Process Arguments Is Visible

```python
cmd = ["7z", "a", "-t7z", "-mhe=on", f"-p{passphrase}", ...]
```
The passphrase appears in the process argument list and will be visible in `/proc/*/cmdline` and `ps aux` output on the host. Consider using 7z's stdin passphrase mode (`-si` or environment variable passthrough) instead.

### 10. `pyyaml` Dependency Is Unused

`pyproject.toml` lists `pyyaml>=6.0` but everything uses JSON (`sessions.json`, `tags.json`) and TOML (`config.toml`). Unless there's a hidden use case, this is a dead dependency.

### 11. Retention Pruning Doesn't Await Deletion

```python
await asyncio.create_subprocess_exec("rclone", "deletefile", ...)
```
This creates the subprocess but **never awaits its completion** — the coroutine returned by `create_subprocess_exec` is awaited (which starts the process), but `proc.communicate()` or `proc.wait()` is never called. The deletions may not complete before the function returns.

### 12. No TOML Parser in Dependencies

The config is loaded from `config.toml` via `config.py` (Pydantic Settings & TOML loader), but there's no `tomli` or `tomllib` in `pyproject.toml`. Python 3.11+ has `tomllib` in stdlib, so this might be fine if you're relying on that — but `pydantic-settings` doesn't natively read TOML without a plugin or custom source. Needs clarification.

### 13. Collision Suffix Inconsistency

The spec says collisions produce `2026-07-21-02.md`, but the code produces it via:
```python
file_suffix = f"-{counter:02d}"  # counter starts at 2
```
So the first file is `2026-07-21.md` and the second is `2026-07-21-02.md`. This is fine logically, but the corresponding media file for voice memos gets named `2026-07-21-02-audio.mp3` — the `-audio` suffix stacks with the collision suffix, which could confuse Obsidian embed links in the markdown if the markdown builder isn't aware of the collision suffix.

---

## 🟡 Potential Concerns

### A. Single-User Assumption
The bot uses `chat_id` as the session key and has a whitelist guard, but the spec never explicitly states whether this is single-user or multi-user. With multiple allowed user IDs, concurrent sessions could work — but VRAM is shared. Two users triggering transcription simultaneously would OOM.

### B. No Rate Limiting or Queue for GPU-Heavy Operations
If multiple entries are submitted in quick succession (or via scheduled backup + live transcription), there's no mutex or queue protecting CUDA resources. This ties into concern A.

### C. Timezone Handling
`get_item_creation_date` parses UTC timestamps from `ffprobe` but `datetime.fromtimestamp()` (fallback) uses local time. The `min()` comparison could compare timezone-aware and timezone-naive datetimes, which raises a `TypeError` in Python. The entry date anchor needs consistent timezone handling.

### D. No Logging Framework
The spec has no mention of logging. For a pipeline with FFmpeg, Whisper, LLM, Rclone, and Telegram API calls, structured logging (`structlog` or stdlib `logging`) would be essential for debugging.

### E. `docker-compose.yml` Version Key Is Deprecated
`version: '3.8'` is [deprecated in modern Docker Compose](https://docs.docker.com/compose/compose-file/04-version-and-name/) and can be removed.

---

## 🟢 Questions for You

### Architecture & Intent

1. **Docker vs. Host for the Python app**: Do you intend the Python app to run inside Docker (needs GPU passthrough config) or directly on WSL (only Telegram Bot API in Docker)?

2. **Is this single-user only?** The whitelist allows multiple IDs — should the system support concurrent sessions from different users, or is the second ID just a backup/admin?

3. **What happens to the Telegram review step if the bot restarts mid-review?** Sessions are persisted, but is the draft markdown also persisted? Can the user resume reviewing an already-generated draft?

### Transcription & LLM

4. **Multi-speaker detection**: You mention "multi-speaker support" — is this Whisper diarization (speaker labels), or just the LLM inferring speakers from context/captions? Faster-Whisper doesn't do diarization natively; you'd need `pyannote` or similar.

5. **What's the structured output schema?** You mention Pydantic validation for LLM output. What fields does a diary entry have? (e.g., title, summary, mood, topics, people mentioned, tags?)

6. **Cloud LLM fallback**: If Ollama is down or the model isn't loaded, should the system automatically fall back to Gemini, or just error out?

### Obsidian & Content

7. **Markdown template**: What should the final `.md` note look like? Is there a specific frontmatter schema (YAML front matter with tags, date, speakers, etc.)?

8. **Media embedding**: Should the note embed the video/audio inline (`![[file.mp4]]`) or just link to it? Should there be a thumbnail?

9. **Tag hierarchy**: You mention "hierarchical tagging" — what does the hierarchy look like? (e.g., `#health/exercise`, `#work/project-x`?) Are there fixed top-level categories?

### Operations

10. **Backup trigger**: Should backup run automatically after every approved entry, only on the cron schedule, or both?

11. **Error notifications**: If a backup fails or FFmpeg crashes, should the bot notify the user via Telegram?

12. **Existing vault content**: Will this be used on an empty vault, or do you have existing diary entries? If existing, does `/sync_tags` need to handle non-vlog markdown files too?

13. **`config.toml` vs `.env` split rationale**: Secrets are in `.env` and settings in TOML — but `ALLOWED_USER_IDS` feels more like config than a secret. Is the split intentional, or should some things move?

---

## Summary

| Category | Count |
|---|---|
| 🔴 Concrete bugs/issues | 13 |
| 🟡 Design concerns | 5 |
| 🟢 Open questions | 13 |

The biggest blockers before building are **#1** (Docker GPU), **#3** (broken voice concat), **#4** (missing pipeline step), and **#5** (missing core implementations). The open questions, especially around the LLM output schema and markdown template, will directly shape the code.
