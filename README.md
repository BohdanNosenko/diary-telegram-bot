# vlog-journal

> **Personal Vlog & Voice Diary Automation Assistant for Obsidian**
>
> Process raw video clips and voice notes sent via Telegram into beautifully structured, enriched, and searchable Obsidian markdown notes with AI summaries, multi-speaker diarization, offline GPS geocoding, Open-Meteo weather data, interactive review loops, and automated encrypted backups.

---

## 🌟 Key Features

- 📹 **Multi-Clip Video & Voice Stitching**: Automatically stitches multiple video clips (scaling/padding to target resolution like `720p`/`1080p` and normalizing FPS) or concatenates voice notes via FFmpeg.
- 🎙️ **Faster-Whisper Transcription**: High-speed, high-accuracy speech-to-text with auto language detection and per-segment confidence scores using `WhisperModel` (`large-v3`).
- 👥 **Pyannote Speaker Diarization**: Multi-speaker diarization with automatic timestamp overlap merging and custom speaker relabeling (`Speaker 1 = Me, Speaker 2 = Mom`).
- 🤖 **Structured LLM Processing (LiteLLM)**: Structured output validation via `NoteSchema` (Pydantic). Primary execution on local Ollama (`qwen2.5:14b`) with automatic failover to Google Gemini (`gemini-2.5-flash`).
- 📍 **GPS Extraction & Offline Geocoding**: Extracts ISO 6709 location metadata from Samsung S24 Ultra videos, deduplicates locations within a 500m radius using Haversine formulas, and resolves city names offline via `reverse_geocoder`.
- 🌤️ **Open-Meteo Weather Integration**: Fetches historical/forecast weather data (temperature, WMO weather descriptions) for entry locations without requiring an API key.
- 📝 **Jinja2 Obsidian Templates**: Renders clean Markdown files featuring maximalist Dataview-compatible YAML frontmatter, summary blockquotes, highlights, action item checkboxes, and collapsible transcript callouts (`> [!NOTE]-`).
- 💬 **Interactive Telegram Review Loop**: Preview draft notes in Telegram before saving. Supports interactive inline keyboards (`[✅ Approve]`, `[✏️ Edit]`, `[❌ Discard]`), speaker relabeling, date overrides, and free-text LLM prompts.
- 🔒 **Encrypted 7z Backups & Rclone Sync**: Creates AES-256 encrypted 7z archives in pure Python (`py7zr`) keeping passphrases in memory, uploads archives to Google Drive via `rclone`, and enforces a 2-daily + 1-weekly retention policy.
- ⚡ **Strict VRAM & Resource Management**: Unloads GPU models (`WhisperModel`, Pyannote `Pipeline`) immediately after use to maintain peak VRAM usage under 8 GB.

---

## 🏗️ Architecture Overview

```
                          ┌───────────────────────────┐
                          │   Telegram Bot Client     │
                          └─────────────┬─────────────┘
                                        │
                                        ▼
                          ┌───────────────────────────┐
                          │   Local Bot API Server    │
                          │     (Docker Container)    │
                          └─────────────┬─────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            vlog-journal Engine                              │
│                                                                             │
│  ┌──────────────────┐    ┌──────────────────┐    ┌───────────────────────┐  │
│  │ Media Processing │ ──►│  Transcription   │ ──►│    LLM Structuring    │  │
│  │     (FFmpeg)     │    │(Whisper+Pyannote)│    │(Ollama / Gemini Lite) │  │
│  └──────────────────┘    └──────────────────┘    └───────────┬───────────┘  │
│                                                              │              │
│  ┌──────────────────┐    ┌──────────────────┐                │              │
│  │ Obsidian Vault   │ ◄──│ Jinja2 Renderer  │ ◄───────────────┘              │
│  │ Storage & Tags   │    │  & Enrichment    │ (GPS, Weather, Media Stats)   │
│  └────────┬─────────┘    └──────────────────┘                               │
└───────────┼─────────────────────────────────────────────────────────────────┘
            │
            ▼
┌───────────────────────┐
│ Encrypted 7z Backup   │ ──► Upload via Rclone to Cloud Storage
│ (py7zr AES-256)       │     (2 Daily + 1 Weekly Retention)
└───────────────────────┘
```

