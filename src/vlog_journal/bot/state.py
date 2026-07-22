import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import structlog

logger = structlog.get_logger(__name__)

class SessionManager:
    """Manages active user recording/processing sessions with JSON file persistence."""

    def __init__(self, state_file: Path | str = "data/sessions.json") -> None:
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, dict[str, Any]] = {}
        self.load_state()

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def load_state(self) -> None:
        """Load session state from JSON file."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    self._sessions = json.load(f)
                logger.info("Loaded sessions from disk", session_count=len(self._sessions), path=str(self.state_file))
            except Exception as e:
                logger.error("Failed to load sessions.json, starting with empty state", error=str(e))
                self._sessions = {}
        else:
            self._sessions = {}

    def save_state(self) -> None:
        """Atomically save session state to JSON file."""
        temp_file = self.state_file.with_suffix(".tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self._sessions, f, indent=2, ensure_ascii=False)
            temp_file.replace(self.state_file)
        except Exception as e:
            logger.error("Failed to save sessions.json", error=str(e))

    def start_session(self, chat_id: int, date_override: str | None = None) -> dict[str, Any]:
        """Start a new recording session for a chat ID."""
        key = str(chat_id)
        now = self._now_iso()

        session = {
            "status": "collecting",  # collecting | processing | draft_pending | approved
            "clips": [],
            "entry_date": date_override,
            "draft_markdown": None,
            "note_schema": None,
            "speaker_map": {},
            "pipeline_progress": 0,
            "created_at": now,
            "updated_at": now,
            "error": None,
        }

        self._sessions[key] = session
        self.save_state()
        logger.info("Session started", chat_id=chat_id, entry_date=date_override)
        return session

    def get_session(self, chat_id: int) -> dict[str, Any] | None:
        """Get copy of active session for chat ID, or None if not found."""
        session = self._sessions.get(str(chat_id))
        return dict(session) if session else None

    def is_active(self, chat_id: int) -> bool:
        """Check if chat ID has an active session."""
        return str(chat_id) in self._sessions

    def add_clip(self, chat_id: int, clip_path: str, media_type: str, caption: str | None = None) -> dict[str, Any]:
        """Add a media clip (video or voice) to the session. Auto-creates session if missing."""
        key = str(chat_id)
        if key not in self._sessions:
            self.start_session(chat_id)

        session = self._sessions[key]
        clip_entry = {
            "path": clip_path,
            "type": media_type,  # video | voice
            "caption": caption,
        }
        session["clips"].append(clip_entry)
        session["updated_at"] = self._now_iso()
        self.save_state()
        logger.info("Clip added to session", chat_id=chat_id, media_type=media_type, total_clips=len(session["clips"]))
        return session

    def set_status(self, chat_id: int, status: str) -> None:
        """Update session status."""
        key = str(chat_id)
        if key in self._sessions:
            self._sessions[key]["status"] = status
            self._sessions[key]["updated_at"] = self._now_iso()
            self.save_state()
            logger.info("Session status updated", chat_id=chat_id, status=status)

    def update_payload(self, chat_id: int, **kwargs: Any) -> None:
        """Update fields on the session dictionary."""
        key = str(chat_id)
        if key in self._sessions:
            session = self._sessions[key]
            for k, v in kwargs.items():
                if k in session:
                    session[k] = v
            session["updated_at"] = self._now_iso()
            self.save_state()
            logger.info("Session payload updated", chat_id=chat_id, updated_keys=list(kwargs.keys()))

    def pop_session(self, chat_id: int) -> dict[str, Any] | None:
        """Remove and return session for chat ID."""
        key = str(chat_id)
        session = self._sessions.pop(key, None)
        if session:
            self.save_state()
            logger.info("Session popped", chat_id=chat_id)
        return session

    def get_pending_reviews(self) -> list[tuple[int, dict[str, Any]]]:
        """Return all sessions currently waiting in draft_pending status."""
        results = []
        for key, session in self._sessions.items():
            if session.get("status") == "draft_pending":
                results.append((int(key), dict(session)))
        return results

    def get_stale_sessions(self, timeout_hours: int = 12) -> list[tuple[int, dict[str, Any]]]:
        """Return all collecting sessions that have not been updated for > timeout_hours."""
        results = []
        now = datetime.now(timezone.utc)
        for key, session in self._sessions.items():
            if session.get("status") == "collecting":
                updated_at_str = session.get("updated_at")
                if updated_at_str:
                    try:
                        updated_at = datetime.fromisoformat(updated_at_str)
                        if updated_at.tzinfo is None:
                            updated_at = updated_at.replace(tzinfo=timezone.utc)
                        elapsed_hours = (now - updated_at).total_seconds() / 3600.0
                        if elapsed_hours >= timeout_hours:
                            results.append((int(key), dict(session)))
                    except ValueError:
                        pass
        return results

    def get_interrupted_processing(self) -> list[tuple[int, dict[str, Any]]]:
        """Return all sessions that were in processing status when bot crashed/restarted."""
        results = []
        for key, session in self._sessions.items():
            if session.get("status") == "processing":
                results.append((int(key), dict(session)))
        return results
