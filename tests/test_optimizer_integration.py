"""Tests for LayoutOptimizer — 主协调器集成测试

覆盖范围：
- optimize_layout 入口函数：端到端调用
- LayoutOptimizer.__init__: 默认参数 / 自定义 config
- _run_step: strict=True 时抛异常；strict=False 时记录 _failures
- optimize: 全链路各 transformer 被正确执行（通过 stats 断言）
- 空 HTML / 单节点 / 多节点的边界行为
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from targets.html.postprocess.layout_optimizer.optimizer import (
    LayoutOptimizer,
    optimize_layout,
)
from targets.html.postprocess.layout_optimizer.transformers.css_pretty import (
    CssPrettyConfig,
)
from targets.html.postprocess.layout_optimizer.transformers.image_layer_flatten import (
    FlattenConfig,
)
from targets.html.postprocess.layout_optimizer.transformers.position_noise_relaxer import (
    PositionRelaxerConfig,
)
from targets.html.postprocess.layout_optimizer.transformers.repeat_class_unifier import (
    RepeatUnifyConfig,
)
from targets.html.postprocess.layout_optimizer.transformers.semantic_class_rename import (
    SemanticRenameConfig,
)
from targets.html.postprocess.layout_optimizer.transformers.virtual_wrapper_rename import (
    VirtualWrapperRenameConfig,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures / Helpers
# ═══════════════════════════════════════════════════════════════════════════════


_SIMPLE_HTML = """<!DOCTYPE html>
<html><body>
<div id="canvas">
  <div class="root" data-type="group" style="position:relative;">
    <div class="child-a" data-type="image"></div>
    <div class="child-b" data-type="text">Hello</div>
  </div>
</div>
</body></html>"""

_SIMPLE_CSS = {
    ".root": {
        "position": "absolute",
        "left": "0px",
        "top": "0px",
        "width": "400px",
        "height": "300px",
    },
    ".child-a": {
        "position": "absolute",
        "left": "10px",
        "top": "10px",
        "width": "100px",
        "height": "50px",
        "background-image": "url(images/a.png)",
        "background-size": "100% 100%",
    },
    ".child-b": {
        "position": "absolute",
        "left": "10px",
        "top": "70px",
        "width": "100px",
        "height": "30px",
    },
}


def _flex_html():
    """生成一个垂直 3 排列 DOM（能触发 flex）"""
    items = ""
    for i in range(1, 4):
        items += (
            f'  <div class="item-{i}" data-type="group">'
            f'<div class="txt-{i}" data-type="text">T{i}</div></div>\n'
        )
    return f"""<!DOCTYPE html>
