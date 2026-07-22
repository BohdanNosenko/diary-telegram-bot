vlog-journal Technical Specification & Architecture Reference1. Executive Summary & System Objectivesvlog-journal is an open-source, modular Python application designed to automate the lifecycle of daily video and audio diaries. It ingests video clips, native Telegram voice memos, or multi-item daily sessions sent via Telegram, normalizes and stitches media according to custom quality presets, transcribes audio locally on GPU, uses a local LLM (or cloud model) to generate structured summary notes with multi-speaker support and hierarchical tagging, provides an interactive review draft in Telegram, saves the final output to a configurable Obsidian Vault, and manages encrypted Google Drive backups via Rclone.Target Hardware & System EnvironmentHost Machine: Acer Nitro 5 (AMD Ryzen CPU + NVIDIA GeForce RTX 30-series GPU with 8GB VRAM).OS Environment: Windows 11 with WSL2 (Ubuntu Linux).System Package / Dev Shell Manager: Nix Flakes (flake.nix via nix develop).Python Package Manager: uv.Mobile Capture Device: Samsung Galaxy S24 Ultra + Samsung Galaxy Buds 3 Pro.2. Prerequisites & Dependency Management Matrix2.1 Dependency Division MatrixComponentResponsible ManagerInstallation / Management MethodFFmpeg (CLI & codecs)Nix Flake (flake.nix)Provided inside nix develop devShellRclone & 7-ZipNix Flake (flake.nix)Provided inside nix develop devShellNVIDIA CUDA DriversWSL2 Host / NixWindows NVIDIA Driver + WSL CUDA passthroughOllama DaemonNix Flake / DaemonProvided via devShell (ollama serve)Telegram Bot APIDocker ComposeOfficial Docker container (aiogram/telegram-bot-api)uv Package ManagerNix Flake (flake.nix)Provided inside nix develop devShellFaster-Whisper Engineuv (pyproject.toml)Installs faster-whisper Python package + CTranslate2 binariesLLM Client / SDKsuv (pyproject.toml)Installs litellm & pydantic Python packagesBot Frameworkuv (pyproject.toml)Installs pyTelegramBotAPI Python package2.2 Nix Flake DevShell (flake.nix)Project environment tools (ffmpeg, rclone, p7zip, uv, ollama) are declared in flake.nix and activated strictly when working in this project directory via nix develop (or automatically via direnv).{
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
2.3 Container Architecture (docker-compose.yml)Standard Telegram cloud APIs cap downloads at 20MB. Running a local Bot API server in Docker raises the upload/download limit to 2GB. Both the Telegram Bot API server and the Python application share a local volume mount (telegram-bot-data) so downloaded files are accessible directly via local disk paths.version: '3.8'

services:
  telegram-bot-api:
    image: aiogram/telegram-bot-api:latest
    container_name: telegram_local_server
    restart: unless-stopped
    environment:
      TELEGRAM_API_ID: "YOUR_TELEGRAM_API_ID"
      TELEGRAM_API_HASH: "YOUR_TELEGRAM_API_HASH"
      TELEGRAM_LOCAL: "true"
    volumes:
      - telegram-bot-data:/var/lib/telegram-bot-api
    ports:
      - "8081:8081"

  vlog-journal-app:
    build: .
    container_name: vlog_journal_bot
    restart: unless-stopped
    depends_on:
      - telegram-bot-api
    network_mode: "host"
    env_file:
      - .env
    volumes:
      - telegram-bot-data:/var/lib/telegram-bot-api
      - /home/user/Obsidian/PersonalVault:/vault

volumes:
  telegram-bot-data:
    name: telegram-bot-data
2.4 GPU VRAM Budget & Memory Management (8GB VRAM Limit)To prevent CUDA Out-Of-Memory (OOM) crashes on an 8GB GPU:Recommended Default: qwen2.5:7b + Faster-Whisper medium.en (~7.2 GB VRAM combined).Memory Cleanup Guard: transcriber.py explicitly executes del model; gc.collect(); torch.cuda.empty_cache() after processing to keep CUDA buffers clean.3. Mobile Capture Strategy, Audio Memos, & Date Resolution3.1 Earliest Item Date Anchor (min(creation_time))When a multi-item session or single upload is processed:media.py inspects queued items (.mp4 video clips and .ogg/.wav voice memos) for embedded EXIF/stream timestamps via ffprobe.The entry date is anchored to the date of the earliest recorded item (min(creation_times)).Example: Video clips or voice notes recorded at 10:30 PM on July 20th and 1:15 AM on July 21st are automatically anchored to 2026-07-20.Overrides: Users can explicitly pass a date via /start_session 2026-07-20 or adjust the date during the interactive Telegram review step (✏️ Edit Note -> "Set date to 2026-07-20").3.2 Native Telegram Voice Memo IntegrationVoice memos sent to the bot (standalone or inside a session) are:Captured via Telegram's voice content type (.ogg Opus stream).Transcribed via Faster-Whisper alongside video audio.Transcoded to a lightweight MP3 file (128k) and stored in Attachments/Vlogs/YYYY-MM-DD-audio.mp3.Linked natively in the Obsidian note alongside or in place of video embeds (![[YYYY-MM-DD-audio.mp3]]).3.3 Pro Video Mode SettingsResolution & Framerate: HD 720p @ 30 FPS (or 1080p 30 FPS).Dynamic Range: HDR10+ OFF.Audio Input: Bluetooth (Galaxy Buds 3 Pro mic).4. Obsidian Integration, Tags, & Encrypted Backup4.1 Vault Structure & Collision ProtectionPersonalVault/
├── Journal/
│   └── Vlogs/
│       ├── 2026-07-20.md
│       └── 2026-07-21.md
└── Attachments/
    └── Vlogs/
        ├── 2026-07-20.mp4
        └── 2026-07-21-audio.mp3
Same-Day Collision Protection: If YYYY-MM-DD.md already exists in the vault when saving a new entry, the storage processor automatically appends an incremental suffix (e.g., 2026-07-21-02.md and 2026-07-21-02.mp4), preventing accidental overwrites of earlier entries recorded on the same day.4.2 Dynamic Tag Manager (TagManager)Local Cache (tags.json): Stores sorted list of unique vault tags for instant $0\text{ms}$ LLM prompt construction.Write-Through: Automatically appends new tags to tags.json when a note is approved and saved.Reconciliation (/sync_tags): On startup or via Telegram command, scans .md frontmatter in vault_path, updates tags.json, and prunes deleted tags.4.3 Encrypted Rclone Backup & Retention PolicyBackups run automatically via background schedule or manually via /backup:Archive Creation: Compresses and encrypts /vault/ into an AES-256 7-Zip file using BACKUP_ENCRYPTION_PASSPHRASE.Remote Sync: Uploads archive to Google Drive via Rclone using RCLONE_CONFIG_ credentials in .env.Retention Policy (2 Daily + 1 Weekly):Retains the last 2 daily backups (*_daily.7z).Retains the last 1 weekly backup (*_weekly.7z, taken on Sundays).Automatically prunes older archives from Google Drive.5. Unified Processing Engine & Bot Resilience5.1 Persistent Session StateTo prevent data loss if WSL, Docker, or the bot script restarts during an active vlogging day, active session queues and pending draft reviews are persisted to disk at data/sessions.json. On startup, the bot reloads active sessions automatically.5.2 Intermediate Disk CleanupAll intermediate video segments, extracted WAV files, and raw 7z archives in /tmp/ are automatically deleted by the pipeline execution wrapper immediately after a note is approved/saved or discarded.┌─────────────────────────────────────────────────────────┐
│                    Telegram Interface                   │
└────────────────────────────┬────────────────────────────┘
                             │
            ┌────────────────┴────────────────┐
            │ Video / Voice / /finish_session │
            └────────────────┬────────────────┘
                             │
                             ▼
              ┌─────────────────────────────┐
              │ State Manager & Media Prep  │ ──► Persist Queue (data/sessions.json)
              │ - Supports Video & Voice    │ ──► Date Anchor (min(creation_time))
              │ - Live Telegram Progress    │
              └──────────────┬──────────────┘
                             │
                             ▼
                ┌─────────────────────────┐
                │ Faster-Whisper (CUDA)   │ ──► Raw Transcript
                └────────────┬────────────┘
                             │
                             ▼
                ┌─────────────────────────┐
                │ LiteLLM (Ollama/Gemini) │ ◄── Master Tags (tags.json)
                └────────────┬────────────┘ ◄── Caption Context ("With Dima")
                             │
                             ▼
                ┌─────────────────────────┐
                │ Telegram Review Loop    │ ◄── User Edit: "Set date to yesterday"
                │ [ Approve ]   [ Edit ]  │
                └────────────┬────────────┘
                             │ Approved
                             ▼
                ┌─────────────────────────┐
                │ Obsidian Vault Storage  │ ──► Collision Protection (Suffix -02)
                └────────────┬────────────┘ ──► Purge /tmp/ Intermediate Files
                             │
                             ▼
                ┌─────────────────────────┐
                │ Rclone Encrypted Backup │ ──► AES-256 Archive
                └─────────────────────────┘ ──► Upload GDrive & Prune (2D + 1W)
6. Directory Layout (src Layout)vlog-journal/
├── flake.nix                  # Nix Flake dev environment spec
├── flake.lock                 # Nix dependency lockfile
├── config.example.toml        # Template configuration file
├── .env.example               # Secrets template (Bot Token, API Keys)
├── docker-compose.yml         # Local Telegram Bot API server setup
├── pyproject.toml             # uv package build spec
├── uv.lock                    # Universal lockfile
├── README.md
├── src/
│   └── vlog_journal/
│       ├── __init__.py
│       ├── cli.py             # App entrypoint CLI
│       ├── config.py          # Pydantic Settings & TOML loader
│       ├── bot/
│       │   ├── __init__.py
│       │   ├── app.py         # AsyncTeleBot engine & state reloader
│       │   ├── state.py       # Persistent session manager (data/sessions.json)
│       │   └── middleware.py  # User ID Whitelist Guard
│       ├── pipeline/
│       │   ├── __init__.py
│       │   ├── registry.py    # Decorator registry & Context definitions
│       │   └── runner.py      # TOML pipeline interpreter
│       ├── processors/
│       │   ├── __init__.py
│       │   ├── media.py       # Video/audio transcode, stitch, & voice memo handlers
│       │   ├── transcriber.py # Faster-Whisper VRAM manager & wrapper
│       │   └── llm.py         # LiteLLM + Pydantic schema validation
│       └── vault/
│           ├── __init__.py
│           ├── markdown.py    # Obsidian markdown builder
│           ├── tags.py        # TagManager & tags.json cache reconciler
│           ├── backup.py      # Encrypted Rclone archive & 2D+1W retention
│           └── storage.py     # File mover, collision resolver, & vault path writer
└── tests/
    ├── test_media.py
    └── test_pipeline.py
7. Configuration Specifications7.1 pyproject.toml[project]
name = "vlog-journal"
version = "0.1.0"
description = "Automated video/voice diary pipeline with local transcription and Obsidian sync"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "pyTelegramBotAPI>=4.20.0",
    "faster-whisper>=1.0.0",
    "pydantic>=2.7.0",
    "pydantic-settings>=2.2.0",
    "litellm>=1.35.0",
    "pyyaml>=6.0",
    "aiofiles>=23.2.1",
    "torch>=2.2.0",
    "apscheduler>=3.10.0",
]

