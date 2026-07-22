import json
import os
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

from vlog_journal.pipeline.registry import PipelineContext, register_step
from vlog_journal.processors.schemas import NoteSchema

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT_TEMPLATE = """You are an expert AI assistant that processes raw personal vlog transcripts into structured Obsidian journal notes.

### Language Policy
1. `title`: English (5-10 words, scannable).
2. `summary`: 2-3 sentences in the MAIN SPOKEN LANGUAGE of the transcript (e.g. Russian if spoken in Russian).
3. `mood`, `energy_level`, `category`: English.
4. `people`: English transliterations (e.g. "Mom" instead of "Мама", "Dima" instead of "Дима").
5. `topics`, `locations_mentioned`, `key_highlights`: English.
6. `notable_quotes`: In original spoken language verbatim.
7. `cleaned_transcript`: Verbatim in original spoken language(s) with correct speaker assignments.
8. `health`: Extract health/wellness info (sleep, exercise, pain, symptoms, mental state, nutrition) ONLY if explicitly mentioned.

### Output Instructions
- Return a valid JSON object matching NoteSchema.
- For Tier 2 fields (action_items, questions_raised, gratitude, concerns, etc.), return empty lists [] if not mentioned in the transcript.
{tag_context}
{caption_context}
{speaker_context}
"""

def load_tag_cache(tags_cache_file: Path | str = "data/tags.json") -> list[str]:
    p = Path(tags_cache_file)
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                tags = json.load(f)
                if isinstance(tags, list):
                    return tags
        except Exception:
            pass
    return []

def build_prompt_contexts(ctx: PipelineContext) -> dict[str, str]:
    # 1. Tag context
    tags_file = ctx.config.app.tags_cache_file if ctx.config else "data/tags.json"
    tags = load_tag_cache(tags_file)
    tag_str = f"### Existing Vault Tags for Consistency\nRe-use existing tags where applicable: {', '.join(tags)}" if tags else ""

    # 2. Caption context
    clips = ctx.payload.get("clips", [])
    captions = [f"- Clip {i+1}: '{c.get('caption')}'" for i, c in enumerate(clips) if c.get("caption")]
    caption_str = "### Clip Context from User Captions\n" + "\n".join(captions) if captions else ""

    # 3. Speaker map context
    speaker_map = ctx.payload.get("speaker_map", {})
    speaker_str = "### User Speaker Mapping\n" + "\n".join([f"- {k} -> {v}" for k, v in speaker_map.items()]) if speaker_map else ""

    return {
        "tag_context": tag_str,
        "caption_context": caption_str,
        "speaker_context": speaker_str,
    }

def format_transcript_block(labeled_segments: list[dict[str, Any]]) -> str:
    lines = []
    for seg in labeled_segments:
        spk = seg.get("speaker", "Speaker 1")
        ts = seg.get("timestamp", "00:00")
        txt = seg.get("text", "")
        lines.append(f"[{spk}] ({ts}): {txt}")
    return "\n".join(lines)

async def _call_litellm_structured(
    model: str,
    messages: list[dict[str, str]],
    api_base: str | None = None,
    temperature: float = 0.3,
    timeout: int = 120,
) -> dict[str, Any]:
    from litellm import acompletion

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "timeout": timeout,
        "response_format": NoteSchema,
    }
    if api_base and "ollama" in model:
        kwargs["api_base"] = api_base

    response = await acompletion(**kwargs)
    content = response.choices[0].message.content

    if isinstance(content, str):
        content = json.loads(content)
    elif hasattr(content, "model_dump"):
        content = content.model_dump()

    return content

