"""Unit tests for RepeatClassUnifier transformer.

Tests cover:
- Selector parsing helpers (_parse_named, _is_derived, _common_base_for_group)
- Basic unification (3+ equivalent classes → 1 unified class)
- _allocate_unified naming conflict resolution
- HTML class rewriting
- Skip conditions (derived classes, mixed bases, below threshold)
- data-repeat-index annotation
- _css_merge_groups remaining after unification
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from targets.html.postprocess.layout_optimizer.transformers.repeat_class_unifier import (
    RepeatClassUnifier,
    RepeatUnifyConfig,
    _parse_named,
    _is_derived,
    _common_base_for_group,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _make_elements_html(class_names: list[str]) -> str:
    """Create HTML with N divs, each having one of the given classes."""
    divs = "".join(f'<div class="{cn} layer">Item</div>' for cn in class_names)
    return f"<div>{divs}</div>"


# ---------------------------------------------------------------------------
# Selector parsing
# ---------------------------------------------------------------------------

class TestParseNamed:
    def test_standard(self):
        assert _parse_named(".prop__68") == ("prop", "68")

    def test_with_hyphen(self):
        assert _parse_named(".btn-receive__74") == ("btn-receive", "74")

    def test_with_sibling_index(self):
        assert _parse_named(".rounded-2__40") == ("rounded-2", "40")

    def test_no_dot(self):
        assert _parse_named("prop__68") is None

    def test_no_id_suffix(self):
        assert _parse_named(".prop") is None

    def test_only_digits_base(self):
        # Base must start with letter
        assert _parse_named(".123__45") is None


class TestIsDerived:
    def test_v_stack(self):
        assert _is_derived(".v-stack-7") is True

    def test_v_row(self):
        assert _is_derived(".v-row-29") is True

    def test_v_col(self):
        assert _is_derived(".v-col-43") is True

    def test_non_derived(self):
        assert _is_derived(".prop__68") is False

    def test_non_derived_no_number(self):
        assert _is_derived(".v-stack") is False


class TestCommonBase:
    def test_uniform_base(self):
        assert _common_base_for_group([".prop__68", ".prop__105", ".prop__142"]) == "prop"

    def test_mixed_base_returns_none(self):
        assert _common_base_for_group([".prop__68", ".btn__105"]) is None

    def test_non_named_returns_none(self):
        assert _common_base_for_group([".prop__68", ".plain-class"]) is None

    def test_single_member(self):
        assert _common_base_for_group([".card__10"]) == "card"


# ---------------------------------------------------------------------------
# Basic unification
# ---------------------------------------------------------------------------

class TestBasicUnification:
    """3+ members with same CSS → unified into single class."""

    def test_three_members_unified(self):
        members = [".prop__68", ".prop__105", ".prop__142"]
        # HTML
        html = _make_elements_html(["prop__68", "prop__105", "prop__142"])
        soup = _make_soup(html)
        # CSS: all identical
        css_rules = {sel: {"width": "120px", "height": "80px"} for sel in members}
        stats = {"_css_merge_groups": [members]}

        RepeatClassUnifier(soup, css_rules, stats).run()

        # Original selectors should be gone
        for sel in members:
            assert sel not in css_rules
        # Unified selector should exist
        assert ".prop" in css_rules
        assert css_rules[".prop"] == {"width": "120px", "height": "80px"}
        # Stats
        assert stats["repeat_groups_unified"] == 1
        assert stats["classes_unified"] == 2  # 3 - 1 = 2 removed
        assert stats["elements_unified"] == 3

    def test_html_classes_rewritten(self):
        """Each element's class should be replaced with the unified class."""
        members = [".card__10", ".card__20", ".card__30"]
        html = _make_elements_html(["card__10", "card__20", "card__30"])
        soup = _make_soup(html)
        css_rules = {sel: {"width": "100px"} for sel in members}
        stats = {"_css_merge_groups": [members]}

        RepeatClassUnifier(soup, css_rules, stats).run()

        for el in soup.find_all("div", class_="card"):
            assert "card" in el.get("class", [])

    def test_data_repeat_index_annotated(self):
        """data-repeat-index should be set on each element (1-based)."""
        members = [".item__1", ".item__2", ".item__3"]
        html = _make_elements_html(["item__1", "item__2", "item__3"])
        soup = _make_soup(html)
        css_rules = {sel: {"height": "50px"} for sel in members}
        stats = {"_css_merge_groups": [members]}
        config = RepeatUnifyConfig(annotate_index=True)

        RepeatClassUnifier(soup, css_rules, stats, config).run()

        indices = [el.get("data-repeat-index") for el in soup.find_all("div", class_="item")]
        assert indices == ["1", "2", "3"]