[project.scripts]
vlog-journal = "vlog_journal.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
dev-dependencies = [
    "pytest>=8.0.0",
    "ruff>=0.4.0",
]
7.2 .env.example# Telegram Credentials
TELEGRAM_BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
ALLOWED_USER_IDS="123456789,987654321"
TELEGRAM_LOCAL_API_URL="http://localhost:8081"

# Rclone Google Drive Integration
RCLONE_CONFIG_GDRIVE_TYPE="drive"
RCLONE_CONFIG_GDRIVE_SCOPE="drive.file"
RCLONE_CONFIG_GDRIVE_TOKEN='{"access_token":"...","token_type":"Bearer","refresh_token":"...","expiry":"..."}'

# Backup Encryption Passphrase
BACKUP_ENCRYPTION_PASSPHRASE="your-super-secret-passphrase"

# Cloud LLM Keys (Optional)
GEMINI_API_KEY=""
7.3 config.example.toml[app]
vault_name = "PersonalVault"
vault_path = "/vault"
vlogs_relative_path = "Journal/Vlogs"
media_relative_path = "Attachments/Vlogs"
tags_cache_file = "data/tags.json"
sessions_state_file = "data/sessions.json"

[media]
target_resolution = "720p"  # Options: "original", "4k", "1080p", "720p", "480p", "240p"
target_fps = 30             # Options: 24, 30, 60, or "keep"
video_codec = "libsvtav1"    # Options: "libsvtav1", "hevc_nvenc", "h264"
audio_codec = "libopus"       # Options: "libopus", "aac"
audio_bitrate = "128k"
crf = 32                     # Constant Rate Factor (Quality target)

