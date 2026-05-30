"""Tests for LayoutOptimizer._run_step — strict / tolerant mode.

Validates the P2 fix: strict=True raises immediately; strict=False
records to stats['_failures'] and continues.
"""

from __future__ import annotations

import pytest

from targets.html.postprocess.layout_optimizer.optimizer import LayoutOptimizer


def _make_optimizer(strict: bool = False) -> LayoutOptimizer:
    """Build a minimal LayoutOptimizer with trivial HTML/CSS for testing _run_step."""
    html = '<div class="box">hello</div>'
    css = {".box": {"width": "100px", "height": "100px"}}
    return LayoutOptimizer(html, css, strict=strict)


class TestRunStepTolerant:
    """strict=False（默认）：失败被捕获、记录到 _failures。"""

    def test_success_no_failure(self):
        opt = _make_optimizer(strict=False)
        calls = []

        def good_fn():
            calls.append("ok")

        opt._run_step("good_step", good_fn)

        assert calls == ["ok"]
        assert opt.stats["_failures"] == []

    def test_failure_recorded(self):
        opt = _make_optimizer(strict=False)

        def bad_fn():
            raise RuntimeError("boom")

        opt._run_step("bad_step", bad_fn)

        failures = opt.stats["_failures"]
        assert len(failures) == 1
        assert failures[0]["step"] == "bad_step"
        assert "boom" in failures[0]["error"]

    def test_multiple_failures_accumulated(self):
        opt = _make_optimizer(strict=False)

        def fail_a():
            raise ValueError("error-a")

        def fail_b():
            raise TypeError("error-b")

        opt._run_step("step_a", fail_a)
        opt._run_step("step_b", fail_b)

        assert len(opt.stats["_failures"]) == 2
        assert opt.stats["_failures"][0]["step"] == "step_a"
        assert opt.stats["_failures"][1]["step"] == "step_b"

    def test_failure_does_not_block_subsequent(self):
        """Tolerant mode should continue after a failure."""
        opt = _make_optimizer(strict=False)
        order = []

        def fail():
            order.append("fail")
            raise RuntimeError("x")

        def succeed():
            order.append("ok")

        opt._run_step("s1", fail)
        opt._run_step("s2", succeed)

        assert order == ["fail", "ok"]
        assert len(opt.stats["_failures"]) == 1

    def test_args_forwarded(self):
        """_run_step should forward *args and **kwargs to fn."""
        opt = _make_optimizer(strict=False)
        received = {}

        def fn_with_args(a, b, key=None):
            received["a"] = a
            received["b"] = b
            received["key"] = key

        opt._run_step("arg_step", fn_with_args, 1, 2, key="val")
        assert received == {"a": 1, "b": 2, "key": "val"}


class TestRunStepStrict:
    """strict=True（CI / debug 模式）：异常直接抛出。"""

    def test_success_ok(self):
        opt = _make_optimizer(strict=True)
        calls = []

        def good():
            calls.append("yes")

        opt._run_step("ok", good)
        assert calls == ["yes"]
        assert opt.stats["_failures"] == []

    def test_failure_raises(self):
        opt = _make_optimizer(strict=True)

        def bad():
            raise RuntimeError("strict boom")

        with pytest.raises(RuntimeError, match="strict boom"):
            opt._run_step("bad", bad)

    def test_failure_not_recorded_when_strict(self):
        """strict 模式下异常直接抛，不应记入 _failures。"""
        opt = _make_optimizer(strict=True)

        def bad():
            raise RuntimeError("x")

        with pytest.raises(RuntimeError):
            opt._run_step("x", bad)

        # 异常抛出后 _failures 应为空（没来得及记录）
        assert opt.stats["_failures"] == []


class TestOptimizerInit:
    """验证 LayoutOptimizer 初始化状态。"""

    def test_stats_has_failures_key(self):
        opt = _make_optimizer()
        assert "_failures" in opt.stats
        assert isinstance(opt.stats["_failures"], list)
        assert opt.stats["_failures"] == []

    def test_strict_default_false(self):
        opt = _make_optimizer()
        assert opt.strict is False

    def test_strict_true(self):
        opt = _make_optimizer(strict=True)
        assert opt.strict is True
