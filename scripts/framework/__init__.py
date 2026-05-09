"""Generic pipeline abstractions: Stage / Context / Pipeline / Hooks."""

from .context import PipelineContext
from .stage import Stage
from .pipeline import Pipeline
from .hooks import PipelineHook, NullHook, LoggingHook

__all__ = [
    "PipelineContext",
    "Stage",
    "Pipeline",
    "PipelineHook",
    "NullHook",
    "LoggingHook",
]
