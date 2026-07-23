from datetime import datetime, timedelta

from vlog_journal.bot.review import (
    MAX_TELEGRAM_MSG_LEN,
    build_edit_keyboard,
    build_review_keyboard,
    build_review_message,
    parse_date_input,
    parse_speaker_map_input,
)


def test_parse_speaker_map_input():
    # Comma separated
    res1 = parse_speaker_map_input("Speaker 1 = Me, Speaker 2 = Mom")
    assert res1 == {"Speaker 1": "Me", "Speaker 2": "Mom"}

    # Colon separated
    res2 = parse_speaker_map_input("Speaker 1: Alice, Speaker 2: Bob")
    assert res2 == {"Speaker 1": "Alice", "Speaker 2": "Bob"}

    # Multi-line
    res3 = parse_speaker_map_input("Speaker 1 = John\nSpeaker 2 = Sarah")
    assert res3 == {"Speaker 1": "John", "Speaker 2": "Sarah"}

    # Empty / invalid
    assert parse_speaker_map_input("") == {}
    assert parse_speaker_map_input("Hello world") == {}


def test_parse_date_input():
    today_str = datetime.now().strftime("%Y-%m-%d")
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    assert parse_date_input("2026-07-20") == "2026-07-20"
    assert parse_date_input("today") == today_str
    assert parse_date_input("сегодня") == today_str
    assert parse_date_input("yesterday") == yesterday_str
    assert parse_date_input("вчера") == yesterday_str
    assert parse_date_input("not-a-date") is None
    assert parse_date_input("") is None


def test_build_keyboards():
    review_kb = build_review_keyboard()
    assert len(review_kb.keyboard[0]) == 3
    callbacks = [btn.callback_data for btn in review_kb.keyboard[0]]
    assert "review_approve" in callbacks
    assert "review_edit_menu" in callbacks
    assert "review_discard" in callbacks

    edit_kb = build_edit_keyboard()
    edit_callbacks = [
        btn.callback_data
        for row in edit_kb.keyboard
        for btn in row
    ]
    assert "edit_speakers" in edit_callbacks
    assert "edit_date" in edit_callbacks
    assert "edit_prompt" in edit_callbacks
    assert "review_back" in edit_callbacks


def test_build_review_message_standard():
    session = {
        "entry_date": "2026-07-20",
        "note_schema": {
            "title": "Evening cooking",
            "summary": "Cooked dinner with family.",
            "mood": "relaxed",
            "energy_level": "medium",
            "key_highlights": ["Roasted beets", "Discussed recipes"],
            "action_items": ["Buy beets on Saturday"],
        },
        "speaker_map": {"Speaker 1": "Me", "Speaker 2": "Mom"},
        "labeled_segments": [
            {"speaker": "Speaker 1", "text": "Hi"},
            {"speaker": "Speaker 2", "text": "Hello"},
        ],
        "media_stats": {
            "duration": "00:05:00",
            "word_count": 450,
        },
    }

    text, kb = build_review_message(session)
    assert "Evening cooking" in text
    assert "relaxed" in text
    assert "Cooked dinner with family." in text
    assert "Speaker 1` ➔ **Me**" in text
    assert "Speaker 2` ➔ **Mom**" in text
    assert len(kb.keyboard[0]) == 3


def test_build_review_message_truncation():
    long_summary = "A" * 5000
    session = {
        "entry_date": "2026-07-20",
        "note_schema": {
            "title": "Very long note",
            "summary": long_summary,
            "mood": "focused",
            "energy_level": "high",
            "key_highlights": ["Long text"],
        },
        "speaker_map": {},
        "labeled_segments": [],
        "media_stats": {},
    }

    text, _ = build_review_message(session)
    assert len(text) <= MAX_TELEGRAM_MSG_LEN
    assert "Preview truncated for Telegram limits" in text
