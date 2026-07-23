from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
import structlog

from vlog_journal.pipeline.registry import PipelineContext, register_step
from vlog_journal.vault.tags import generate_tags

logger = structlog.get_logger(__name__)

def get_jinja_env(template_dir: Path | str = "templates") -> Environment:
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )

@register_step("vault.render_markdown")
async def render_markdown(ctx: PipelineContext) -> PipelineContext:
    """Render structured session data and enrichment stats into Jinja2 Markdown."""
    note = ctx.payload.get("note_schema", {})
    media_stats = ctx.payload.get("media_stats", {})
    is_voice_memo = ctx.payload.get("is_voice_memo", False)
    entry_date = ctx.payload.get("entry_date", datetime.now().strftime("%Y-%m-%d"))

    media_stats = dict(ctx.payload.get("media_stats") or {})
    if "languages_detected" not in media_stats:
        media_stats["languages_detected"] = {"en": 100}

    # Time and Day of week
    dt = datetime.strptime(entry_date, "%Y-%m-%d")
    day_of_week = dt.strftime("%A")
    entry_time = ctx.payload.get("entry_time", datetime.now().strftime("%H:%M"))

    # Generate Tags
    people = note.get("people", [])
    topics = note.get("topics", [])
    primary_location = ctx.payload.get("primary_location")
    category = note.get("category", "reflection")

    tags = generate_tags(
        people=people,
        topics=topics,
        primary_location=primary_location,
        category=category,
        is_voice_memo=is_voice_memo,
    )
    ctx.payload["tags"] = tags

    # Media filename (placeholder until storage step resolves collision suffix)
    ext = ".mp3" if is_voice_memo else ".mp4"
    media_filename = ctx.payload.get("media_filename", f"{entry_date}{ext}")

    # Build full render context
    context: dict[str, Any] = {
        "entry_date": entry_date,
        "entry_time": entry_time,
        "day_of_week": day_of_week,
        "is_voice_memo": is_voice_memo,
        "media_filename": media_filename,
        "title": note.get("title", "Untitled Journal Entry"),
        "summary": note.get("summary", ""),
        "mood": note.get("mood", "neutral"),
        "energy_level": note.get("energy_level", "medium"),
        "category": category,
        "people": people,
        "topics": topics,
        "locations_mentioned": note.get("locations_mentioned", []),
        "key_highlights": note.get("key_highlights", []),
        "action_items": note.get("action_items", []),
        "questions_raised": note.get("questions_raised", []),
        "gratitude": note.get("gratitude", []),
        "concerns": note.get("concerns", []),
        "notable_quotes": note.get("notable_quotes", []),
        "media_mentioned": note.get("media_mentioned", []),
        "food_and_drink": note.get("food_and_drink", []),
        "plans_future": note.get("plans_future", []),
        "references_past": note.get("references_past", []),
        "dreams": note.get("dreams", []),
        "creative_ideas": note.get("creative_ideas", []),
        "learning": note.get("learning", []),
        "financial_mentions": note.get("financial_mentions", []),
        "social_quality": note.get("social_quality"),
        "health": note.get("health", {}),
        "primary_location": primary_location,
        "primary_weather": ctx.payload.get("primary_weather"),
        "locations_visited": ctx.payload.get("locations_visited", []),
        "media_stats": media_stats,
        "tags": tags,
        "cleaned_transcript": note.get("cleaned_transcript", ctx.payload.get("labeled_segments", [])),
    }

    env = get_jinja_env()
    template_name = "voice_note.md.j2" if is_voice_memo else "vlog_note.md.j2"
    template = env.get_template(template_name)

    rendered_md = template.render(**context)
    ctx.payload["draft_markdown"] = rendered_md
    logger.info("Markdown rendered successfully", template=template_name, title=note.get("title"))
    return ctx
