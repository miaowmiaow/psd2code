"""Tests for the framework package: Pipeline, Stage, PipelineContext, Hooks.

All tests are pure in-memory, no PSD or filesystem access required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from framework.context import PipelineContext
from framework.hooks import NullHook, LoggingHook, PipelineHook
from framework.pipeline import Pipeline
from framework.stage import Stage


# ===================================================================
# PipelineContext
# ===================================================================

class TestPipelineContext:
    def test_default_values(self):
        ctx = PipelineContext(psd_path=Path("test.psd"))
        assert ctx.target_name == "html"
        assert ctx.verbose is False
        assert ctx.psd is None
        assert ctx.ir is None
        assert ctx.artifacts == {}

    def test_set_and_get(self):
        ctx = PipelineContext(psd_path=Path("x.psd"))
        ctx.set("key", 42)
        assert ctx.get("key") == 42
        assert ctx.get("missing") is None
        assert ctx.get("missing", "default") == "default"

    def test_log_verbose(self, capsys):
        ctx = PipelineContext(psd_path=Path("x.psd"), verbose=True)
        ctx.log("hello")
        assert "hello" in capsys.readouterr().out

    def test_log_not_verbose(self, capsys):
        ctx = PipelineContext(psd_path=Path("x.psd"), verbose=False)
        ctx.log("secret")
        assert capsys.readouterr().out == ""


# ===================================================================
# Stage (abstract)
# ===================================================================

class _IncrementStage(Stage):
    """简单测试 Stage：给 ctx.artifacts['count'] 加 1。"""
    name = "increment"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        current = ctx.get("count", 0)
        ctx.set("count", current + 1)
        return ctx


class _FailStage(Stage):
    name = "fail"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        raise RuntimeError("stage failed")


class _SkipStage(Stage):
    """直接返回 ctx 不做修改 — 幂等的 no-op Stage。"""
    name = "skip"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        return ctx


class TestStage:
    def test_auto_name(self):
        class MyStage(Stage):
            def run(self, ctx):
                return ctx

        s = MyStage()
        assert s.name == "MyStage"

    def test_explicit_name(self):
        s = _IncrementStage(name="custom")
        assert s.name == "custom"

    def test_class_level_name(self):
        s = _IncrementStage()
        assert s.name == "increment"


# ===================================================================
# Pipeline
# ===================================================================

class TestPipeline:
    def test_empty_pipeline(self):
        p = Pipeline()
        ctx = PipelineContext(psd_path=Path("x.psd"))
        result = p.run(ctx)
        assert result is ctx

    def test_single_stage(self):
        p = Pipeline([_IncrementStage()])
        ctx = PipelineContext(psd_path=Path("x.psd"))
        result = p.run(ctx)
        assert result.get("count") == 1

    def test_multiple_stages(self):
        p = Pipeline([_IncrementStage(), _IncrementStage(), _IncrementStage()])
        ctx = PipelineContext(psd_path=Path("x.psd"))
        result = p.run(ctx)
        assert result.get("count") == 3

    def test_add_stage(self):
        p = Pipeline()
        p.add(_IncrementStage())
        ctx = PipelineContext(psd_path=Path("x.psd"))
        result = p.run(ctx)
        assert result.get("count") == 1

    def test_extend_stages(self):
        p = Pipeline()
        p.extend([_IncrementStage(), _IncrementStage()])
        assert len(p.stages) == 2

    def test_stage_failure_propagates(self):
        p = Pipeline([_IncrementStage(), _FailStage(), _IncrementStage()])
        ctx = PipelineContext(psd_path=Path("x.psd"))
        with pytest.raises(RuntimeError, match="stage failed"):
            p.run(ctx)
        # 第一个 stage 已经执行
        assert ctx.get("count") == 1

    def test_skip_stage(self):
        p = Pipeline([_IncrementStage(), _SkipStage(), _IncrementStage()])
        ctx = PipelineContext(psd_path=Path("x.psd"))
        result = p.run(ctx)
        assert result.get("count") == 2

    def test_stages_property_returns_copy(self):
        p = Pipeline([_IncrementStage()])
        stages = p.stages
        stages.append(_SkipStage())  # mutating the copy
        assert len(p.stages) == 1  # original unchanged


# ===================================================================
# Hooks
# ===================================================================

class _RecordingHook(PipelineHook):
    """记录所有 hook 调用顺序的测试用 Hook。"""

    def __init__(self):
        self.events: list[str] = []

    def on_pipeline_start(self, ctx):
        self.events.append("pipeline_start")

    def on_pipeline_end(self, ctx):
        self.events.append("pipeline_end")

    def on_stage_start(self, stage, ctx):
        self.events.append(f"stage_start:{stage.name}")

    def on_stage_end(self, stage, ctx, elapsed_ms):
        self.events.append(f"stage_end:{stage.name}")

    def on_error(self, stage, ctx, err):
        self.events.append(f"error:{stage.name}")


class TestHooks:
    def test_null_hook_no_error(self):
        hook = NullHook()
        ctx = PipelineContext(psd_path=Path("x.psd"), hook=hook)
        p = Pipeline([_IncrementStage()])
        p.run(ctx)
        # NullHook is no-op, just ensure no exceptions

    def test_logging_hook(self, capsys):
        hook = LoggingHook(verbose=True)
        ctx = PipelineContext(psd_path=Path("x.psd"), hook=hook)
        p = Pipeline([_IncrementStage()])
        p.run(ctx)
        out = capsys.readouterr().out
        assert "increment" in out

    def test_recording_hook_events(self):
        hook = _RecordingHook()
        ctx = PipelineContext(psd_path=Path("x.psd"), hook=hook)
        p = Pipeline([_IncrementStage(), _SkipStage()])
        p.run(ctx)
        assert hook.events == [
            "pipeline_start",
            "stage_start:increment",
            "stage_end:increment",
            "stage_start:skip",
            "stage_end:skip",
            "pipeline_end",
        ]

    def test_recording_hook_on_error(self):
        hook = _RecordingHook()
        ctx = PipelineContext(psd_path=Path("x.psd"), hook=hook)
        p = Pipeline([_FailStage()])
        with pytest.raises(RuntimeError):
            p.run(ctx)
        assert "error:fail" in hook.events
        # pipeline_end should still be called (in finally)
        assert "pipeline_end" in hook.events
