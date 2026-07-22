import pytest
from pathlib import Path
from telebot.asyncio_handler_backends import CancelUpdate
from telebot.types import Message, User, Chat

from vlog_journal.bot.app import create_bot
from vlog_journal.bot.middleware import WhitelistMiddleware
from vlog_journal.bot.handlers import WELCOME_TEXT, HELP_TEXT, STUB_RESPONSE
from vlog_journal.config import load_config

@pytest.mark.asyncio
async def test_whitelist_middleware_allowed():
    mw = WhitelistMiddleware(allowed_user_ids={12345, 67890})
    msg = Message(
        message_id=1,
        from_user=User(id=12345, is_bot=False, first_name="Test"),
        date=1000,
        chat=Chat(id=12345, type="private"),
        content_type="text",
        options={},
        json_string="",
    )
    result = await mw.pre_process(msg, {})
    assert result is None

@pytest.mark.asyncio
async def test_whitelist_middleware_blocked():
    mw = WhitelistMiddleware(allowed_user_ids={12345})
    msg = Message(
        message_id=2,
        from_user=User(id=99999, is_bot=False, first_name="Intruder"),
        date=1000,
        chat=Chat(id=99999, type="private"),
        content_type="text",
        options={},
        json_string="",
    )
    result = await mw.pre_process(msg, {})
    assert isinstance(result, CancelUpdate)

def test_whitelist_middleware_string_parsing():
    mw = WhitelistMiddleware(allowed_user_ids="111, 222,  333 ")
    assert mw.allowed_user_ids == {111, 222, 333}

def test_create_bot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
    monkeypatch.setenv("TELEGRAM_API_ID", "123")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    monkeypatch.setenv("ALLOWED_USER_IDS", "12345,67890")
    
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
    video_diary = []
    """
    config_file = tmp_path / "config.toml"
    config_file.write_text(config_content)
    
    settings = load_config(config_file)
    bot = create_bot(settings)
    assert bot is not None

def test_handler_texts():
    assert "Welcome to Vlog Journal Bot" in WELCOME_TEXT
    assert "Available Commands" in HELP_TEXT
    assert "Feature coming soon" in STUB_RESPONSE
