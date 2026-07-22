from typing import Callable, Any, Awaitable
from dataclasses import dataclass, field
import structlog
from vlog_journal.config import AppSettings

logger = structlog.get_logger(__name__)

@dataclass
class PipelineContext:
    chat_id: int
    config: AppSettings
    session_date: str | None = None
    input_files: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    pipeline_progress: int = 0
    
    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)
        
    def set(self, key: str, value: Any) -> None:
        self.payload[key] = value

    async def notify(self, message: str) -> None:
        logger.info("notify", chat_id=self.chat_id, message=message)

PipelineStep = Callable[[PipelineContext], Awaitable[PipelineContext]]

_REGISTRY: dict[str, PipelineStep] = {}

def register_step(name: str):
    def decorator(func: PipelineStep):
        if name in _REGISTRY:
            logger.warning("Overwriting pipeline step", step=name)
        _REGISTRY[name] = func
        return func
    return decorator

def get_step(name: str) -> PipelineStep:
    if name not in _REGISTRY:
        raise KeyError(f"Pipeline step not found: {name}")
    return _REGISTRY[name]
