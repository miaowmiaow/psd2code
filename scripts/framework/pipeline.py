"""Pipeline: executes a sequence of Stage objects over a PipelineContext.

Each stage is observed by ctx.hook (Observer pattern):
  on_pipeline_start → [on_stage_start → run → on_stage_end]* → on_pipeline_end
If a stage raises, on_error is invoked and the exception re-raised.
"""

from __future__ import annotations

import time
from typing import Iterable

from .context import PipelineContext
from .stage import Stage


class Pipeline:
    def __init__(self, stages: Iterable[Stage] | None = None) -> None:
        self._stages: list[Stage] = list(stages) if stages else []

    def add(self, stage: Stage) -> "Pipeline":
        self._stages.append(stage)
        return self

    def extend(self, stages: Iterable[Stage]) -> "Pipeline":
        self._stages.extend(stages)
        return self

    @property
    def stages(self) -> list[Stage]:
        return list(self._stages)

    def run(self, ctx: PipelineContext) -> PipelineContext:
        hook = ctx.hook
        hook.on_pipeline_start(ctx)
        try:
            for stage in self._stages:
                ctx.log(f"stage: {stage.name} ...")
                hook.on_stage_start(stage, ctx)
                t0 = time.perf_counter()
                try:
                    ctx = stage.run(ctx)
                except BaseException as err:
                    hook.on_error(stage, ctx, err)
                    raise
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                hook.on_stage_end(stage, ctx, elapsed_ms)
        finally:
            hook.on_pipeline_end(ctx)
        return ctx