<html><body>
<div id="canvas">
<div class="container" data-type="group">
{items}</div>
</div>
</body></html>"""


def _flex_css():
    """3 个垂直排列的子项 CSS（X 对齐，Y 递增）"""
    rules = {
        ".container": {
            "position": "absolute",
            "left": "0px",
            "top": "0px",
            "width": "300px",
            "height": "400px",
        },
    }
    for i in range(1, 4):
        rules[f".item-{i}"] = {
            "position": "absolute",
            "left": "50px",
            "top": f"{(i - 1) * 120}px",
            "width": "200px",
            "height": "100px",
        }
        rules[f".txt-{i}"] = {
            "position": "absolute",
            "left": "10px",
            "top": "10px",
            "width": "80px",
            "height": "20px",
        }
    return rules


# ═══════════════════════════════════════════════════════════════════════════════
# TestRunStep
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunStep:
    """_run_step 容错机制"""

    def test_strict_mode_raises(self):
        """strict=True 时，步骤失败直接抛出"""
        opt = LayoutOptimizer(_SIMPLE_HTML, _SIMPLE_CSS, strict=True)

        def _failing():
            raise ValueError("intentional failure")

        with pytest.raises(ValueError, match="intentional failure"):
            opt._run_step("test-step", _failing)

    def test_tolerant_mode_records_failure(self):
        """strict=False 时，步骤失败记录到 _failures"""
        opt = LayoutOptimizer(_SIMPLE_HTML, _SIMPLE_CSS, strict=False)

        def _failing():
            raise RuntimeError("oops")

        opt._run_step("test-step", _failing)
        assert len(opt.stats["_failures"]) == 1
        assert opt.stats["_failures"][0]["step"] == "test-step"
        assert "oops" in opt.stats["_failures"][0]["error"]

    def test_successful_step_no_failure(self):
        """正常步骤不会增加 _failures"""
        opt = LayoutOptimizer(_SIMPLE_HTML, _SIMPLE_CSS, strict=False)

        called = []

        def _ok():
            called.append(True)

        opt._run_step("ok-step", _ok)
        assert len(opt.stats["_failures"]) == 0
        assert called == [True]


# ═══════════════════════════════════════════════════════════════════════════════
# TestLayoutOptimizerInit
# ═══════════════════════════════════════════════════════════════════════════════


class TestLayoutOptimizerInit:
    """__init__ 参数默认值和 config"""

    def test_default_configs(self):
        opt = LayoutOptimizer(_SIMPLE_HTML, _SIMPLE_CSS)
        assert isinstance(opt.pretty_config, CssPrettyConfig)
        assert isinstance(opt.repeat_unify_config, RepeatUnifyConfig)
        assert isinstance(opt.semantic_rename_config, SemanticRenameConfig)
        assert isinstance(opt.virtual_wrapper_rename_config, VirtualWrapperRenameConfig)
        assert isinstance(opt.position_relaxer_config, PositionRelaxerConfig)
        assert isinstance(opt.flatten_config, FlattenConfig)
        assert opt.strict is False
        assert opt.images_dir is None

    def test_custom_strict(self):
        opt = LayoutOptimizer(_SIMPLE_HTML, _SIMPLE_CSS, strict=True)
        assert opt.strict is True

    def test_stats_initialized(self):
        opt = LayoutOptimizer(_SIMPLE_HTML, _SIMPLE_CSS)
        assert opt.stats["flex_applied"] == 0
        assert opt.stats["dom_restructured"] == 0
        assert opt.stats["_failures"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# TestOptimizeEndToEnd
# ═══════════════════════════════════════════════════════════════════════════════


class TestOptimizeEndToEnd:
    """optimize() 端到端集成"""

    def test_returns_three_tuple(self):
        """optimize 返回 (html_str, css_dict, stats_dict)"""
        html_out, css_out, stats = optimize_layout(
            _SIMPLE_HTML, _SIMPLE_CSS, strict=False
        )
        assert isinstance(html_out, str)
        assert isinstance(css_out, dict)
        assert isinstance(stats, dict)

    def test_html_output_is_valid(self):
        """输出 HTML 可被解析"""
        html_out, _, _ = optimize_layout(_SIMPLE_HTML, _SIMPLE_CSS)
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_out, "html.parser")
        # 至少应该保留 root 和 canvas
        assert soup.find(id="canvas") is not None

    def test_stats_has_key_fields(self):
        """stats 包含所有预定义 key"""
        _, _, stats = optimize_layout(_SIMPLE_HTML, _SIMPLE_CSS)
        expected_keys = [
            "backgrounds_merged",
            "classes_merged",
            "flex_applied",
            "positions_removed",
            "dom_restructured",
            "sibling_lists_created",
            "sibling_items_wrapped",
            "z_index_pruned",
            "css_rules_merged",
            "_failures",
            "_pretty_css",
        ]
        for key in expected_keys:
            assert key in stats, f"Missing stats key: {key}"

    def test_empty_html_no_crash(self):
        """空 HTML 不崩溃"""
        html_out, css_out, stats = optimize_layout("", {})
        assert isinstance(html_out, str)

    def test_empty_body_no_crash(self):
        """空 body 不崩溃"""
        html = "<html><body></body></html>"
        html_out, css_out, stats = optimize_layout(html, {})
        assert isinstance(html_out, str)

    def test_flex_applied_on_vertical_items(self):
        """3 个垂直排列子项 → flex_applied > 0"""
        html_out, css_out, stats = optimize_layout(
            _flex_html(), _flex_css(), strict=False
        )
        # 应该识别出至少 1 个 flex 容器
        assert stats["flex_applied"] >= 1

    def test_css_dedup_runs(self):
        """CssDedup 被运行（z_index_pruned 出现在 stats 中）"""
        # 给所有元素加 z-index：单调递增
        css = dict(_SIMPLE_CSS)
        css[".child-a"] = {**css[".child-a"], "z-index": "1"}
        css[".child-b"] = {**css[".child-b"], "z-index": "2"}
        _, css_out, stats = optimize_layout(_SIMPLE_HTML, css, strict=False)
        # z_index_pruned 键必须存在且 >= 0
        assert "z_index_pruned" in stats

    def test_pretty_css_generated(self):
        """CssPretty 默认开启，输出 _pretty_css"""
        _, _, stats = optimize_layout(_SIMPLE_HTML, _SIMPLE_CSS)
        # pretty_css 应该是非空字符串（除非 DOM 为空无可用 class）
        assert "_pretty_css" in stats


# ═══════════════════════════════════════════════════════════════════════════════
# TestOptimizeLayoutFunction
# ═══════════════════════════════════════════════════════════════════════════════


class TestOptimizeLayoutFunction:
    """optimize_layout 入口函数参数透传"""

    def test_strict_propagated(self):
        """strict=True 参数被传递到 LayoutOptimizer"""
        # 使用正常输入不应触发异常
        html_out, _, stats = optimize_layout(
            _SIMPLE_HTML, _SIMPLE_CSS, strict=True
        )
        assert len(stats["_failures"]) == 0

    def test_flatten_config_disabled(self):
        """flatten_config(enabled=False) 跳过图层扁平化"""
        config = FlattenConfig(enabled=False)
        _, _, stats = optimize_layout(
            _SIMPLE_HTML, _SIMPLE_CSS, flatten_config=config
        )
        # 不应有容器被 flatten
        assert stats.get("image_layer_containers_flattened", 0) == 0

    def test_pretty_config_disabled(self):
        """pretty_config(enabled=False) 不生成 _pretty_css"""
        config = CssPrettyConfig(enabled=False)
        _, _, stats = optimize_layout(
            _SIMPLE_HTML, _SIMPLE_CSS, pretty_config=config
        )
        assert stats["_pretty_css"] == ""

    def test_position_relaxer_disabled(self):
        """position_relaxer_config(enabled=False) 跳过位置归一"""
        config = PositionRelaxerConfig(enabled=False)
        _, _, stats = optimize_layout(
            _SIMPLE_HTML, _SIMPLE_CSS, position_relaxer_config=config
        )
        assert stats.get("position_relaxed_groups", 0) == 0

    def test_global_header_preserved_in_pretty_css(self):
        """global_header 中的 CSS 规则被传递到 CssPretty 输出"""
        header = "/* global reset */\n* { margin: 0; padding: 0; }\n"
        _, _, stats = optimize_layout(
            _SIMPLE_HTML, _SIMPLE_CSS, global_header=header
        )
        pretty = stats.get("_pretty_css", "")
        if pretty:
            # CssPretty 重写注释格式但保留规则内容
            assert "margin: 0" in pretty
            assert "padding: 0" in pretty


# ═══════════════════════════════════════════════════════════════════════════════
# TestStrictModePropagation
# ═══════════════════════════════════════════════════════════════════════════════


class TestStrictModePropagation:
    """strict 模式在 CssPretty 中的行为"""

    def test_pretty_failure_in_strict_raises(self):
        """strict=True + CssPretty 异常 → 向上传播"""
        with patch(
            "targets.html.postprocess.layout_optimizer.optimizer.CssPretty"
        ) as mock_cls:
            mock_cls.return_value.render.side_effect = RuntimeError("render boom")
            with pytest.raises(RuntimeError, match="render boom"):
                optimize_layout(_SIMPLE_HTML, _SIMPLE_CSS, strict=True)

    def test_pretty_failure_in_tolerant_recorded(self):
        """strict=False + CssPretty 异常 → 记录但不崩溃"""
        with patch(
            "targets.html.postprocess.layout_optimizer.optimizer.CssPretty"
        ) as mock_cls:
            mock_cls.return_value.render.side_effect = RuntimeError("render boom")
            _, _, stats = optimize_layout(_SIMPLE_HTML, _SIMPLE_CSS, strict=False)
            assert any(
                "CSS 美化" in f["step"] for f in stats["_failures"]
            )
            assert stats["_pretty_css"] == ""