---

## 📋 Bot Command Reference

| Command | Description |
|---|---|
| `/start` | Welcome message and quick start overview |
| `/start_session [YYYY-MM-DD]` | Start a recording session (optional date override) |
| `/finish_session` | Finalize clip collection and trigger full processing pipeline |
| `/cancel` | Cancel active session and delete temporary media files |
| `/status` | Diagnostic health check (Ollama status, VRAM usage, disk space, Rclone, active sessions) |
| `/retry` | Resume a failed/interrupted pipeline from the failed step |
| `/retry full` | Re-run the processing pipeline from step 0 |
| `/sync_tags` | Scan Obsidian vault markdown files and reconcile `data/tags.json` cache |
| `/backup` | Manually trigger encrypted 7z backup and remote cloud upload |
| `/help` | Show command list and usage instructions |

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- **OS**: Linux (Ubuntu 22.04+ on bare metal or WSL2)
- **NVIDIA GPU**: 8 GB+ VRAM with CUDA support
- **Tools**: [Nix](https://nixos.org/) with Flake support, [direnv](https://direnv.net/), Docker & Docker Compose

### 2. Installation

Clone the repository and enter the dev environment:

```bash
git clone https://github.com/BohdanNosenko/diary-telegram-bot.git vlog-journal
cd vlog-journal
direnv allow   # Or run 'nix develop'
```

### 3. Environment Configuration

Copy the example environment and configuration files:

```bash
cp .env.example .env
cp config.example.toml config.toml
```

Edit `.env` to supply your credentials:
```env
TELEGRAM_BOT_TOKEN="123456789:ABC..."
ALLOWED_USER_IDS="123456789"
HF_TOKEN="hf_..."
BACKUP_ENCRYPTION_PASSPHRASE="your-secret-passphrase"
```

Edit `config.toml` to set your Obsidian vault path:
```toml
[app]
vault_path = "/home/user/documents/PersonalVault"
```

### 4. Start Local Telegram Bot API Server & Ollama

```bash
# Start local Telegram Bot API container (bypasses 50 MB upload limits for up to 2 GB files)
docker compose up -d

# Start Ollama service and pull the structured model
ollama serve &
ollama pull qwen2.5:14b-q3_K_M
```

### 5. Run the Bot

```bash
uv run vlog-journal
```

---

## 📄 Obsidian Markdown Note Example

Every entry is saved as a Markdown note with full Dataview-queryable YAML frontmatter:

```markdown
---
date: 2026-07-22
time: "20:30"
day_of_week: Wednesday
type: vlog
media_type: video
title: Evening cooking session with Mom

mood: relaxed
energy_level: medium
category: conversation
summary: >
  Cooked dinner with Mom. Tried a new recipe for roasted beets and discussed upcoming family weekend plans.
people:
  - Mom
topics:
  - cooking
  - family
key_highlights:
  - Roasted beets turned out great
  - Planned family dinner for Saturday

primary_location: "Brooklyn, New York"
primary_weather: "24°C, partly cloudy"
duration: "00:05:32"
clip_count: 2
word_count: 650
speakers: 2

tags:
  - journal/vlog
  - people/mom
  - topic/cooking
  - topic/family
  - location/brooklyn
---

# Vlog — 2026-07-22

![[2026-07-22.mp4]]

> Cooked dinner with Mom. Tried a new recipe for roasted beets and discussed upcoming family weekend plans.

## Highlights
- Roasted beets turned out great
- Planned family dinner for Saturday

## Action Items
- [ ] Buy beets at farmer's market on Saturday
- [ ] Call brother about weekend dinner

## Transcript

> [!NOTE]- Full Transcript (650 words, 00:05:32)
>
> **Bohdan** *(00:00)*
> Let's try roasting the beets today.
>
> **Mom** *(00:15)*
> That sounds like a great idea.
```

---

## 🧪 Testing

Run unit and end-to-end integration tests using `uv`:

```bash
# Run all tests
uv run pytest tests/ -v

# Run code linter
uv run ruff check src/ tests/
```

---

## 📜 License

This project is licensed under the MIT License.
