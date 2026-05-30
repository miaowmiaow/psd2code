"""CssDedup transformer 单测

覆盖 4 个 Pass：
  - Pass 0a：默认值剔除（opacity/mix-blend-mode/background-position）
  - Pass 0b：background shorthand 合并
  - Pass 1：z-index 精简（单调递增 / 非单调保留 / 混合补 z / v-* 保留）
  - Pass 2：等价规则合并
"""

import pytest
from bs4 import BeautifulSoup

from targets.html.postprocess.layout_optimizer.transformers.css_dedup import (
    CssDedup,
    _CSS_DEFAULT_VALUES,
    _BACKGROUND_NOISE_VALUES,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _run_dedup(html: str, css_rules: dict) -> dict:
    """快捷执行 CssDedup.run()，返回 stats"""
    soup = _make_soup(html)
    stats: dict = {}
    dedup = CssDedup(soup, css_rules, stats)
    dedup.run()
    return stats


# ===========================================================================
# Pass 0a: 默认值剔除
# ===========================================================================


class TestStripDefaultValues:
    """Pass 0a —— 删除 CSS 规范默认值的属性"""

    def test_strip_opacity_1(self):
        css = {".a": {"opacity": "1", "color": "red"}}
        _run_dedup("<div class='a'></div>", css)
        assert "opacity" not in css[".a"]
        assert css[".a"]["color"] == "red"

    def test_strip_opacity_1_dot_0(self):
        css = {".a": {"opacity": "1.0", "width": "100px"}}
        _run_dedup("<div class='a'></div>", css)
        assert "opacity" not in css[".a"]

    def test_keep_opacity_non_default(self):
        css = {".a": {"opacity": "0.5"}}
        _run_dedup("<div class='a'></div>", css)
        assert css[".a"]["opacity"] == "0.5"

    def test_strip_mix_blend_mode_normal(self):
        css = {".a": {"mix-blend-mode": "normal", "width": "50px"}}
        _run_dedup("<div class='a'></div>", css)
        assert "mix-blend-mode" not in css[".a"]

    def test_keep_mix_blend_mode_multiply(self):
        css = {".a": {"mix-blend-mode": "multiply"}}
        _run_dedup("<div class='a'></div>", css)
        assert css[".a"]["mix-blend-mode"] == "multiply"

    def test_strip_background_position_left_top(self):
        css = {".a": {"background-position": "left top", "height": "10px"}}
        _run_dedup("<div class='a'></div>", css)
        assert "background-position" not in css[".a"]

    def test_strip_background_position_0_0(self):
        css = {".a": {"background-position": "0 0"}}
        _run_dedup("<div class='a'></div>", css)
        assert "background-position" not in css[".a"]

    def test_strip_background_position_percent(self):
        css = {".a": {"background-position": "0% 0%"}}
        _run_dedup("<div class='a'></div>", css)
        assert "background-position" not in css[".a"]

    def test_keep_background_repeat_no_repeat(self):
        """background-repeat: no-repeat 不是 CSS 默认值，不能删"""
        css = {".a": {"background-repeat": "no-repeat"}}
        _run_dedup("<div class='a'></div>", css)
        assert css[".a"]["background-repeat"] == "no-repeat"

    def test_stats_count(self):
        css = {
            ".a": {"opacity": "1", "mix-blend-mode": "normal"},
            ".b": {"background-position": "0% 0%"},
        }
        stats = _run_dedup("<div class='a'></div><div class='b'></div>", css)
        assert stats["css_defaults_stripped"] == 3

    def test_multiple_rules(self):
        css = {
            ".x": {"opacity": "1", "color": "blue"},
            ".y": {"opacity": "0.8", "mix-blend-mode": "normal"},
        }
        _run_dedup("<div class='x'></div><div class='y'></div>", css)
        assert "opacity" not in css[".x"]
        assert css[".y"]["opacity"] == "0.8"
        assert "mix-blend-mode" not in css[".y"]


# ===========================================================================
# Pass 0b: background shorthand 合并
# ===========================================================================


class TestCollapseBackgroundShorthand:
    """Pass 0b —— background-image + position + repeat → background 单行"""

    def test_basic_merge(self):
        css = {".a": {
            "background-image": "url(img.png)",
            "background-position": "center",
            "background-repeat": "no-repeat",
            "width": "100px",
        }}
        _run_dedup("<div class='a'></div>", css)
        assert "background" in css[".a"]
        assert "background-image" not in css[".a"]
        assert "background-position" not in css[".a"]
        assert "background-repeat" not in css[".a"]
        assert "url(img.png)" in css[".a"]["background"]
        assert "center" in css[".a"]["background"]
        assert "no-repeat" in css[".a"]["background"]

    def test_skip_when_background_color_exists(self):
        """有 background-color 时不合并 shorthand"""
        css = {".a": {
            "background-image": "url(img.png)",
            "background-color": "#fff",
            "background-repeat": "no-repeat",
        }}
        _run_dedup("<div class='a'></div>", css)
        assert "background" not in css[".a"]
        assert "background-image" in css[".a"]

    def test_skip_when_background_size_exists(self):
        css = {".a": {
            "background-image": "url(img.png)",
            "background-size": "cover",
            "background-repeat": "no-repeat",
        }}
        _run_dedup("<div class='a'></div>", css)
        assert "background" not in css[".a"]

    def test_merge_without_position(self):
        """没有 background-position 时也能合并（只有 image + repeat）"""
        css = {".a": {
            "background-image": "url(img.png)",
            "background-repeat": "no-repeat",
        }}
        _run_dedup("<div class='a'></div>", css)
        assert "background" in css[".a"]
        assert css[".a"]["background"] == "url(img.png) no-repeat"

    def test_no_merge_single_token(self):
        """只有 background-image 无 position/repeat 时不合并"""
        css = {".a": {
            "background-image": "url(img.png)",
            "width": "50px",
        }}
        _run_dedup("<div class='a'></div>", css)
        # background-position 在 Pass 0a 会被 strip（如果是 default），
        # 但如果没有 position/repeat，token < 2，不合并
        assert "background-image" in css[".a"]

    def test_stats(self):
        css = {".a": {
            "background-image": "url(img.png)",
            "background-position": "center",
            "background-repeat": "no-repeat",
        }}
        stats = _run_dedup("<div class='a'></div>", css)
        # 合并: image + pos + repeat → background，节省 2 个字段
        assert stats["background_shorthand_merged"] == 2


# ===========================================================================
# Pass 1: z-index 精简
# ===========================================================================


class TestPruneZIndex:
    """Pass 1 —— z-index 精简"""

    def test_monotonic_increasing_pruned(self):
        """DOM 顺序 z-index 严格递增 → 全删"""
        html = """
        <div id="parent">
          <div class="a"></div>
          <div class="b"></div>
          <div class="c"></div>
        </div>
        """
        css = {
            ".a": {"z-index": "1", "width": "10px"},
            ".b": {"z-index": "5", "width": "20px"},
            ".c": {"z-index": "10", "width": "30px"},
        }
        stats = _run_dedup(html, css)
        assert "z-index" not in css[".a"]
        assert "z-index" not in css[".b"]
        assert "z-index" not in css[".c"]
        assert stats["z_index_pruned"] == 3

    def test_non_monotonic_preserved(self):
        """z-index 非单调递增 → 全保留"""
        html = """
        <div id="parent">
          <div class="a"></div>
          <div class="b"></div>
          <div class="c"></div>
        </div>
        """
        css = {
            ".a": {"z-index": "10"},
            ".b": {"z-index": "5"},
            ".c": {"z-index": "20"},
        }
        stats = _run_dedup(html, css)
        assert css[".a"]["z-index"] == "10"
        assert css[".b"]["z-index"] == "5"
        assert css[".c"]["z-index"] == "20"
        assert stats["z_index_pruned"] == 0

    def test_virtual_wrapper_preserved(self):
        """v-* wrapper 的 z-index 不被删除"""
        html = """
        <div id="parent">
          <div class="v-stack-1"></div>
          <div class="a"></div>
          <div class="b"></div>
        </div>
        """
        css = {
            ".v-stack-1": {"z-index": "1"},
            ".a": {"z-index": "5"},
            ".b": {"z-index": "10"},
        }
        stats = _run_dedup(html, css)
        # v-stack-1 的 z-index 必须保留
        assert css[".v-stack-1"]["z-index"] == "1"
        # a 和 b 可以被删
        assert "z-index" not in css[".a"]
        assert "z-index" not in css[".b"]
        assert stats["z_index_pruned"] == 2

    def test_mixed_state_fills_z(self):
        """混合状态（部分 auto）→ 给 auto 兄弟补 z-index"""
        html = """
        <div id="parent">
          <div class="a"></div>
          <div class="b"></div>
          <div class="c"></div>
        </div>
        """
        css = {
            ".a": {"width": "10px"},  # auto（无 z-index）
            ".b": {"z-index": "5", "width": "20px"},
            ".c": {"width": "30px"},  # auto
        }
        stats = _run_dedup(html, css)
        # .a 应该被补 z-index
        assert "z-index" in css[".a"]
        # .c 应该也被补且 > 5
        assert "z-index" in css[".c"]
        assert int(css[".c"]["z-index"]) > 5
        assert stats["z_index_filled"] > 0

    def test_all_auto_no_descendant_z_no_action(self):
        """全部子 auto + 后代也无 z → 不做任何处理"""
        html = """
        <div id="parent">
          <div class="a"><span>text</span></div>
          <div class="b"></div>
        </div>
        """
        css = {
            ".a": {"width": "10px"},
            ".b": {"width": "20px"},
        }
        stats = _run_dedup(html, css)
        assert "z-index" not in css[".a"]
        assert "z-index" not in css[".b"]
        assert stats["z_index_pruned"] == 0
        assert stats["z_index_filled"] == 0


# ===========================================================================
# Pass 2: 等价规则合并
# ===========================================================================


class TestMergeEquivalentRules:
    """Pass 2 —— 属性完全相同的选择器合并到同组"""

    def test_basic_merge(self):
        html = "<div class='a'></div><div class='b'></div>"
        css = {
            ".a": {"width": "100px", "height": "50px"},
            ".b": {"width": "100px", "height": "50px"},
        }
        stats = _run_dedup(html, css)
        assert stats["css_rules_merged"] == 1
        groups = stats["_css_merge_groups"]
        assert len(groups) == 1
        assert ".a" in groups[0]
        assert ".b" in groups[0]

    def test_no_merge_different_props(self):
        html = "<div class='a'></div><div class='b'></div>"
        css = {
            ".a": {"width": "100px"},
            ".b": {"width": "200px"},
        }
        stats = _run_dedup(html, css)
        assert stats["css_rules_merged"] == 0
        assert stats["_css_merge_groups"] == []

    def test_three_way_merge(self):
        html = "<div class='a'></div><div class='b'></div><div class='c'></div>"
        css = {
            ".a": {"color": "red", "font-size": "14px"},
            ".b": {"color": "red", "font-size": "14px"},
            ".c": {"color": "red", "font-size": "14px"},
        }
        stats = _run_dedup(html, css)
        assert stats["css_rules_merged"] == 2
        groups = stats["_css_merge_groups"]
        assert len(groups) == 1
        assert sorted(groups[0]) == [".a", ".b", ".c"]

    def test_empty_rules_not_merged(self):
        """空规则不参与合并"""
        html = "<div class='a'></div><div class='b'></div>"
        css = {
            ".a": {},
            ".b": {},
        }
        stats = _run_dedup(html, css)
        assert stats["css_rules_merged"] == 0

    def test_multiple_merge_groups(self):
        html = "<div class='a'></div><div class='b'></div><div class='c'></div><div class='d'></div>"
        css = {
            ".a": {"color": "red"},
            ".b": {"color": "red"},
            ".c": {"color": "blue"},
            ".d": {"color": "blue"},
        }
        stats = _run_dedup(html, css)
        assert stats["css_rules_merged"] == 2
        assert len(stats["_css_merge_groups"]) == 2


# ===========================================================================
# 辅助方法
# ===========================================================================


class TestHelperMethods:
    """CssDedup 内部辅助方法"""

    def test_is_virtual_wrapper_selector(self):
        assert CssDedup._is_virtual_wrapper_selector(".v-list-1") is True
        assert CssDedup._is_virtual_wrapper_selector(".v-stack-3") is True
        assert CssDedup._is_virtual_wrapper_selector(".v-row-2") is True
        assert CssDedup._is_virtual_wrapper_selector(".btn__1") is False
        assert CssDedup._is_virtual_wrapper_selector(".card") is False

    def test_first_class(self):
        soup = _make_soup("<div class='a b c'></div>")
        elem = soup.find("div")
        dedup = CssDedup(soup, {}, {})
        assert dedup._first_class(elem) == "a"

    def test_first_class_none(self):
        soup = _make_soup("<div></div>")
        elem = soup.find("div")
        dedup = CssDedup(soup, {}, {})
        assert dedup._first_class(elem) is None

    def test_read_z_valid(self):
        css = {".a": {"z-index": "42"}}
        soup = _make_soup("<div class='a'></div>")
        dedup = CssDedup(soup, css, {})
        assert dedup._read_z(".a") == 42

    def test_read_z_none(self):
        css = {".a": {"width": "10px"}}
        soup = _make_soup("<div class='a'></div>")
        dedup = CssDedup(soup, css, {})
        assert dedup._read_z(".a") is None

    def test_read_z_missing_selector(self):
        soup = _make_soup("<div></div>")
        dedup = CssDedup(soup, {}, {})
        assert dedup._read_z(".nonexistent") is None


# ===========================================================================
# 集成：run() 执行完整 pipeline
# ===========================================================================


class TestFullPipeline:
    """验证 run() 按 0a→0b→1→2 顺序执行所有 pass"""

    def test_defaults_stripped_before_merge(self):
        """删除默认值后两条规则变成等价，能被合并"""
        html = "<div class='a'></div><div class='b'></div>"
        css = {
            ".a": {"width": "100px", "opacity": "1"},
            ".b": {"width": "100px", "mix-blend-mode": "normal"},
        }
        stats = _run_dedup(html, css)
        # 两条规则删除默认值后都只剩 width: 100px → 可以合并
        assert stats["css_rules_merged"] == 1
        assert "opacity" not in css[".a"]
        assert "mix-blend-mode" not in css[".b"]

    def test_shorthand_before_merge(self):
        """shorthand 合并后两条规则变等价，能被 merge"""
        html = "<div class='a'></div><div class='b'></div>"
        css = {
            ".a": {
                "background-image": "url(img.png)",
                "background-repeat": "no-repeat",
                "width": "100px",
            },
            ".b": {
                "background-image": "url(img.png)",
                "background-repeat": "no-repeat",
                "width": "100px",
            },
        }
        stats = _run_dedup(html, css)
        # 两个都合并成 background shorthand 后属性相同 → 合并
        assert stats["css_rules_merged"] == 1
