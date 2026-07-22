from datetime import datetime, timezone, timedelta
from pathlib import Path
from vlog_journal.bot.state import SessionManager

def test_session_lifecycle(tmp_path: Path):
    state_file = tmp_path / "sessions.json"
    sm = SessionManager(state_file)

    # 1. Start Session
    session = sm.start_session(chat_id=12345, date_override="2026-07-20")
    assert session["status"] == "collecting"
    assert session["entry_date"] == "2026-07-20"
    assert session["clips"] == []
    assert sm.is_active(12345) is True

    # 2. Add Clips (Video and Voice)
    sm.add_clip(chat_id=12345, clip_path="/tmp/v1.mp4", media_type="video", caption="Clip 1")
    sm.add_clip(chat_id=12345, clip_path="/tmp/a1.ogg", media_type="voice", caption=None)

    updated = sm.get_session(12345)
    assert updated is not None
    assert len(updated["clips"]) == 2
    assert updated["clips"][0] == {"path": "/tmp/v1.mp4", "type": "video", "caption": "Clip 1"}
    assert updated["clips"][1] == {"path": "/tmp/a1.ogg", "type": "voice", "caption": None}

    # 3. Set Status and Update Payload
    sm.set_status(12345, "processing")
    sm.update_payload(12345, pipeline_progress=3, draft_markdown="# Test Draft")

    session = sm.get_session(12345)
    assert session["status"] == "processing"
    assert session["pipeline_progress"] == 3
    assert session["draft_markdown"] == "# Test Draft"

    # 4. Pop Session
    popped = sm.pop_session(12345)
    assert popped["status"] == "processing"
    assert sm.is_active(12345) is False
    assert sm.get_session(12345) is None

def test_persistence(tmp_path: Path):
    state_file = tmp_path / "sessions.json"
    sm1 = SessionManager(state_file)
    sm1.start_session(chat_id=999, date_override="2026-07-22")
    sm1.add_clip(chat_id=999, clip_path="/tmp/video.mp4", media_type="video", caption="Vlog")
    sm1.set_status(chat_id=999, status="draft_pending")

    # Load in new SessionManager instance
    sm2 = SessionManager(state_file)
    session = sm2.get_session(999)

    assert session is not None
    assert session["status"] == "draft_pending"
    assert session["entry_date"] == "2026-07-22"
    assert len(session["clips"]) == 1
    assert session["clips"][0]["path"] == "/tmp/video.mp4"

def test_get_pending_reviews(tmp_path: Path):
    state_file = tmp_path / "sessions.json"
    sm = SessionManager(state_file)

    sm.start_session(101)
    sm.start_session(102)
    sm.set_status(102, "draft_pending")
    sm.start_session(103)
    sm.set_status(103, "approved")

    pending = sm.get_pending_reviews()
    assert len(pending) == 1
    assert pending[0][0] == 102
    assert pending[0][1]["status"] == "draft_pending"

def test_get_stale_sessions(tmp_path: Path):
    state_file = tmp_path / "sessions.json"
    sm = SessionManager(state_file)

    sm.start_session(201)
    sm.add_clip(201, "/tmp/clip.mp4", "video")

    # Manually backdate updated_at to 15 hours ago
    past_time = (datetime.now(timezone.utc) - timedelta(hours=15)).isoformat()
    sm._sessions["201"]["updated_at"] = past_time
    sm.save_state()

    stale = sm.get_stale_sessions(timeout_hours=12)
    assert len(stale) == 1
    assert stale[0][0] == 201

def test_get_interrupted_processing(tmp_path: Path):
    state_file = tmp_path / "sessions.json"
    sm = SessionManager(state_file)

    sm.start_session(301)
    sm.set_status(301, "processing")

    interrupted = sm.get_interrupted_processing()
    assert len(interrupted) == 1
    assert interrupted[0][0] == 301
