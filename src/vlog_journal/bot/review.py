import re
from datetime import datetime, timedelta
from typing import Any
import structlog
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = structlog.get_logger(__name__)

MAX_TELEGRAM_MSG_LEN = 4000

def parse_speaker_map_input(text: str) -> dict[str, str]:
    """Parse speaker map input string into a dictionary.
    
    Supported formats:
    - Speaker 1 = Me, Speaker 2 = Mom
    - Speaker 1: Me, Speaker 2: Mom
    - Speaker 1 = Me
      Speaker 2 = Mom
    """
    if not text:
        return {}

    speaker_map = {}
    # Split by comma or newline
    lines = [line.strip() for line in re.split(r"[\n,]", text) if line.strip()]
    
    pattern = r"^(Speaker\s*\d+)\s*[:=]\s*(.+)$"
    for line in lines:
        match = re.match(pattern, line, re.IGNORECASE)
        if match:
            num_match = re.search(r"\d+", match.group(1))
            spk_num = num_match.group(0) if num_match else "1"
            spk_key = f"Speaker {spk_num}"
            spk_val = match.group(2).strip()
            if spk_val:
                speaker_map[spk_key] = spk_val

    return speaker_map

def parse_date_input(text: str) -> str | None:
    """Parse date override text into YYYY-MM-DD string."""
    if not text:
        return None

    cleaned = text.strip().lower()
    today = datetime.now()

    if cleaned in ("today", "сегодня"):
        return today.strftime("%Y-%m-%d")
    elif cleaned in ("yesterday", "вчера"):
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")

    # Match YYYY-MM-DD
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", cleaned)
    if match:
        raw_date = match.group(1)
        try:
            datetime.strptime(raw_date, "%Y-%m-%d")
            return raw_date
        except ValueError:
            pass

    return None

def build_review_keyboard() -> InlineKeyboardMarkup:
    """Build main review inline keyboard with Approve, Edit, Discard."""
    keyboard = InlineKeyboardMarkup(row_width=3)
    keyboard.add(
        InlineKeyboardButton("✅ Approve", callback_data="review_approve"),
        InlineKeyboardButton("✏️ Edit", callback_data="review_edit_menu"),
        InlineKeyboardButton("❌ Discard", callback_data="review_discard"),
    )
    return keyboard

def build_edit_keyboard() -> InlineKeyboardMarkup:
    """Build edit sub-menu inline keyboard."""
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🗣️ Label Speakers", callback_data="edit_speakers"),
        InlineKeyboardButton("📅 Change Date", callback_data="edit_date"),
    )
    keyboard.add(
        InlineKeyboardButton("✍️ Free-text Correction", callback_data="edit_prompt"),
        InlineKeyboardButton("⬅️ Back", callback_data="review_back"),
    )
    return keyboard

def build_review_message(session_data: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    """Format session draft note into a Telegram-friendly preview message.
    
    Returns (formatted_text, inline_keyboard).
    """
    schema = session_data.get("note_schema") or {}
    entry_date = session_data.get("entry_date") or "Today"
    media_stats = session_data.get("media_stats") or {}
    speaker_map = session_data.get("speaker_map") or {}

    title = schema.get("title") or "Vlog Note"
    summary = schema.get("summary") or "No summary available."
    mood = schema.get("mood") or "N/A"
    energy = schema.get("energy_level") or "N/A"
    highlights = schema.get("key_highlights") or []
    action_items = schema.get("action_items") or []

    # Format speakers
    labeled_segments = session_data.get("labeled_segments") or []
    raw_speakers = sorted(list(set(seg.get("speaker", "Speaker 1") for seg in labeled_segments)))
    if not raw_speakers:
        raw_speakers = ["Speaker 1"]

    speaker_lines = []
    for spk in raw_speakers:
        mapped_name = speaker_map.get(spk)
        if mapped_name:
            speaker_lines.append(f"• `{spk}` ➔ **{mapped_name}**")
        else:
            speaker_lines.append(f"• `{spk}` *(unlabeled)*")

    speakers_str = "\n".join(speaker_lines)

    # Format highlights
    highlights_str = "\n".join(f"• {h}" for h in highlights[:5]) if highlights else "None"
    
    # Format action items
    actions_str = "\n".join(f"• [ ] {a}" for a in action_items[:3]) if action_items else "None"

    # Assemble text
    text_blocks = [
        "📝 **Vlog Journal Draft Review**\n",
        f"📅 **Date:** `{entry_date}`",
        f"🎬 **Title:** {title}",
        f"🎭 **Mood / Energy:** `{mood}` / `{energy}`\n",
        f"💡 **Summary:**\n_{summary.strip()}_\n",
        f"🌟 **Key Highlights:**\n{highlights_str}\n",
    ]

    if action_items:
        text_blocks.append(f"✅ **Action Items:**\n{actions_str}\n")

    text_blocks.append(f"🗣️ **Speakers Identified:**\n{speakers_str}\n")

    if media_stats.get("duration"):
        text_blocks.append(
            f"⏱️ **Duration:** `{media_stats['duration']}` | **Words:** `{media_stats.get('word_count', 0)}`"
        )

    full_text = "\n".join(text_blocks)

    # Message truncation guard
    if len(full_text) > MAX_TELEGRAM_MSG_LEN:
        full_text = full_text[: MAX_TELEGRAM_MSG_LEN - 50] + "\n\n⚠️ _(Preview truncated for Telegram limits)_"

    return full_text, build_review_keyboard()