[transcription]
engine = "faster-whisper"
model = "medium.en"
device = "cuda"
compute_type = "float16"

[llm]
provider = "ollama/qwen2.5:7b"
api_base = "http://localhost:11434"
temperature = 0.3

[backup]
enabled = true
schedule_cron = "0 4 * * *"          # Daily at 4:00 AM
remote_name = "gdrive"
remote_folder = "vlog-journal-backups"
retention_daily_days = 2             # Keep 2 daily backups
retention_weekly_weeks = 1           # Keep 1 weekly backup

[pipelines]
video_diary = [
  "media.prepare_and_stitch",
  "media.extract_audio",
  "audio.whisper_transcribe",
  "llm.structure_transcript",
  "vault.save_entry",
  "media.cleanup_temp_files"
]

backup_vault = [
  "vault.create_encrypted_archive",
  "vault.upload_and_prune_remote"
]
8. Core Technical Implementation8.1 Persistent Session Manager (src/vlog_journal/bot/state.py)import json
from pathlib import Path
from typing import Dict, Any

class SessionManager:
    def __init__(self, state_file: Path):
        self.state_file = state_file
        self._sessions: Dict[int, Dict[str, Any]] = {}
        self.load_state()

    def load_state(self) -> None:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self._sessions = {int(k): v for k, v in data.items()}
            except Exception:
                self._sessions = {}

    def save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self._sessions, f, indent=2)

    def start_session(self, chat_id: int) -> None:
        self._sessions[chat_id] = {"clips": [], "captions": []}
        self.save_state()

    def add_clip(self, chat_id: int, clip_path: str, caption: str | None = None) -> None:
        if chat_id not in self._sessions:
            self._sessions[chat_id] = {"clips": [], "captions": []}
        self._sessions[chat_id]["clips"].append(clip_path)
        if caption:
            self._sessions[chat_id]["captions"].append(caption)
        self.save_state()

    def pop_session(self, chat_id: int) -> Dict[str, Any] | None:
        session = self._sessions.pop(chat_id, None)
        self.save_state()
        return session

    def is_active(self, chat_id: int) -> bool:
        return chat_id in self._sessions
