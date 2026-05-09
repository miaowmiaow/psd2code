"""PipelineHook: Observer 模式，在 stage 前后/出错时接收通知。

内置两种实现：
- NullHook   —— 默认，不做任何事
- LoggingHook —— 打印进度与耗时
"""

from __future__ import annotations

import time
from abc import ABC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import PipelineContext
    from .stage import Stage


class PipelineHook(ABC):
    """流水线观察者。子类覆盖感兴趣的钩子即可，缺省为 no-op。"""

    def on_pipeline_start(self, ctx: "PipelineContext") -> None: ...
    def on_pipeline_end(self, ctx: "PipelineContext") -> None: ...
    def on_stage_start(self, stage: "Stage", ctx: "PipelineContext") -> None: ...
    def on_stage_end(self, stage: "Stage", ctx: "PipelineContext", elapsed_ms: float) -> None: ...
    def on_error(self, stage: "Stage", ctx: "PipelineContext", err: BaseException) -> None: ...


class NullHook(PipelineHook):
    """默认 no-op。"""


class LoggingHook(PipelineHook):
    """打印进度与耗时。verbose=True 时打印 stage start/end，一直打印错误。"""

    def __init__(self, verbose: bool = True) -> None:
        self.verbose = verbose
        self._t0: dict[int, float] = {}

    def on_pipeline_start(self, ctx: "PipelineContext") -> None:
        if self.verbose:
            print(f"[pipeline] ▶ start target={ctx.target_name}")

    def on_pipeline_end(self, ctx: "PipelineContext") -> None:
        if self.verbose:
            print(f"[pipeline] ■ done")

    def on_stage_start(self, stage: "Stage", ctx: "PipelineContext") -> None:
        self._t0[id(stage)] = time.perf_counter()
        if self.verbose:
            print(f"[pipeline] ↳ {stage.name}")

    def on_stage_end(self, stage: "Stage", ctx: "PipelineContext", elapsed_ms: float) -> None:
        if self.verbose:
            print(f"[pipeline] ✓ {stage.name} ({elapsed_ms:.1f} ms)")

    def on_error(self, stage: "Stage", ctx: "PipelineContext", err: BaseException) -> None:
        print(f"[pipeline] ✗ {stage.name} failed: {err!r}")
