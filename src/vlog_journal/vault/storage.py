import re
import shutil
from pathlib import Path

import structlog

from vlog_journal.pipeline.registry import PipelineContext, register_step

logger = structlog.get_logger(__name__)

def resolve_collision_filename(
    journal_dir: Path, date_str: str, ext: str = ".mp4"
) -> tuple[str, str]:
    """Resolve same-day collision suffix per architecture Section 6.1.
    
    Returns tuple of (stem_name, media_filename).
    e.g. ("2026-07-20", "2026-07-20.mp4") or ("2026-07-20-02", "2026-07-20-02.mp4")
    """
    stem = date_str
    md_path = journal_dir / f"{stem}.md"
    if not md_path.exists():
        return stem, f"{stem}{ext}"

    counter = 2
    while True:
        stem = f"{date_str}-{counter:02d}"
        md_path = journal_dir / f"{stem}.md"
        if not md_path.exists():
            return stem, f"{stem}{ext}"
        counter += 1

@register_step("vault.save_entry")
async def save_entry(ctx: PipelineContext) -> PipelineContext:
    """Save rendered Markdown and move media file to Obsidian vault with collision protection."""
    draft_markdown = ctx.payload.get("draft_markdown")
    if not draft_markdown:
        raise ValueError("draft_markdown missing from context payload")

    is_voice_memo = ctx.payload.get("is_voice_memo", False)
    raw_video_path = ctx.payload.get("raw_video_path")
    raw_audio_path = ctx.payload.get("raw_audio_path")

    source_media = raw_video_path if (raw_video_path and Path(raw_video_path).exists()) else raw_audio_path

    # Read Vault paths from config
    app_cfg = getattr(ctx.config, "app", None) if ctx.config else None
    vault_path = Path(getattr(app_cfg, "vault_path", "data/vault")) if app_cfg else Path("data/vault")
    vlogs_rel = getattr(app_cfg, "vlogs_relative_path", "Journal/Vlogs") if app_cfg else "Journal/Vlogs"
    media_rel = getattr(app_cfg, "media_relative_path", "Attachments/Vlogs") if app_cfg else "Attachments/Vlogs"

    journal_dir = vault_path / vlogs_rel
    attachments_dir = vault_path / media_rel
    journal_dir.mkdir(parents=True, exist_ok=True)
    attachments_dir.mkdir(parents=True, exist_ok=True)

    entry_date = ctx.payload.get("entry_date", "2026-07-20")
    media_ext = ".mp3" if is_voice_memo else ".mp4"

    stem_name, media_filename = resolve_collision_filename(journal_dir, entry_date, media_ext)

    # Move media file to vault attachments
    dest_media_path = attachments_dir / media_filename
    if source_media and Path(source_media).exists():
        shutil.copy2(source_media, dest_media_path)
        logger.info("Media file saved to vault", destination=str(dest_media_path))
    else:
        logger.warning("Source media file not found for vault save", source_media=source_media)

    # Update wikilink in markdown to point to actual media filename
    updated_markdown = re.sub(
        r"!\[\[.*?\]\]",
        f"![[{media_filename}]]",
        draft_markdown,
        count=1,
    )

    dest_md_path = journal_dir / f"{stem_name}.md"
    dest_md_path.write_text(updated_markdown, encoding="utf-8")
    logger.info("Markdown note saved to vault", destination=str(dest_md_path))

    ctx.payload["final_markdown_path"] = str(dest_md_path)
    ctx.payload["final_media_path"] = str(dest_media_path)
    return ctx
