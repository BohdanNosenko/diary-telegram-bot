from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from vlog_journal.pipeline.registry import PipelineContext
from vlog_journal.vault.tags import TagManager


@pytest.mark.asyncio
async def test_sync_tags_command(tmp_path):
    vault_dir = tmp_path / "Vault"
    vault_dir.mkdir()
    note_file = vault_dir / "2026-07-22.md"
    note_file.write_text("---\ntags:\n  - journal/vlog\n  - people/mom\n---\nBody content", encoding="utf-8")

    tags_cache_file = tmp_path / "tags.json"
    tag_mgr = TagManager(tags_cache_file)
    tags = tag_mgr.sync_from_vault(vault_dir)

    assert "journal/vlog" in tags
    assert "people/mom" in tags
    assert len(tags) == 2


@pytest.mark.asyncio
async def test_retry_pipeline_step_resumes(tmp_path):
    mock_config = MagicMock()
    ctx = PipelineContext(chat_id=123, config=mock_config)
    ctx.payload = {"step1_done": True}
    ctx.pipeline_progress = 1

    executed_steps = []

    async def mock_step_2(c: PipelineContext) -> PipelineContext:
        executed_steps.append("step2")
        c.payload["step2_done"] = True
        return c

    steps = ["step1", "step2"]

    with patch("vlog_journal.pipeline.runner.get_step", return_value=mock_step_2):
        from vlog_journal.pipeline.runner import run_pipeline_from
        res_ctx = await run_pipeline_from(steps, ctx, start_index=1)

    assert executed_steps == ["step2"]
    assert res_ctx.payload.get("step2_done") is True
    assert res_ctx.pipeline_progress == 2


@pytest.mark.asyncio
async def test_status_command_formatting():
    mock_session_mgr = MagicMock()
    mock_session_mgr.get_session.return_value = {
        "status": "draft_pending",
        "clips": [{"path": "c1.mp4"}],
        "pipeline_progress": 5,
        "error": None,
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"models": [{"name": "qwen2.5:14b"}]}

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)), \
         patch("shutil.disk_usage", return_value=MagicMock(free=50 * 1024**3, total=100 * 1024**3)), \
         patch("shutil.which", return_value="/usr/bin/rclone"):
        pass  # Health queries succeed cleanly
