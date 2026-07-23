import json
import re
from pathlib import Path

import structlog

from vlog_journal.pipeline.registry import PipelineContext, register_step

logger = structlog.get_logger(__name__)

def slugify(text: str) -> str:
    """Convert text into a URL/tag friendly slug: lowercase, replace spaces/underscores with hyphens."""
    if not text:
        return ""
    s = text.lower().strip()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^\w\-]", "", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")

def generate_tags(
    people: list[str],
    topics: list[str],
    primary_location: str | None,
    category: str,
    is_voice_memo: bool = False,
) -> list[str]:
    """Generate hierarchical Obsidian tags based on NoteSchema and enrichment fields."""
    tags = []
    base_tag = "journal/voice" if is_voice_memo else "journal/vlog"
    tags.append(base_tag)

    if category:
        c_slug = slugify(category)
        if c_slug:
            tags.append(f"category/{c_slug}")

    for p in people:
        p_slug = slugify(p)
        if p_slug:
            tags.append(f"people/{p_slug}")

    for t in topics:
        t_slug = slugify(t)
        if t_slug:
            tags.append(f"topic/{t_slug}")

    if primary_location:
        city_part = primary_location.split(",")[0].strip()
        l_slug = slugify(city_part)
        if l_slug:
            tags.append(f"location/{l_slug}")

    # Remove duplicates while preserving order
    seen = set()
    unique_tags = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            unique_tags.append(tag)

    return unique_tags

class TagManager:
    """Manages vault tag caching, write-through additions, and vault reconciliation."""

    def __init__(self, cache_file: Path | str = "data/tags.json"):
        self.cache_file = Path(cache_file)

    def load(self) -> list[str]:
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return sorted(list(set(data)))
            except Exception as e:
                logger.warning("Failed to load tag cache", cache_file=str(self.cache_file), error=str(e))
        return []

    def get_tags(self) -> list[str]:
        return self.load()

    def add_tags(self, new_tags: list[str]) -> list[str]:
        existing = set(self.load())
        updated = sorted(list(existing.union(set(new_tags))))
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(updated, f, indent=2, ensure_ascii=False)
            logger.info("Updated tag cache", count=len(updated), new_added=len(new_tags))
        except Exception as e:
            logger.error("Failed to write tag cache", cache_file=str(self.cache_file), error=str(e))
        return updated

    def sync_from_vault(self, vault_path: Path | str) -> list[str]:
        """Scan ALL .md files under vault_path, parse YAML frontmatter tags, update tags.json."""
        v_path = Path(vault_path)
        if not v_path.exists():
            logger.warning("Vault path does not exist for sync", vault_path=str(v_path))
            return self.get_tags()

        found_tags: set[str] = set()

        for md_file in v_path.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8", errors="replace")
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        yaml_block = parts[1]
                        # Extract tags section
                        in_tags = False
                        for line in yaml_block.splitlines():
                            line_str = line.strip()
                            if line_str.startswith("tags:"):
                                in_tags = True
                                continue
                            if in_tags:
                                if line_str.startswith("-"):
                                    tag = line_str.lstrip("-").strip()
                                    if tag:
                                        found_tags.add(tag)
                                elif line_str and not line_str.startswith("#"):
                                    in_tags = False
            except Exception as e:
                logger.warning("Failed to parse tags from markdown file", file=str(md_file), error=str(e))

        sorted_tags = sorted(list(found_tags))
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(sorted_tags, f, indent=2, ensure_ascii=False)
            logger.info("Reconciled tag cache from vault scan", total_tags=len(sorted_tags))
        except Exception as e:
            logger.error("Failed to write reconciled tag cache", error=str(e))

        return sorted_tags

@register_step("vault.update_tag_cache")
async def update_tag_cache(ctx: PipelineContext) -> PipelineContext:
    """Pipeline step to add note's tags to tag cache."""
    tags = ctx.payload.get("tags", [])
    cache_file = (
        getattr(getattr(ctx.config, "app", None), "tags_cache_file", "data/tags.json")
        if ctx.config
        else "data/tags.json"
    )
    tm = TagManager(cache_file=cache_file)
    tm.add_tags(tags)
    return ctx
