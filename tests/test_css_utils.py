"""Tests for common.css_utils — pure-function unit tests.

These are the easiest, most stable tests in the project: no IO, no PSD,
no images — just CSS string ↔ dict round-trip + value normalisation.
"""

from __future__ import annotations

import pytest

from common.css_utils import (
    _format_number,
    _normalize_css_value,
    _strip_leading_comments,
    dict_to_css,
    extract_global_css_header,
    parse_css_to_dict,
)


# ===================================================================
# _format_number
# ===================================================================

class TestFormatNumber:
    """CSS 数字精度规范化。"""

    @pytest.mark.parametrize("inp, expected", [
        ("22.099999999999998", "22.1"),
        ("22.0", "22"),
        ("0.5", "0.5"),
        ("100", "100"),
        ("0", "0"),
        ("-3.1415926", "-3.14"),
        ("1.10", "1.1"),
        ("abc", "abc"),           # 非数字原样返回
        ("", ""),                 # 空串
    ])
    def test_format_number(self, inp: str, expected: str):
        assert _format_number(inp) == expected


# ===================================================================
# _normalize_css_value
# ===================================================================

class TestNormalizeCssValue:
    """CSS 属性值中数字的规范化 + url() 保护。"""

    def test_float_noise(self):
        assert _normalize_css_value("22.099999999999998px") == "22.1px"

    def test_rgba_normalize(self):
        result = _normalize_css_value("rgba(19, 12, 41, 1.0)")
        assert result == "rgba(19, 12, 41, 1)"

    def test_integer_as_float(self):
        assert _normalize_css_value("1.0") == "1"

    def test_url_preserved(self):
        val = 'url("images/bg-f07984.png")'
        assert _normalize_css_value(val) == val

    def test_url_with_single_quotes_preserved(self):
        val = "url('images/layer-123.png')"
        assert _normalize_css_value(val) == val

    def test_var_preserved(self):
        assert _normalize_css_value("var(--color-1)") == "var(--color-1)"

    def test_multiple_numbers(self):
        result = _normalize_css_value("10.00px 20.50px 0px")
        assert result == "10px 20.5px 0px"

    def test_non_string_input(self):
        # noinspection PyTypeChecker
        assert _normalize_css_value(42) == "42"  # type: ignore[arg-type]


# ===================================================================
# _strip_leading_comments
# ===================================================================

class TestStripLeadingComments:
    def test_no_comment(self):
        assert _strip_leading_comments(".foo") == ".foo"

    def test_single_comment(self):
        assert _strip_leading_comments("/* layer */ .bar") == ".bar"

    def test_multiple_comments(self):
        assert _strip_leading_comments("/* a */ /* b */ .baz") == ".baz"

    def test_unclosed_comment(self):
        raw = "/* unclosed .qux"
        assert _strip_leading_comments(raw) == raw


# ===================================================================
# parse_css_to_dict
# ===================================================================

class TestParseCssToDict:
    """CSS text → {selector: {prop: val}} 解析。"""

    def test_simple_class(self):
        css = ".foo { color: red; font-size: 14px; }"
        result = parse_css_to_dict(css)
        assert ".foo" in result
        assert result[".foo"]["color"] == "red"
        assert result[".foo"]["font-size"] == "14px"

    def test_id_selector(self):
        css = "#canvas { width: 375px; height: 812px; }"
        result = parse_css_to_dict(css)
        assert "#canvas" in result
        assert result["#canvas"]["width"] == "375px"

    def test_global_selectors_excluded(self):
        css = "* { margin: 0; } body { overflow: hidden; } .foo { color: red; }"
        result = parse_css_to_dict(css)
        assert "*" not in result
        assert "body" not in result
        assert ".foo" in result

    def test_media_query_excluded(self):
        css = "@media (max-width: 600px) { .foo { color: blue; } } .bar { color: green; }"
        result = parse_css_to_dict(css)
        # @media 内的 .foo 不应出现在顶层
        assert ".foo" not in result
        assert ".bar" in result

    def test_comment_before_selector(self):
        css = "/* 图层样式 */\n.bg__1 { width: 100px; }"
        result = parse_css_to_dict(css)
        assert ".bg__1" in result

    def test_empty_input(self):
        assert parse_css_to_dict("") == {}

    def test_empty_rule_body(self):
        css = ".empty { }"
        result = parse_css_to_dict(css)
        # 空属性体应被跳过
        assert ".empty" not in result

    def test_sample_css(self, sample_css_text: str):
        """用 conftest 提供的样本 CSS 做端到端解析。"""
        result = parse_css_to_dict(sample_css_text)
        assert ".bg__1" in result
        assert ".title__2" in result
        assert ".divider__3" in result
        assert "#canvas" in result
        # 全局 * / body 不应出现
        assert "*" not in result
        assert "body" not in result