@register_step("llm.structure_transcript")
async def structure_transcript(ctx: PipelineContext) -> PipelineContext:
    """Structure transcript into Pydantic NoteSchema using LiteLLM with fallback."""
    labeled_segments = ctx.payload.get("labeled_segments", [])
    if not labeled_segments:
        logger.warning("No labeled_segments to structure in pipeline context")
        # Provide empty fallback NoteSchema
        dummy_schema = NoteSchema(
            title="Empty Session Note",
            summary="No transcript audio recorded.",
            mood="neutral",
            energy_level="medium",
            category="reflection",
            key_highlights=["No speech recorded"],
        )
        ctx.payload["note_schema"] = dummy_schema.model_dump()
        return ctx

    transcript_text = format_transcript_block(labeled_segments)
    prompt_ctx = build_prompt_contexts(ctx)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(**prompt_ctx)

    user_prompt = f"### Raw Transcript\n{transcript_text}\n\nPlease extract and structure into NoteSchema JSON."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # LLM Settings
    llm_cfg = getattr(ctx.config, "llm", None) if ctx.config else None
    primary_provider = getattr(llm_cfg, "provider", "ollama/qwen2.5:14b-q3_K_M") if llm_cfg else "ollama/qwen2.5:14b-q3_K_M"
    api_base = getattr(llm_cfg, "api_base", "http://localhost:11434") if llm_cfg else "http://localhost:11434"
    temperature = getattr(llm_cfg, "temperature", 0.3) if llm_cfg else 0.3
    timeout = getattr(llm_cfg, "timeout", 120) if llm_cfg else 120

    fallback_cfg = getattr(llm_cfg, "fallback", None) if llm_cfg else None
    fallback_provider = getattr(fallback_cfg, "provider", "gemini/gemini-2.5-flash") if fallback_cfg else "gemini/gemini-2.5-flash"

    chosen_model = primary_provider
    ctx.payload["llm_fallback_used"] = False
    raw_response_dict = None

    logger.info("Calling primary LLM provider", model=primary_provider, api_base=api_base)

    try:
        raw_response_dict = await _call_litellm_structured(
            model=primary_provider,
            messages=messages,
            api_base=api_base,
            temperature=temperature,
            timeout=timeout,
        )
    except Exception as e:
        logger.warning("Primary LLM call failed", provider=primary_provider, error=str(e))
        if fallback_provider:
            logger.info("Attempting fallback LLM provider", fallback_provider=fallback_provider)
            try:
                # Ensure GEMINI_API_KEY is available in env if using gemini
                if "gemini" in fallback_provider and ctx.config and getattr(ctx.config, "gemini_api_key", None):
                    os.environ["GEMINI_API_KEY"] = ctx.config.gemini_api_key.get_secret_value()

                raw_response_dict = await _call_litellm_structured(
                    model=fallback_provider,
                    messages=messages,
                    temperature=getattr(fallback_cfg, "temperature", 0.3) if fallback_cfg else 0.3,
                    timeout=timeout,
                )
                chosen_model = fallback_provider
                ctx.payload["llm_fallback_used"] = True
                logger.info("Fallback LLM succeeded", model=fallback_provider)
            except Exception as fb_err:
                logger.error("Fallback LLM provider also failed", error=str(fb_err))
                raise RuntimeError(f"Both primary ({primary_provider}) and fallback ({fallback_provider}) LLM calls failed: {fb_err}")
        else:
            raise RuntimeError(f"Primary LLM call ({primary_provider}) failed and no fallback is configured: {e}")

    # Validate against NoteSchema with 1 retry on Pydantic validation error
    try:
        note_schema = NoteSchema(**raw_response_dict)
    except ValidationError as val_err:
        logger.warning("NoteSchema validation failed, attempting 1 retry with feedback", error=str(val_err))
        retry_user_prompt = f"{user_prompt}\n\nPrevious JSON output was invalid:\n{val_err}\n\nPlease fix the JSON and return valid NoteSchema."
        retry_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": retry_user_prompt},
        ]
        raw_response_dict = await _call_litellm_structured(
            model=chosen_model,
            messages=retry_messages,
            api_base=api_base if "ollama" in chosen_model else None,
            temperature=temperature,
            timeout=timeout,
        )
        note_schema = NoteSchema(**raw_response_dict)

    ctx.payload["note_schema"] = note_schema.model_dump()
    ctx.payload["llm_model"] = chosen_model
    logger.info("NoteSchema structured successfully", title=note_schema.title, model=chosen_model)
    return ctx