8.2 Media Processor & Audio Handler (src/vlog_journal/processors/media.py)import asyncio
import json
from datetime import datetime
from pathlib import Path
from vlog_journal.pipeline.registry import register_step, PipelineContext

RESOLUTION_MAP = {
    "4k": "3840:2160",
    "1080p": "1920:1080",
    "720p": "1280:720",
    "480p": "854:480",
    "240p": "426:240"
}

async def get_item_creation_date(item_path: Path) -> datetime:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_entries", "format_tags=creation_time:stream_tags=creation_time",
        str(item_path)
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    try:
        data = json.loads(stdout.decode())
        tags = data.get("format", {}).get("tags", {}) or data.get("streams", [{}])[0].get("tags", {})
        time_str = tags.get("creation_time")
        if time_str:
            return datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    except Exception:
        pass
    return datetime.fromtimestamp(item_path.stat().st_mtime)

@register_step("media.prepare_and_stitch")
async def prepare_and_stitch_step(ctx: PipelineContext) -> PipelineContext:
    clip_paths = [Path(p) for p in ctx.payload["queued_clip_paths"]]
    session_dir = Path(ctx.payload.get("session_dir", "/tmp"))
    
    # 1. Resolve Entry Date to Earliest Clip Date
    creation_dates = [await get_item_creation_date(p) for p in clip_paths]
    earliest_date = min(creation_dates)
    ctx.payload["entry_date"] = earliest_date.strftime("%Y-%m-%d")
    
    # Check if input is purely voice notes
    is_voice = all(p.suffix.lower() in [".ogg", ".wav", ".mp3", ".m4a"] for p in clip_paths)
    ctx.payload["is_voice_memo"] = is_voice

    if is_voice:
        output_audio_path = session_dir / f"{ctx.payload['entry_date']}-audio.mp3"
        cmd = ["ffmpeg", "-y"]
        for p in clip_paths: cmd.extend(["-i", str(p)])
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "128k", str(output_audio_path)])
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
        ctx.payload["raw_video_path"] = None
        ctx.payload["audio_wav_path"] = str(output_audio_path)
        return ctx

    output_master_path = session_dir / f"{ctx.payload['entry_date']}-master.mp4"
    res_setting = ctx.metadata.get("media_target_resolution", "720p")
    target_res = RESOLUTION_MAP.get(res_setting, "1280:720")
    width, height = target_res.split(":")

    if len(clip_paths) == 1 and res_setting == "original":
        ctx.payload["raw_video_path"] = str(clip_paths[0])
        return ctx

    inputs = []
    filter_chains = []
    concat_inputs = []

    for idx, path in enumerate(clip_paths):
        inputs.extend(["-i", str(path)])
        filter_chains.append(
            f"[{idx}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=30[v{idx}];"
            f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=mono,apad=pad_dur=0.5[a{idx}];"
        )
        concat_inputs.append(f"[v{idx}][a{idx}]")

    filter_complex = (
        "".join(filter_chains) +
        "".join(concat_inputs) +
        f"concat=n={len(clip_paths)}:v=1:a=1[outv][outa]"
    )

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", ctx.metadata.get("media_video_codec", "libsvtav1"),
        "-preset", "8", "-crf", str(ctx.metadata.get("media_crf", 32)),
        "-c:a", ctx.metadata.get("media_audio_codec", "libopus"),
        "-b:a", ctx.metadata.get("media_audio_bitrate", "128k"),
        str(output_master_path)
    ]

    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg transcode/stitch failed: {stderr.decode()}")

    ctx.payload["raw_video_path"] = str(output_master_path)
    return ctx