# ===================================================================
# extract_global_css_header
# ===================================================================

class TestExtractGlobalCssHeader:
    def test_extracts_star_and_body(self):
        css = "* { margin: 0; } body { overflow: hidden; } .foo { color: red; }"
        header = extract_global_css_header(css)
        assert "* {" in header
        assert "body {" in header
        assert ".foo" not in header

    def test_preserves_media_query(self):
        css = "@media (max-width: 600px) { .x { color: blue; } } .bar { color: green; }"
        header = extract_global_css_header(css)
        assert "@media" in header
        assert ".bar" not in header

    def test_sample_css(self, sample_css_text: str):
        header = extract_global_css_header(sample_css_text)
        assert "* {" in header
        assert "body {" in header
        # class 和 id 不应出现在 header 中
        assert ".bg__1" not in header
        assert ".title__2" not in header


# ===================================================================
# dict_to_css
# ===================================================================

class TestDictToCss:
    """字典 → CSS 文本序列化。"""

    def test_simple_round_trip(self):
        rules = {".foo": {"color": "red", "font-size": "14px"}}
        css = dict_to_css(rules)
        assert ".foo {" in css
        assert "color: red;" in css
        assert "font-size: 14px;" in css

    def test_header_included(self):
        rules = {".foo": {"color": "red"}}
        header = "* { margin: 0; }"
        css = dict_to_css(rules, header=header)
        assert css.startswith("* { margin: 0; }")
        # 图层样式分隔注释
        assert "图层样式" in css

    def test_merge_groups(self):
        rules = {
            ".a": {"color": "red"},
            ".b": {"color": "red"},
            ".c": {"color": "blue"},
        }
        groups = [[".a", ".b"]]
        css = dict_to_css(rules, merge_groups=groups)
        # .a 和 .b 应该合并成一条规则
        assert ".a,\n.b {" in css or ".a,\n.b {" in css
        # .c 应该单独输出
        assert ".c {" in css

    def test_merge_group_single_member_fallback(self):
        """合并组内只有一个有效成员时应降级为单条输出。"""
        rules = {".only": {"color": "red"}}
        groups = [[".only", ".missing"]]
        css = dict_to_css(rules, merge_groups=groups)
        assert ".only {" in css

    def test_empty_rules(self):
        assert dict_to_css({}) == ""

    def test_number_normalization(self):
        """dict_to_css 应经过 _normalize_css_value。"""
        rules = {".f": {"font-size": "22.099999999999998px"}}
        css = dict_to_css(rules)
        assert "22.1px" in css
        assert "22.099" not in css


# ===================================================================
# Round-trip: parse → dict → css → parse again
# ===================================================================

class TestRoundTrip:
    def test_parse_then_emit_then_parse(self, sample_css_text: str):
        """parse → dict_to_css → parse 的结果应保持属性等价。"""
        rules1 = parse_css_to_dict(sample_css_text)
        header = extract_global_css_header(sample_css_text)
        css2 = dict_to_css(rules1, header=header)
        rules2 = parse_css_to_dict(css2)

        # 两次解析的选择器集合应一致
        assert set(rules1.keys()) == set(rules2.keys())

        # 每条规则的属性名应一致（值可能因数字精度规范化略有不同）
        for sel in rules1:
            assert set(rules1[sel].keys()) == set(rules2[sel].keys()), (
                f"Property keys differ for {sel}"
            )
