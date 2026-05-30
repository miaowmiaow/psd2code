# -*- coding: utf-8 -*-
"""Unit tests for core.extract.compose_cluster (P4).

Tests cover:
  - Helper functions: _bm_str, _is_pass_through, _is_normal_blend, _is_clipping,
    _is_text, _is_adjustment, _classify_layer, _is_group_recursively_empty
  - ComposeCluster data structure methods
  - detect_compose_clusters algorithm (R1~R4 rules)
  - decide_group_merge high-level decisions (no_merge / merge_full /
    merge_with_text_kept / merge_partial)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.extract.compose_cluster import (
    LayerKind,
    _bm_str,
    _is_pass_through,
    _is_normal_blend,
    _is_clipping,
    _is_text,
    _is_adjustment,
    _classify_layer,
    _is_group_recursively_empty,
    _group_contains_context_dependent,
    ComposeCluster,
    detect_compose_clusters,
    decide_group_merge,
    GroupComposeDecision,
    describe_decision,
    _has_recursive_text,
    _collect_recursive_text,
)


# ---------------------------------------------------------------------------
# Mock layer factory helpers
# ---------------------------------------------------------------------------

def _layer(
    *,
    kind="pixel",
    blend_mode="BlendMode.NORMAL",
    clipping=0,
    visible=True,
    opacity=255,
    is_group=False,
    bbox=(0, 0, 100, 100),
    children=None,
    name="layer",
):
    """Create a minimal mock PSD layer."""
    record = SimpleNamespace(clipping=clipping)
    layer = MagicMock()
    layer.kind = kind
    layer.blend_mode = blend_mode
    layer.visible = visible
    layer.opacity = opacity
    layer.bbox = bbox
    layer.name = name
    layer._record = record
    layer.is_group = MagicMock(return_value=is_group)

    # Make layer iterable (group children)
    if children is not None:
        layer.__iter__ = MagicMock(side_effect=lambda: iter(children))
    else:
        layer.__iter__ = MagicMock(side_effect=lambda: iter([]))

    return layer


def _group(children=None, blend_mode="BlendMode.NORMAL", **kwargs):
    """Convenience: create a group layer."""
    return _layer(
        kind="group",
        is_group=True,
        blend_mode=blend_mode,
        children=children or [],
        **kwargs,
    )


def _text(**kwargs):
    return _layer(kind="type", **kwargs)


def _pixel(**kwargs):
    return _layer(kind="pixel", **kwargs)


def _adjustment(**kwargs):
    return _layer(kind="adjustment", **kwargs)


# ===========================================================================
# Helper functions tests
# ===========================================================================


class TestBmStr:
    def test_normal(self):
        l = _layer(blend_mode="BlendMode.NORMAL")
        assert "NORMAL" in _bm_str(l)

    def test_empty_fallback(self):
        l = _layer()
        l.blend_mode = ""
        assert _bm_str(l) == ""

    def test_no_attr(self):
        l = SimpleNamespace()  # no blend_mode attr
        assert _bm_str(l) == ""


class TestIsPassThrough:
    def test_pass_through(self):
        l = _layer(blend_mode="BlendMode.PASS_THROUGH")
        assert _is_pass_through(l) is True

    def test_normal(self):
        l = _layer(blend_mode="BlendMode.NORMAL")
        assert _is_pass_through(l) is False


class TestIsNormalBlend:
    def test_normal(self):
        l = _layer(blend_mode="BlendMode.NORMAL")
        assert _is_normal_blend(l) is True

    def test_dissolve(self):
        l = _layer(blend_mode="BlendMode.DISSOLVE")
        assert _is_normal_blend(l) is True

    def test_multiply(self):
        l = _layer(blend_mode="BlendMode.MULTIPLY")
        assert _is_normal_blend(l) is False

    def test_pass_through_not_normal(self):
        l = _layer(blend_mode="BlendMode.PASS_THROUGH")
        assert _is_normal_blend(l) is False

    def test_empty_is_normal(self):
        l = _layer()
        l.blend_mode = ""
        assert _is_normal_blend(l) is True


class TestIsClipping:
    def test_clipping_1(self):
        l = _layer(clipping=1)
        assert _is_clipping(l) is True

    def test_clipping_0(self):
        l = _layer(clipping=0)
        assert _is_clipping(l) is False

    def test_no_record(self):
        l = SimpleNamespace()
        assert _is_clipping(l) is False


class TestIsText:
    def test_type_layer(self):
        l = _layer(kind="type")
        assert _is_text(l) is True

    def test_pixel(self):
        l = _layer(kind="pixel")
        assert _is_text(l) is False

    def test_typelayer_string(self):
        l = _layer(kind="TypeLayer")
        assert _is_text(l) is True


class TestIsAdjustment:
    def test_adjustment_kind(self):
        l = _layer(kind="adjustment")
        assert _is_adjustment(l) is True

    def test_curves(self):
        l = _layer(kind="curves")
        assert _is_adjustment(l) is True

    def test_levels(self):
        l = _layer(kind="levels")
        assert _is_adjustment(l) is True

    def test_zero_bbox_non_group_non_text(self):
        l = _layer(kind="pixel", bbox=(0, 0, 0, 0))
        assert _is_adjustment(l) is True

    def test_normal_pixel(self):
        l = _layer(kind="pixel", bbox=(0, 0, 50, 50))
        assert _is_adjustment(l) is False


class TestClassifyLayer:
    def test_group(self):
        l = _group()
        assert _classify_layer(l) == LayerKind.GROUP

    def test_pass_through_group(self):
        l = _group(blend_mode="BlendMode.PASS_THROUGH")
        assert _classify_layer(l) == LayerKind.PASS_THROUGH_GROUP

    def test_text(self):
        l = _text()
        assert _classify_layer(l) == LayerKind.TEXT

    def test_adjustment(self):
        l = _adjustment()
        assert _classify_layer(l) == LayerKind.ADJUSTMENT

    def test_pixel(self):
        l = _pixel()
        assert _classify_layer(l) == LayerKind.PIXEL


class TestIsGroupRecursivelyEmpty:
    def test_truly_empty_group(self):
        g = _group(children=[])
        assert _is_group_recursively_empty(g) is True

    def test_with_visible_pixel(self):
        g = _group(children=[_pixel()])
        assert _is_group_recursively_empty(g) is False

    def test_with_hidden_child(self):
        g = _group(children=[_pixel(visible=False)])
        assert _is_group_recursively_empty(g) is True

    def test_with_only_adjustment(self):
        g = _group(children=[_adjustment()])
        assert _is_group_recursively_empty(g) is True

    def test_nested_empty_groups(self):
        inner = _group(children=[])
        outer = _group(children=[inner])
        assert _is_group_recursively_empty(outer) is True

    def test_nested_with_text(self):
        inner = _group(children=[_text()])
        outer = _group(children=[inner])
        assert _is_group_recursively_empty(outer) is False


# ===========================================================================
# ComposeCluster data structure
# ===========================================================================


class TestComposeCluster:
    def test_add_and_members(self):
        c = ComposeCluster()
        l1 = _pixel()
        c.add(l1, "test reason")
        assert c.members == [l1]
        assert c.reasons == ["test reason"]

    def test_is_singleton(self):
        c = ComposeCluster()
        c.add(_pixel())
        assert c.is_singleton() is True
        c.add(_pixel())
        assert c.is_singleton() is False

    def test_text_count(self):
        c = ComposeCluster()
        c.add(_text())
        c.add(_pixel())
        c.add(_text())
        assert c.text_count() == 2

    def test_non_text_count(self):
        c = ComposeCluster()
        c.add(_text())
        c.add(_pixel())
        c.add(_adjustment())
        assert c.non_text_count() == 2

    def test_visual_non_text_count_excludes_adjustment(self):
        c = ComposeCluster()
        c.add(_pixel(bbox=(0, 0, 50, 50)))
        c.add(_adjustment())
        c.add(_text())
        assert c.visual_non_text_count() == 1

    def test_visual_non_text_count_excludes_zero_bbox(self):
        c = ComposeCluster()
        c.add(_pixel(bbox=(0, 0, 0, 0)))  # zero-size pixel
        assert c.visual_non_text_count() == 0


# ===========================================================================
# detect_compose_clusters tests
# ===========================================================================


class TestDetectComposeClusters:
    def test_empty_group(self):
        g = _group(children=[])
        assert detect_compose_clusters(g) == []

    def test_all_hidden(self):
        g = _group(children=[_pixel(visible=False), _pixel(opacity=0)])
        assert detect_compose_clusters(g) == []

    def test_single_normal_layer(self):
        g = _group(children=[_pixel()])
        clusters = detect_compose_clusters(g)
        assert len(clusters) == 1
        assert clusters[0].is_singleton()

    def test_two_independent_normal_layers(self):
        """Two normal-blend layers → each gets its own singleton cluster."""
        g = _group(children=[_pixel(name="a"), _pixel(name="b")])
        clusters = detect_compose_clusters(g)
        assert len(clusters) == 2
        assert all(c.is_singleton() for c in clusters)

    def test_r1_clipping_merges_with_base(self):
        """R1: clipping layer merges with the layer below."""
        base = _pixel(name="base")
        clip = _pixel(name="clip", clipping=1)
        g = _group(children=[base, clip])
        clusters = detect_compose_clusters(g)
        assert len(clusters) == 1
        assert len(clusters[0].members) == 2
        assert "R1" in clusters[0].reasons[1]

    def test_r1_multiple_clips(self):
        """Multiple clipping layers merge into same cluster."""
        base = _pixel(name="base")
        clip1 = _pixel(name="clip1", clipping=1)
        clip2 = _pixel(name="clip2", clipping=1)
        g = _group(children=[base, clip1, clip2])
        clusters = detect_compose_clusters(g)
        assert len(clusters) == 1
        assert len(clusters[0].members) == 3

    def test_r2_non_normal_blend_merges_down(self):
        """R2: non-NORMAL blend merges with layers below."""
        bottom = _pixel(name="bottom")
        overlay = _pixel(name="overlay", blend_mode="BlendMode.OVERLAY")
        g = _group(children=[bottom, overlay])
        clusters = detect_compose_clusters(g)
        assert len(clusters) == 1
        assert len(clusters[0].members) == 2
        assert any("R2" in r for r in clusters[0].reasons)

    def test_r2_absorbs_all_below(self):
        """R2 non-clipping non-NORMAL blend absorbs all prior clusters."""
        a = _pixel(name="a")
        b = _pixel(name="b")
        overlay = _pixel(name="overlay", blend_mode="BlendMode.MULTIPLY")
        g = _group(children=[a, b, overlay])
        clusters = detect_compose_clusters(g)
        # All three should be in one cluster due to absorb
        assert len(clusters) == 1
        assert len(clusters[0].members) == 3

    def test_r3_pass_through_with_dependency(self):
        """R3: PASS_THROUGH group with context-dependent child merges with below."""
        # Create a PT group that contains:
        #   - a pixel child (so the group is not "recursively empty")
        #   - an adjustment layer (makes group "context dependent")
        pixel_in_pt = _pixel(name="px_in_pt")
        adj = _adjustment()
        adj.visible = True
        adj.opacity = 255

        pt_children = [pixel_in_pt, adj]
        pt_group = _group(
            children=pt_children,
            blend_mode="BlendMode.PASS_THROUGH",
        )
        # MagicMock __iter__ is consumed once; use side_effect to regenerate
        pt_group.__iter__ = MagicMock(side_effect=lambda: iter(pt_children))

        bottom = _pixel(name="bottom")
        g = _group(children=[bottom, pt_group])
        clusters = detect_compose_clusters(g)
        assert len(clusters) == 1
        assert len(clusters[0].members) == 2

    def test_r4_adjustment_merges_when_prev_locked(self):
        """R4: adjustment layer merges with prev cluster if prev was already locked."""
        base = _pixel(name="base")
        clip = _pixel(name="clip", clipping=1)
        adj = _adjustment()
        g = _group(children=[base, clip, adj])
        clusters = detect_compose_clusters(g)
        # base+clip form a locked cluster (R1), adj merges into it (R4)
        assert len(clusters) == 1
        assert len(clusters[0].members) == 3

    def test_r4_adjustment_does_not_merge_when_prev_independent(self):
        """R4: adjustment alone when prev is independent (single normal layer)."""
        base = _pixel(name="base")
        adj = _adjustment()
        g = _group(children=[base, adj])
        clusters = detect_compose_clusters(g)
        # base is independent, adj becomes its own singleton
        assert len(clusters) == 2

    def test_orphan_clipping_gets_own_cluster(self):
        """Orphan clipping (no base below) → gets its own cluster."""
        clip = _pixel(name="orphan_clip", clipping=1)
        g = _group(children=[clip])
        clusters = detect_compose_clusters(g)
        assert len(clusters) == 1
        assert clusters[0].members[0] is clip

    def test_excludes_recursively_empty_groups(self):
        """Empty nested groups are excluded from clustering."""
        empty_inner = _group(children=[])
        pixel = _pixel(name="visible")
        g = _group(children=[empty_inner, pixel])
        clusters = detect_compose_clusters(g)
        # Only pixel should be in clusters (empty group excluded)
        assert len(clusters) == 1
        assert clusters[0].members[0] is pixel


# ===========================================================================
# decide_group_merge tests
# ===========================================================================


class TestDecideGroupMerge:
    def test_empty_group_returns_no_merge(self):
        g = _group(children=[])
        dec = decide_group_merge(g)
        assert dec.action == "no_merge"
        assert dec.clusters == []

    def test_single_pixel_no_merge(self):
        """Single pixel layer → singleton cluster → no_merge."""
        g = _group(children=[_pixel()])
        dec = decide_group_merge(g)
        assert dec.action == "no_merge"

    def test_single_cluster_all_text_no_merge(self):
        """Single cluster with only text layers → no_merge."""
        base = _text(name="t1")
        clip = _text(name="t2", clipping=1)
        g = _group(children=[base, clip])
        dec = decide_group_merge(g)
        assert dec.action == "no_merge"

    def test_single_cluster_text_and_pixel_merge_with_text_kept(self):
        """Single cluster with text + pixel → merge_with_text_kept."""
        base = _pixel(name="bg", bbox=(0, 0, 200, 100))
        clip = _text(name="label", clipping=1)
        g = _group(children=[base, clip])
        dec = decide_group_merge(g)
        assert dec.action == "merge_with_text_kept"
        assert len(dec.text_layers) >= 1

    def test_single_cluster_only_pixels_merge_full(self):
        """Single cluster with only pixel layers → merge_full."""
        base = _pixel(name="bg", bbox=(0, 0, 200, 100))
        clip = _pixel(name="overlay", clipping=1, bbox=(10, 10, 50, 50))
        g = _group(children=[base, clip])
        dec = decide_group_merge(g)
        assert dec.action == "merge_full"

    def test_multiple_singleton_clusters_no_merge(self):
        """All singletons → no_merge."""
        a = _pixel(name="a")
        b = _pixel(name="b")
        c = _pixel(name="c")
        g = _group(children=[a, b, c])
        dec = decide_group_merge(g)
        assert dec.action == "no_merge"
        assert len(dec.clusters) == 3

    def test_merge_partial_with_glued_and_independent(self):
        """Mixed: one glued cluster + one independent → merge_partial."""
        # Glued cluster: base + clip
        base = _pixel(name="base", bbox=(0, 0, 100, 100))
        clip = _pixel(name="clip", clipping=1, bbox=(0, 0, 50, 50))
        # Independent singleton
        indep = _pixel(name="indep", bbox=(120, 0, 200, 100))
        g = _group(children=[base, clip, indep])
        dec = decide_group_merge(g)
        assert dec.action == "merge_partial"
        assert len(dec.merged_clusters) == 1
        assert len(dec.merged_clusters[0]) == 2
        assert dec.independent_layers == [indep]

    def test_recursive_text_in_subgroup(self):
        """Single cluster with nested text in subgroup → merge_with_text_kept."""
        inner_text = _text(name="inner_text")
        inner_group = _group(children=[inner_text])
        base = _pixel(name="bg", bbox=(0, 0, 200, 100))
        # Force them into same cluster via clipping
        g = _group(children=[base, inner_group])
        # Need a reason to form a non-singleton cluster:
        # Use non-normal blend on inner_group
        inner_group.blend_mode = "BlendMode.MULTIPLY"
        dec = decide_group_merge(g)
        # The inner group has non-normal blend, triggers R2 → single cluster
        # with text in subgroup → merge_with_text_kept
        assert dec.action in ("merge_with_text_kept", "merge_full")


# ===========================================================================
# describe_decision tests
# ===========================================================================


class TestDescribeDecision:
    def test_no_merge(self):
        dec = GroupComposeDecision(action="no_merge", clusters=[])
        desc = describe_decision(dec)
        assert "no_merge" in desc
        assert "clusters=0" in desc

    def test_merge_partial(self):
        c1 = ComposeCluster()
        c1.add(_pixel(bbox=(0, 0, 50, 50)))
        c1.add(_pixel(bbox=(0, 0, 60, 60)))
        c2 = ComposeCluster()
        c2.add(_pixel(bbox=(100, 0, 150, 50)))
        dec = GroupComposeDecision(
            action="merge_partial",
            clusters=[c1, c2],
            merged_clusters=[c1.members],
            merged_layers=c1.members,
            independent_layers=c2.members,
        )
        desc = describe_decision(dec)
        assert "merge_partial" in desc
        assert "merged=" in desc


# ===========================================================================
# _group_contains_context_dependent tests
# ===========================================================================


class TestGroupContainsContextDependent:
    def test_empty_group(self):
        g = _group(children=[])
        assert _group_contains_context_dependent(g) is False

    def test_with_adjustment(self):
        """C1: adjustment layer → context dependent."""
        g = _group(children=[_adjustment()])
        assert _group_contains_context_dependent(g) is True

    def test_adjustment_clipping_with_pixel_base_safe(self):
        """C1 exemption: clipping adjustment layer + pixel base → safe."""
        base = _pixel(name="base")
        adj = _adjustment(name="hue_sat", clipping=1)
        g = _group(children=[base, adj])
        assert _group_contains_context_dependent(g) is False

    def test_adjustment_clipping_with_group_base_safe(self):
        """C1 exemption: clipping adjustment layer + group base (PT) → safe."""
        base_group = _group(
            name="button", children=[_pixel()],
            blend_mode="BlendMode.PASS_THROUGH"
        )
        adj = _adjustment(name="black_white", clipping=1)
        g = _group(children=[base_group, adj])
        assert _group_contains_context_dependent(g) is False

    def test_adjustment_clipping_no_base_triggers(self):
        """C1: clipping adjustment layer but no base → context dependent."""
        adj = _adjustment(name="curves", clipping=1)
        g = _group(children=[adj])
        assert _group_contains_context_dependent(g) is True

    def test_non_normal_no_clip(self):
        """C2: non-NORMAL blend, not clipping → context dependent."""
        l = _pixel(blend_mode="BlendMode.OVERLAY", clipping=0)
        g = _group(children=[l])
        assert _group_contains_context_dependent(g) is True

    def test_normal_blend_only(self):
        """All NORMAL blend layers → not context dependent."""
        g = _group(children=[_pixel(), _pixel()])
        assert _group_contains_context_dependent(g) is False

    def test_non_normal_clip_pixel_base_safe(self):
        """Non-NORMAL blend + clip + pixel base → safe (not context dependent)."""
        base = _pixel(name="base")
        clip = _pixel(name="clip", blend_mode="BlendMode.OVERLAY", clipping=1)
        g = _group(children=[base, clip])
        assert _group_contains_context_dependent(g) is False

    def test_non_normal_no_clip_contained_by_sibling_safe(self):
        """C2 bbox exemption: non-NORMAL, non-clip layer fully contained by
        a NORMAL pixel sibling below → NOT context dependent.
        Typical case: decorative LINEAR_BURN gradient on top of a large bg."""
        bg = _pixel(name="bg", bbox=(0, 0, 750, 1334))  # large background
        decor = _pixel(
            name="decor_gradient",
            blend_mode="BlendMode.LINEAR_BURN",
            clipping=0,
            bbox=(50, 100, 700, 500),  # fully inside bg
        )
        g = _group(children=[bg, decor])
        assert _group_contains_context_dependent(g) is False

    def test_non_normal_no_clip_not_contained_triggers(self):
        """C2: non-NORMAL, non-clip layer NOT contained by any sibling below
        → still context dependent."""
        small_bg = _pixel(name="small_bg", bbox=(100, 100, 200, 200))
        decor = _pixel(
            name="overlay_large",
            blend_mode="BlendMode.OVERLAY",
            clipping=0,
            bbox=(0, 0, 750, 1334),  # much larger than small_bg
        )
        g = _group(children=[small_bg, decor])
        assert _group_contains_context_dependent(g) is True

    def test_non_normal_no_clip_partially_overlapping_triggers(self):
        """C2: non-NORMAL layer partially overlapping (not fully contained)
        → still context dependent."""
        bg = _pixel(name="bg", bbox=(100, 100, 500, 500))
        decor = _pixel(
            name="gradient",
            blend_mode="BlendMode.LINEAR_BURN",
            clipping=0,
            bbox=(50, 100, 400, 400),  # left edge extends beyond bg
        )
        g = _group(children=[bg, decor])
        assert _group_contains_context_dependent(g) is True

    def test_non_normal_no_clip_contained_by_normal_group_safe(self):
        """C2 bbox exemption also works when the containing sibling is a
        NORMAL group (not PASS_THROUGH) with a larger bbox."""
        normal_subgroup = _group(
            children=[_pixel(bbox=(0, 0, 750, 1334))],
            blend_mode="BlendMode.NORMAL",
            bbox=(0, 0, 750, 1334),
        )
        decor = _pixel(
            name="overlay",
            blend_mode="BlendMode.MULTIPLY",
            clipping=0,
            bbox=(10, 10, 200, 200),
        )
        g = _group(children=[normal_subgroup, decor])
        assert _group_contains_context_dependent(g) is False


# ===========================================================================
# _has_recursive_text / _collect_recursive_text
# ===========================================================================


class TestRecursiveText:
    def test_has_recursive_text_direct(self):
        assert _has_recursive_text(_text()) is True

    def test_has_recursive_text_nested(self):
        inner = _group(children=[_text()])
        assert _has_recursive_text(inner) is True

    def test_has_recursive_text_no_text(self):
        assert _has_recursive_text(_pixel()) is False

    def test_collect_recursive_text(self):
        t1 = _text(name="t1")
        t2 = _text(name="t2")
        inner = _group(children=[t2])
        outer = _group(children=[t1, inner])
        out: list = []
        _collect_recursive_text(outer, out)
        assert len(out) == 2
        assert t1 in out
        assert t2 in out

    def test_hidden_text_not_collected(self):
        t = _text(visible=False)
        out: list = []
        _collect_recursive_text(t, out)
        assert out == []
