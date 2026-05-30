"""Unit tests for PositionNoiseRelaxer transformer.

Tests cover:
- Selector parsing (_parse_named, _parse_px)
- Basic position noise normalization (margin → mode)
- z-index drop behavior
- margin drift threshold rejection
- min_unify_count threshold
- Absolute positioning exclusion (top/left)
- Disabled config
- Multi-bucket (same base, different noise keys)
- Integration with _css_merge_groups
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from targets.html.postprocess.layout_optimizer.transformers.position_noise_relaxer import (
    PositionNoiseRelaxer,
    PositionRelaxerConfig,
    _parse_named,
    _parse_px,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_soup(html: str = "<div></div>") -> BeautifulSoup:
    """Minimal soup (PositionNoiseRelaxer reads DOM for stats only)."""
    return BeautifulSoup(html, "html.parser")


# ---------------------------------------------------------------------------
# _parse_named
# ---------------------------------------------------------------------------

class TestParseNamed:
    def test_standard(self):
        assert _parse_named(".nickname__37") == ("nickname", "37")

    def test_with_hyphen(self):
        assert _parse_named(".btn-receive__74") == ("btn-receive", "74")

    def test_sibling_index(self):
        assert _parse_named(".rounded-2__40") == ("rounded-2", "40")

    def test_no_match_no_dot(self):
        assert _parse_named("nickname__37") is None

    def test_no_match_no_id(self):
        assert _parse_named(".nickname") is None

    def test_no_match_derived(self):
        # v-stack-7 is not SimpleNamer format (no __id suffix)
        assert _parse_named(".v-stack-7") is None


# ---------------------------------------------------------------------------
# _parse_px
# ---------------------------------------------------------------------------

class TestParsePxRelaxer:
    def test_int_px(self):
        assert _parse_px("21px") == 21.0

    def test_negative(self):
        assert _parse_px("-3.5px") == -3.5

    def test_bare_number(self):
        assert _parse_px("0") == 0.0

    def test_auto_returns_none(self):
        assert _parse_px("auto") is None

    def test_none_input(self):
        assert _parse_px(None) is None


# ---------------------------------------------------------------------------
# Basic normalization
# ---------------------------------------------------------------------------

class TestBasicNormalization:
    """Core: 3+ members with same non-noise signature → normalized to mode."""

    def _setup(self, margins: list[str], extra_props: dict | None = None):
        """Create css_rules for nickname__N members with given margin-top values."""
        css_rules = {}
        for i, mt in enumerate(margins):
            sel = f".nickname__{i + 10}"
            props = {"width": "80px", "height": "24px", "font-size": "14px"}
            if extra_props:
                props.update(extra_props)
            props["margin-top"] = mt
            css_rules[sel] = props
        return css_rules

    def test_mode_selected(self):
        """Most common margin-top value should win."""
        css_rules = self._setup(["21px", "21px", "21px", "22px", "26px"])
        stats: dict = {}
        soup = _make_soup()

        PositionNoiseRelaxer(soup, css_rules, stats).run()

        # All members should now have margin-top = 21px (mode)
        for sel in css_rules:
            assert css_rules[sel]["margin-top"] == "21px"
        assert stats["position_relaxed_groups"] == 1
        assert stats["position_relaxed_classes"] == 5

    def test_z_index_dropped(self):
        """z-index should be entirely removed (drop_props)."""
        css_rules = {}
        for i in range(4):
            sel = f".card__{i + 20}"
            css_rules[sel] = {
                "width": "120px",
                "height": "180px",
                "margin-top": "10px",
                "z-index": str(10 + i * 8),
            }
        stats: dict = {}
        soup = _make_soup()

        PositionNoiseRelaxer(soup, css_rules, stats).run()

        for sel in css_rules:
            assert "z-index" not in css_rules[sel]

    def test_merge_groups_populated(self):
        """After normalization, members should appear in _css_merge_groups."""
        css_rules = self._setup(["10px", "10px", "10px"])
        stats: dict = {}
        soup = _make_soup()

        PositionNoiseRelaxer(soup, css_rules, stats).run()

        groups = stats.get("_css_merge_groups", [])
        assert len(groups) == 1
        assert len(groups[0]) == 3


# ---------------------------------------------------------------------------
# Rejection conditions
# ---------------------------------------------------------------------------

class TestRejectionConditions:
    """Cases where normalization should NOT happen."""

    def test_below_min_unify_count(self):
        """Only 2 members (below default 3) → no normalization."""
        css_rules = {
            ".item__1": {"width": "50px", "margin-top": "10px"},
            ".item__2": {"width": "50px", "margin-top": "12px"},
        }
        stats: dict = {}
        soup = _make_soup()

        PositionNoiseRelaxer(soup, css_rules, stats).run()

        assert stats.get("position_relaxed_groups", 0) == 0

    def test_margin_drift_exceeds_threshold(self):
        """margin-top spread > 8px → group rejected."""
        css_rules = {}
        margins = ["10px", "10px", "10px", "25px"]  # drift = 15 > 8
        for i, mt in enumerate(margins):
            css_rules[f".wide__{i + 50}"] = {
                "width": "100px",
                "margin-top": mt,
            }
        stats: dict = {}
        soup = _make_soup()

        PositionNoiseRelaxer(soup, css_rules, stats).run()

        assert stats.get("position_relaxed_groups", 0) == 0

    def test_absolute_positioned_excluded(self):
        """Members with top/left should be skipped entirely."""
        css_rules = {}
        for i in range(4):
            css_rules[f".abs__{i + 60}"] = {
                "width": "50px",
                "top": f"{i * 30}px",
                "left": "10px",
                "margin-top": "5px",
            }
        stats: dict = {}
        soup = _make_soup()

        PositionNoiseRelaxer(soup, css_rules, stats).run()

        assert stats.get("position_relaxed_groups", 0) == 0

    def test_different_non_noise_signatures(self):
        """Members with different width → different signature → separate buckets (each <3)."""
        css_rules = {
            ".mixed__1": {"width": "50px", "margin-top": "10px"},
            ".mixed__2": {"width": "50px", "margin-top": "11px"},
            ".mixed__3": {"width": "80px", "margin-top": "10px"},  # different width
            ".mixed__4": {"width": "80px", "margin-top": "11px"},
        }
        stats: dict = {}
        soup = _make_soup()

        PositionNoiseRelaxer(soup, css_rules, stats).run()

        # Two buckets of 2 each (below min_unify_count=3)
        assert stats.get("position_relaxed_groups", 0) == 0


# ---------------------------------------------------------------------------
# Config control
# ---------------------------------------------------------------------------

class TestConfig:
    def test_disabled(self):
        """When enabled=False, nothing should happen."""
        css_rules = {}
        for i in range(5):
            css_rules[f".x__{i}"] = {"width": "50px", "margin-top": f"{10+i}px"}
        stats: dict = {}
        soup = _make_soup()
        config = PositionRelaxerConfig(enabled=False)

        PositionNoiseRelaxer(soup, css_rules, stats, config).run()

        assert stats.get("position_relaxed_groups", 0) == 0

    def test_custom_min_unify_count(self):
        """Custom min_unify_count=2 should allow 2-member groups."""
        css_rules = {
            ".pair__1": {"width": "50px", "margin-top": "10px"},
            ".pair__2": {"width": "50px", "margin-top": "12px"},
        }
        stats: dict = {}
        soup = _make_soup()
        config = PositionRelaxerConfig(min_unify_count=2)

        PositionNoiseRelaxer(soup, css_rules, stats, config).run()

        assert stats["position_relaxed_groups"] == 1

    def test_custom_max_drift(self):
        """Custom max_margin_drift_px=20 should allow wider spread."""
        css_rules = {}
        margins = ["10px", "10px", "25px"]  # drift = 15, under 20
        for i, mt in enumerate(margins):
            css_rules[f".drift__{i + 70}"] = {"width": "100px", "margin-top": mt}
        stats: dict = {}
        soup = _make_soup()
        config = PositionRelaxerConfig(max_margin_drift_px=20.0)

        PositionNoiseRelaxer(soup, css_rules, stats, config).run()

        assert stats["position_relaxed_groups"] == 1


# ---------------------------------------------------------------------------
# Multi-bucket within same base
# ---------------------------------------------------------------------------

class TestMultiBucket:
    def test_same_base_different_noise_keys_split(self):
        """Same base members with different noise key sets → split into separate buckets."""
        css_rules = {
            # Group A: has margin-top only
            ".nick__1": {"width": "50px", "margin-top": "10px"},
            ".nick__2": {"width": "50px", "margin-top": "11px"},
            ".nick__3": {"width": "50px", "margin-top": "12px"},
            # Group B: has margin-top + margin-left
            ".nick__4": {"width": "50px", "margin-top": "10px", "margin-left": "54px"},
            ".nick__5": {"width": "50px", "margin-top": "11px", "margin-left": "54px"},
            ".nick__6": {"width": "50px", "margin-top": "12px", "margin-left": "55px"},
        }
        stats: dict = {}
        soup = _make_soup()

        PositionNoiseRelaxer(soup, css_rules, stats).run()

        # Both sub-buckets meet min_unify_count=3
        assert stats["position_relaxed_groups"] == 2
        assert stats["position_relaxed_classes"] == 6


# ---------------------------------------------------------------------------
# Existing merge groups preserved
# ---------------------------------------------------------------------------

class TestExistingMergeGroups:
    def test_existing_groups_not_lost(self):
        """Pre-existing _css_merge_groups should be preserved."""
        css_rules = {}
        for i in range(3):
            css_rules[f".new__{i + 80}"] = {"width": "50px", "margin-top": "5px"}
        stats = {"_css_merge_groups": [[".old__1", ".old__2"]]}
        soup = _make_soup()

        PositionNoiseRelaxer(soup, css_rules, stats).run()

        groups = stats["_css_merge_groups"]
        # Old group should still be there
        old_group_found = any(".old__1" in g for g in groups)
        assert old_group_found
        # New group also added
        assert len(groups) >= 2
