import tomllib
from pathlib import Path
from pydantic import BaseModel, Field, field_validator, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class AppConfig(BaseModel):
    vault_name: str
    vault_path: Path
    vlogs_relative_path: str = "Journal/Vlogs"
    media_relative_path: str = "Attachments/Vlogs"
    tags_cache_file: Path = Path("data/tags.json")
    sessions_state_file: Path = Path("data/sessions.json")
    session_timeout_hours: int = 12

class MediaConfig(BaseModel):
    target_resolution: str = "720p"
    target_fps: int = 30
    video_codec: str = "libsvtav1"
    audio_codec: str = "libopus"
    audio_bitrate: str = "128k"
    crf: int = 32

    @field_validator("target_resolution")
    @classmethod
    def validate_resolution(cls, v: str) -> str:
        allowed = {"240p", "360p", "720p", "1080p", "4k"}
        if v not in allowed:
            raise ValueError(f"Resolution must be one of {allowed}")
        return v

class TranscriptionConfig(BaseModel):
    engine: str = "faster-whisper"
    model: str = "large-v3"
    language: str = "auto"
    device: str = "cuda"
    compute_type: str = "float16"

class DiarizationConfig(BaseModel):
    enabled: bool = True
    min_speakers: int = 1
    max_speakers: int = 5

class LLMFallbackConfig(BaseModel):
    provider: str = "gemini/gemini-2.5-flash"
    temperature: float = 0.3

class LLMConfig(BaseModel):
    provider: str = "ollama/qwen2.5:14b-q3_K_M"
    api_base: str = "http://localhost:11434"
    temperature: float = 0.3
    timeout: int = 120
    fallback: LLMFallbackConfig | None = None

class EnrichmentConfig(BaseModel):
    gps_extraction: bool = True
    reverse_geocode: bool = True
    weather_fetch: bool = True
    proximity_dedup_meters: int = 500

class BackupConfig(BaseModel):
    enabled: bool = True
    schedule_cron: str = "0 4 * * *"
    remote_name: str = "gdrive"
    remote_folder: str = "vlog-journal-backups"
    retention_daily_days: int = 2
    retention_weekly_weeks: int = 1

class PipelinesConfig(BaseModel):
    video_diary: list[str] = Field(default_factory=list)
    backup_vault: list[str] = Field(default_factory=list)

class AppSettings(BaseSettings):
    # TOML sections
    app: AppConfig
    media: MediaConfig
    transcription: TranscriptionConfig
    diarization: DiarizationConfig
    llm: LLMConfig
    enrichment: EnrichmentConfig
    backup: BackupConfig
    pipelines: PipelinesConfig
    
    # Env variables
    telegram_bot_token: SecretStr
    telegram_api_id: str
    telegram_api_hash: str
    allowed_user_ids: str
    telegram_local_api_url: str = "http://localhost:8081"
    
    hf_token: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    
    rclone_config_gdrive_type: str | None = None
    rclone_config_gdrive_scope: str | None = None
    rclone_config_gdrive_token: SecretStr | None = None
    
    backup_encryption_passphrase: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

def load_config(toml_path: Path | str = "config.toml") -> AppSettings:
    toml_path = Path(toml_path)
    if not toml_path.exists():
        raise FileNotFoundError(f"Config file not found: {toml_path}")
        
    with open(toml_path, "rb") as f:
        toml_data = tomllib.load(f)
        
    return AppSettings(**toml_data)
