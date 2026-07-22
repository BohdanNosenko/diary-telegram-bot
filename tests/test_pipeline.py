import pytest
from unittest.mock import MagicMock
from vlog_journal.pipeline.registry import PipelineContext, register_step
from vlog_journal.pipeline.runner import run_pipeline, run_pipeline_from

@pytest.fixture
def mock_config():
    return MagicMock()

@pytest.mark.asyncio
async def test_pipeline_execution(mock_config):
    @register_step("test.step_one")
    async def step_one(ctx: PipelineContext) -> PipelineContext:
        ctx.set("one", 1)
        return ctx

    @register_step("test.step_two")
    async def step_two(ctx: PipelineContext) -> PipelineContext:
        ctx.set("two", ctx.get("one") + 1)
        return ctx

    ctx = PipelineContext(chat_id=123, config=mock_config)
    result = await run_pipeline(["test.step_one", "test.step_two"], ctx)
    
    assert result.get("one") == 1
    assert result.get("two") == 2
    assert result.pipeline_progress == 2

@pytest.mark.asyncio
async def test_missing_step_raises(mock_config):
    ctx = PipelineContext(chat_id=123, config=mock_config)
    with pytest.raises(KeyError, match="not found"):
        await run_pipeline(["nonexistent.step"], ctx)

@pytest.mark.asyncio
async def test_pipeline_failure_propagates(mock_config):
    @register_step("test.fail")
    async def fail_step(ctx: PipelineContext) -> PipelineContext:
        raise ValueError("Boom")

    ctx = PipelineContext(chat_id=123, config=mock_config)
    with pytest.raises(ValueError, match="Boom"):
        await run_pipeline(["test.fail"], ctx)
        
    assert ctx.payload["error"] == "Boom"
    assert ctx.payload["failed_step"] == "test.fail"
    assert ctx.pipeline_progress == 0

@pytest.mark.asyncio
async def test_run_pipeline_from(mock_config):
    @register_step("test.step_skip")
    async def step_skip(ctx: PipelineContext) -> PipelineContext:
        ctx.set("skipped", False)
        return ctx

    @register_step("test.step_resume")
    async def step_resume(ctx: PipelineContext) -> PipelineContext:
        ctx.set("resumed", True)
        return ctx

    ctx = PipelineContext(chat_id=123, config=mock_config)
    result = await run_pipeline_from(["test.step_skip", "test.step_resume"], ctx, 1)
    
    assert result.get("skipped") is None
    assert result.get("resumed") is True