# ---------------------------------------------------------------------------
# Naming conflict resolution
# ---------------------------------------------------------------------------

class TestNamingConflict:
    """_allocate_unified should resolve naming conflicts."""

    def test_base_exists_uses_suffix(self):
        """If .prop already exists, use .prop-2."""
        members = [".prop__68", ".prop__105", ".prop__142"]
        html = _make_elements_html(["prop__68", "prop__105", "prop__142"])
        soup = _make_soup(html)
        css_rules = {sel: {"width": "100px"} for sel in members}
        # Pre-existing .prop rule → conflict
        css_rules[".prop"] = {"height": "200px"}
        stats = {"_css_merge_groups": [members]}

        RepeatClassUnifier(soup, css_rules, stats).run()

        # Should use .prop-2
        assert ".prop-2" in css_rules
        assert ".prop" in css_rules  # pre-existing preserved
        assert css_rules[".prop-2"] == {"width": "100px"}


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------

class TestSkipConditions:
    def test_below_min_count(self):
        """Only 2 members → skip (default min_unify_count=3)."""
        members = [".x__1", ".x__2"]
        html = _make_elements_html(["x__1", "x__2"])
        soup = _make_soup(html)
        css_rules = {sel: {"width": "50px"} for sel in members}
        stats = {"_css_merge_groups": [members]}

        RepeatClassUnifier(soup, css_rules, stats).run()

        # Original selectors should remain
        for sel in members:
            assert sel in css_rules
        assert stats["repeat_groups_unified"] == 0

    def test_derived_classes_skipped(self):
        """Groups containing v-stack-N etc. should not be unified."""
        members = [".v-stack-1", ".v-stack-2", ".v-stack-3"]
        html = _make_elements_html(["v-stack-1", "v-stack-2", "v-stack-3"])
        soup = _make_soup(html)
        css_rules = {sel: {"position": "relative"} for sel in members}
        stats = {"_css_merge_groups": [members]}

        RepeatClassUnifier(soup, css_rules, stats).run()

        # All remain
        for sel in members:
            assert sel in css_rules
        assert stats["repeat_groups_unified"] == 0

    def test_mixed_base_skipped(self):
        """Members with different bases → _common_base returns None → skip."""
        members = [".alpha__1", ".beta__2", ".gamma__3"]
        html = _make_elements_html(["alpha__1", "beta__2", "gamma__3"])
        soup = _make_soup(html)
        css_rules = {sel: {"width": "50px"} for sel in members}
        stats = {"_css_merge_groups": [members]}

        RepeatClassUnifier(soup, css_rules, stats).run()

        for sel in members:
            assert sel in css_rules
        assert stats["repeat_groups_unified"] == 0

    def test_disabled(self):
        """When enabled=False, nothing happens."""
        members = [".z__1", ".z__2", ".z__3"]
        html = _make_elements_html(["z__1", "z__2", "z__3"])
        soup = _make_soup(html)
        css_rules = {sel: {"width": "50px"} for sel in members}
        stats = {"_css_merge_groups": [members]}
        config = RepeatUnifyConfig(enabled=False)

        RepeatClassUnifier(soup, css_rules, stats, config).run()

        for sel in members:
            assert sel in css_rules


# ---------------------------------------------------------------------------
# Remaining merge groups
# ---------------------------------------------------------------------------

class TestRemainingGroups:
    def test_unified_groups_removed_from_merge_groups(self):
        """Successfully unified groups should be removed from _css_merge_groups."""
        members_a = [".a__1", ".a__2", ".a__3"]
        members_b = [".b__1", ".b__2"]  # below threshold, remains
        html = _make_elements_html(["a__1", "a__2", "a__3", "b__1", "b__2"])
        soup = _make_soup(html)
        css_rules = {}
        for sel in members_a:
            css_rules[sel] = {"width": "100px"}
        for sel in members_b:
            css_rules[sel] = {"height": "50px"}
        stats = {"_css_merge_groups": [members_a, members_b]}

        RepeatClassUnifier(soup, css_rules, stats).run()

        remaining = stats["_css_merge_groups"]
        # members_a should have been unified and removed
        assert members_a not in remaining
        # members_b stays (below threshold)
        assert members_b in remaining
