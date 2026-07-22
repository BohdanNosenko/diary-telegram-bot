import structlog
import time
from vlog_journal.pipeline.registry import PipelineContext, get_step

logger = structlog.get_logger(__name__)

async def run_pipeline(steps: list[str], ctx: PipelineContext) -> PipelineContext:
    return await run_pipeline_from(steps, ctx, 0)

async def run_pipeline_from(steps: list[str], ctx: PipelineContext, start_index: int) -> PipelineContext:
    logger.info("pipeline.started", total_steps=len(steps), start_index=start_index, chat_id=ctx.chat_id)
    
    for i in range(start_index, len(steps)):
        step_name = steps[i]
        ctx.pipeline_progress = i
        await ctx.notify(f"Running {step_name}...")
        
        logger.info("pipeline.step_start", step=step_name, chat_id=ctx.chat_id)
        start_t = time.time()
        
        try:
            step_fn = get_step(step_name)
            ctx = await step_fn(ctx)
            duration = time.time() - start_t
            logger.info("pipeline.step_done", step=step_name, duration=f"{duration:.2f}s")
        except Exception as e:
            ctx.payload["error"] = str(e)
            ctx.payload["failed_step"] = step_name
            logger.error("pipeline.step_failed", step=step_name, error=str(e), exc_info=True)
            raise e
            
    ctx.pipeline_progress = len(steps)
    logger.info("pipeline.completed", chat_id=ctx.chat_id)
    return ctx
