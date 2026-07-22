import pytest
from pathlib import Path
from pydantic import ValidationError
from vlog_journal.config import load_config

def test_load_config_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Set required env vars
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
    monkeypatch.setenv("TELEGRAM_API_ID", "123")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    monkeypatch.setenv("ALLOWED_USER_IDS", "1,2")
    
    config_content = """
    [app]
    vault_name = "TestVault"
    vault_path = "/tmp/vault"
    
    [media]
    target_resolution = "720p"
    
    [transcription]
    engine = "faster-whisper"
    
    [diarization]
    enabled = true
    
    [llm]
    provider = "test"
    api_base = "test"
    temperature = 0.5
    timeout = 60
    
    [enrichment]
    gps_extraction = false
    
    [backup]
    enabled = false
    
    [pipelines]
    video_diary = ["step1", "step2"]
    """
    
    config_file = tmp_path / "config.toml"
    config_file.write_text(config_content)
    
    settings = load_config(config_file)
    
    assert settings.app.vault_name == "TestVault"
    assert settings.app.vault_path == Path("/tmp/vault")
    assert settings.media.target_resolution == "720p"
    assert settings.media.video_codec == "libsvtav1" # default
    assert settings.pipelines.video_diary == ["step1", "step2"]
    assert settings.telegram_bot_token.get_secret_value() == "test_token"

def test_load_config_invalid_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
    monkeypatch.setenv("TELEGRAM_API_ID", "123")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    monkeypatch.setenv("ALLOWED_USER_IDS", "1,2")
    
    config_content = """
    [app]
    vault_name = "TestVault"
    vault_path = "/tmp/vault"
    
    [media]
    target_resolution = "999p"
    
    [transcription]
    engine = "faster-whisper"
    
    [diarization]
    enabled = true
    
    [llm]
    provider = "test"
    api_base = "test"
    temperature = 0.5
    timeout = 60
    
    [enrichment]
    gps_extraction = false
    
    [backup]
    enabled = false
    
    [pipelines]
    """
    
    config_file = tmp_path / "config.toml"
    config_file.write_text(config_content)
    
    with pytest.raises(ValidationError) as exc:
        load_config(config_file)
        
    assert "Resolution must be one of" in str(exc.value)

def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config("nonexistent_file.toml")