@register_step("media.cleanup_temp_files")
async def cleanup_temp_files_step(ctx: PipelineContext) -> PipelineContext:
    for p_key in ["raw_video_path", "audio_wav_path"]:
        path_str = ctx.payload.get(p_key)
        if path_str and str(path_str).startswith("/tmp"):
            p = Path(path_str)
            if p.exists(): p.unlink()
    
    queued = ctx.payload.get("queued_clip_paths", [])
    for p_str in queued:
        if str(p_str).startswith("/tmp"):
            p = Path(p_str)
            if p.exists(): p.unlink()

    return ctx
8.3 Vault Storage & Same-Day Collision Resolver (src/vlog_journal/vault/storage.py)import shutil
from pathlib import Path
from vlog_journal.pipeline.registry import register_step, PipelineContext

@register_step("vault.save_entry")
async def save_entry_step(ctx: PipelineContext) -> PipelineContext:
    vault_path = Path(ctx.metadata.get("app_vault_path", "/vault"))
    vlogs_rel = ctx.metadata.get("app_vlogs_relative_path", "Journal/Vlogs")
    media_rel = ctx.metadata.get("app_media_relative_path", "Attachments/Vlogs")
    
    entry_date = ctx.payload["entry_date"]
    markdown_content = ctx.payload["draft_markdown"]

    vlogs_dir = vault_path / vlogs_rel
    media_dir = vault_path / media_rel
    vlogs_dir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)

    # 1. Resolve Same-Day File Naming Collisions
    counter = 1
    file_suffix = ""
    while (vlogs_dir / f"{entry_date}{file_suffix}.md").exists():
        counter += 1
        file_suffix = f"-{counter:02d}"

    final_md_path = vlogs_dir / f"{entry_date}{file_suffix}.md"

    # 2. Move media file if present
    raw_video = ctx.payload.get("raw_video_path")
    raw_audio = ctx.payload.get("audio_wav_path")

    if raw_video and Path(raw_video).exists():
        dest_media_path = media_dir / f"{entry_date}{file_suffix}.mp4"
        shutil.move(raw_video, dest_media_path)
    elif raw_audio and Path(raw_audio).exists():
        dest_media_path = media_dir / f"{entry_date}{file_suffix}-audio.mp3"
        shutil.move(raw_audio, dest_media_path)

    # 3. Save Markdown note
    with open(final_md_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)

    ctx.payload["final_markdown_path"] = str(final_md_path)
    return ctx
