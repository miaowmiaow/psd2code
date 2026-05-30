"""Unit tests for WrapperCollapse transformer.

Tests cover:
- Single-child v-row/v-col wrapper collapse
- Margin merging (additive px values)
- Multi-round collapse (nested single-child wrappers)
- Skip non-collapsible kinds (v-stack, v-list)
- Skip multi-child wrappers
- _parse_px edge cases
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from targets.html.postprocess.layout_optimizer.transformers.wrapper_collapse import (
    WrapperCollapse,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_soup(html: str) -> BeautifulSoup:
    """Parse a minimal HTML fragment."""
    return BeautifulSoup(html, "html.parser")


def _make_wrapper_html(
    wrapper_class: str = "v-row-1",
    wrapper_virtual: str = "row",
    child_class: str = "item__10",
    child_content: str = "Hello",
) -> str:
    """Generate a typical single-child wrapper div."""
    return (
        f'<div class="{wrapper_class} v-row" data-virtual="{wrapper_virtual}">'
        f'<div class="{child_class} layer">{ child_content}</div>'
        f"</div>"
    )


# ---------------------------------------------------------------------------
# _parse_px
# ---------------------------------------------------------------------------

class TestParsePx:
    """Static helper _parse_px."""

    def test_basic_int(self):
        assert WrapperCollapse._parse_px("12px") == 12.0

    def test_basic_float(self):
        assert WrapperCollapse._parse_px("12.5px") == 12.5

    def test_no_unit(self):
        assert WrapperCollapse._parse_px("7") == 7.0

    def test_zero(self):
        assert WrapperCollapse._parse_px("0px") == 0.0

    def test_negative(self):
        assert WrapperCollapse._parse_px("-3px") == -3.0

    def test_none_input(self):
        assert WrapperCollapse._parse_px(None) is None

    def test_empty_string(self):
        assert WrapperCollapse._parse_px("") is None

    def test_non_numeric(self):
        assert WrapperCollapse._parse_px("auto") is None

    def test_whitespace(self):
        assert WrapperCollapse._parse_px("  10px  ") == 10.0


# ---------------------------------------------------------------------------
# Basic collapse
# ---------------------------------------------------------------------------

class TestBasicCollapse:
    """Core single-child wrapper collapse logic."""

    def test_single_child_v_row_collapsed(self):
        """A v-row with one child should be collapsed."""
        html = _make_wrapper_html()
        soup = _make_soup(html)
        css_rules = {
            ".v-row-1": {"display": "flex", "margin-top": "10px"},
            ".item__10": {"width": "100px", "height": "50px"},
        }
        stats = {}

        WrapperCollapse(soup, css_rules, stats).run()

        # Wrapper should be gone from DOM
        assert soup.find("div", attrs={"data-virtual": "row"}) is None
        # Child still exists
        child = soup.find("div", class_="item__10")
        assert child is not None
        # Wrapper CSS removed
        assert ".v-row-1" not in css_rules
        # Stats updated
        assert stats["wrappers_collapsed"] == 1

    def test_margin_merged_additively(self):
        """Wrapper margin + child margin should be summed."""
        html = _make_wrapper_html()
        soup = _make_soup(html)
        css_rules = {
            ".v-row-1": {"margin-top": "10px", "margin-left": "5px"},
            ".item__10": {"margin-top": "3px", "width": "100px"},
        }
        stats = {}

        WrapperCollapse(soup, css_rules, stats).run()

        child_css = css_rules[".item__10"]
        assert child_css["margin-top"] == "13px"
        assert child_css["margin-left"] == "5px"

    def test_zero_margin_removed(self):
        """If merged margin is < 0.5, the property should be removed."""
        html = _make_wrapper_html()
        soup = _make_soup(html)
        css_rules = {
            ".v-row-1": {"margin-top": "0px"},
            ".item__10": {"margin-top": "0px", "width": "100px"},
        }
        stats = {}

        WrapperCollapse(soup, css_rules, stats).run()

        assert "margin-top" not in css_rules[".item__10"]

    def test_v_col_also_collapsed(self):
        """data-virtual='col' should be collapsible too."""
        html = (
            '<div class="v-col-3 v-col" data-virtual="col">'
            '<div class="card__5 layer">Content</div>'
            "</div>"
        )
        soup = _make_soup(html)
        css_rules = {
            ".v-col-3": {"margin-top": "8px"},
            ".card__5": {"width": "200px"},
        }
        stats = {}

        WrapperCollapse(soup, css_rules, stats).run()

        assert soup.find("div", attrs={"data-virtual": "col"}) is None
        assert stats["wrappers_collapsed"] == 1


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------

class TestSkipConditions:
    """Cases where collapse should NOT happen."""

    def test_skip_v_stack(self):
        """data-virtual='stack' should not be collapsed."""
        html = (
            '<div class="v-stack-2 v-stack" data-virtual="stack">'
            '<div class="item__20 layer">Content</div>'
            "</div>"
        )
        soup = _make_soup(html)
        css_rules = {
            ".v-stack-2": {"position": "relative"},
            ".item__20": {"position": "absolute"},
        }
        stats = {}

        WrapperCollapse(soup, css_rules, stats).run()

        # Wrapper should remain
        assert soup.find("div", attrs={"data-virtual": "stack"}) is not None
        assert stats["wrappers_collapsed"] == 0

    def test_skip_v_list(self):
        """Wrapper with SKIP_MARKER_CLASSES (v-list) in class list should be skipped."""
        html = (
            '<div class="v-row-5 v-list" data-virtual="row">'
            '<div class="item__30 layer">Content</div>'
            "</div>"
        )
        soup = _make_soup(html)
        css_rules = {
            ".v-row-5": {"display": "flex"},
            ".item__30": {"width": "50px"},
        }
        stats = {}

        WrapperCollapse(soup, css_rules, stats).run()

        assert soup.find("div", attrs={"data-virtual": "row"}) is not None
        assert stats["wrappers_collapsed"] == 0

    def test_skip_multi_child(self):
        """Wrapper with multiple children should not be collapsed."""
        html = (
            '<div class="v-row-6 v-row" data-virtual="row">'
            '<div class="a__1 layer">A</div>'
            '<div class="b__2 layer">B</div>'
            "</div>"
        )
        soup = _make_soup(html)
        css_rules = {
            ".v-row-6": {"display": "flex"},
            ".a__1": {"width": "50px"},
            ".b__2": {"width": "50px"},
        }
        stats = {}

        WrapperCollapse(soup, css_rules, stats).run()

        assert soup.find("div", attrs={"data-virtual": "row"}) is not None
        assert stats["wrappers_collapsed"] == 0

    def test_skip_child_is_v_list(self):
        """If the single child has SKIP_MARKER_CLASSES, skip collapse."""
        html = (
            '<div class="v-row-7 v-row" data-virtual="row">'
            '<div class="v-list-1 v-list" data-virtual="list">Items</div>'
            "</div>"
        )
        soup = _make_soup(html)
        css_rules = {
            ".v-row-7": {"display": "flex"},
            ".v-list-1": {"display": "flex", "flex-wrap": "wrap"},
        }
        stats = {}

        WrapperCollapse(soup, css_rules, stats).run()

        assert soup.find("div", attrs={"data-virtual": "row"}) is not None
        assert stats["wrappers_collapsed"] == 0


# ---------------------------------------------------------------------------
# Multi-round collapse
# ---------------------------------------------------------------------------

class TestMultiRoundCollapse:
    """Nested single-child wrappers should be collapsed across rounds."""

    def test_nested_wrappers_collapsed(self):
        """Two nested single-child wrappers: both should be collapsed."""
        html = (
            '<div class="v-row-1 v-row" data-virtual="row">'
            '<div class="v-col-2 v-col" data-virtual="col">'
            '<div class="leaf__99 layer">Leaf</div>'
            "</div>"
            "</div>"
        )
        soup = _make_soup(html)
        css_rules = {
            ".v-row-1": {"margin-top": "5px"},
            ".v-col-2": {"margin-top": "3px"},
            ".leaf__99": {"width": "80px"},
        }
        stats = {}

        WrapperCollapse(soup, css_rules, stats).run()

        # Both wrappers removed
        assert soup.find("div", attrs={"data-virtual": True}) is None
        # Leaf should exist
        leaf = soup.find("div", class_="leaf__99")
        assert leaf is not None
        # Margins accumulated: 5 + 3 = 8
        assert css_rules[".leaf__99"]["margin-top"] == "8px"
        assert stats["wrappers_collapsed"] == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Boundary conditions."""

    def test_no_wrappers_noop(self):
        """No virtual wrappers → nothing happens."""
        html = '<div class="regular layer">Content</div>'
        soup = _make_soup(html)
        css_rules = {".regular": {"width": "100px"}}
        stats = {}

        WrapperCollapse(soup, css_rules, stats).run()

        assert stats["wrappers_collapsed"] == 0

    def test_wrapper_no_css_rule(self):
        """Wrapper with no CSS rule entry should be skipped."""
        html = _make_wrapper_html(wrapper_class="v-row-9")
        soup = _make_soup(html)
        css_rules = {
            # No .v-row-9 rule
            ".item__10": {"width": "100px"},
        }
        stats = {}

        WrapperCollapse(soup, css_rules, stats).run()

        # Should not crash, wrapper remains
        assert soup.find("div", attrs={"data-virtual": "row"}) is not None
        assert stats["wrappers_collapsed"] == 0

    def test_child_without_class(self):
        """If child has no class attribute, skip collapse."""
        html = (
            '<div class="v-row-10 v-row" data-virtual="row">'
            "<div>No class child</div>"
            "</div>"
        )
        soup = _make_soup(html)
        css_rules = {
            ".v-row-10": {"margin-top": "10px"},
        }
        stats = {}

        WrapperCollapse(soup, css_rules, stats).run()

        assert soup.find("div", attrs={"data-virtual": "row"}) is not None
        assert stats["wrappers_collapsed"] == 0