8.4 Backup & Rclone Module (src/vlog_journal/vault/backup.py)import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from vlog_journal.pipeline.registry import register_step, PipelineContext

@register_step("vault.create_encrypted_archive")
async def create_archive_step(ctx: PipelineContext) -> PipelineContext:
    vault_path = Path(ctx.metadata.get("app_vault_path", "/vault"))
    passphrase = os.getenv("BACKUP_ENCRYPTION_PASSPHRASE", "default_secret")
    
    today = datetime.now()
    is_sunday = today.weekday() == 6
    tag = "weekly" if is_sunday else "daily"
    archive_name = f"vlog_backup_{today.strftime('%Y-%m-%d')}_{tag}.7z"
    archive_path = Path("/tmp") / archive_name

    cmd = [
        "7z", "a", "-t7z", "-mhe=on",
        f"-p{passphrase}",
        str(archive_path),
        str(vault_path)
    ]

    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"7z compression failed: {stderr.decode()}")

    ctx.payload["archive_path"] = str(archive_path)
    ctx.payload["archive_name"] = archive_name
    return ctx

@register_step("vault.upload_and_prune_remote")
async def upload_and_prune_step(ctx: PipelineContext) -> PipelineContext:
    archive_path = Path(ctx.payload["archive_path"])
    remote_folder = f"{ctx.metadata.get('backup_remote_name', 'gdrive')}:{ctx.metadata.get('backup_remote_folder', 'vlog-backups')}"

    copy_cmd = ["rclone", "copy", str(archive_path), remote_folder]
    proc = await asyncio.create_subprocess_exec(*copy_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()

    list_cmd = ["rclone", "lsjson", remote_folder]
    proc = await asyncio.create_subprocess_exec(*list_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    
    if proc.returncode == 0 and stdout:
        files = json.loads(stdout.decode())
        dailies = sorted([f for f in files if "_daily.7z" in f["Name"]], key=lambda x: x["Name"])
        weeklies = sorted([f for f in files if "_weekly.7z" in f["Name"]], key=lambda x: x["Name"])

        while len(dailies) > ctx.metadata.get("backup_retention_daily_days", 2):
            target = dailies.pop(0)
            await asyncio.create_subprocess_exec("rclone", "deletefile", f"{remote_folder}/{target['Name']}")

        while len(weeklies) > ctx.metadata.get("backup_retention_weekly_weeks", 1):
            target = weeklies.pop(0)
            await asyncio.create_subprocess_exec("rclone", "deletefile", f"{remote_folder}/{target['Name']}")

    if archive_path.exists():
        archive_path.unlink()

    return ctx
9. Deployment & Execution Guide# 1. Enter isolated dev environment via Nix Flake
nix develop

# 2. Start background services inside dev shell
ollama serve &
docker compose up -d

# 3. Initialize/Sync Python virtual environment using uv
uv sync

# 4. Launch application CLI
uv run vlog-journal --config config.toml
