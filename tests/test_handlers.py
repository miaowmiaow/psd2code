"""Tests for core.extract.handlers — Chain of Responsibility for layer export.

All tests use mock layer objects and mock LayerExporter (no PSD I/O required).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from core.extract.handlers import (
    HandlerContext,
    HandlerResult,
    LayerHandler,
    ClippingGroupHandler,
    InvisibleLayerHandler,
    GroupHandler,
    LeafLayerHandler,
    DEFAULT_HANDLERS,
    run_handlers,
)


# ===================================================================
# Helpers
# ===================================================================

def _mock_exporter():
    """Create a minimal mock LayerExporter."""
    exp = MagicMock()
    exp.skipped_count = 0
    exp.exported_count = 0
    exp._z_counter = 0
    exp._ancestor_group_masks = []
    exp.canvas_width = 375
    exp.canvas_height = 812
    return exp


def _mock_layer(
    kind: str = "pixel",
    is_group: bool = False,
    visible: bool = True,
    opacity: int = 255,
    name: str = "TestLayer",
    blend_mode=None,
    left: int = 0,
    top: int = 0,
    width: int = 100,
    height: int = 100,
    children=None,
    mask=None,
):
    """Create a mock PSD layer."""
    from psd_tools.constants import BlendMode
    layer = MagicMock()
    layer.kind = kind
    layer.is_group.return_value = is_group
    layer.visible = visible
    layer.opacity = opacity
    layer.name = name
    layer.blend_mode = blend_mode or BlendMode.NORMAL
    layer.left = left
    layer.top = top
    layer.width = width
    layer.height = height
    layer.bbox = (left, top, left + width, top + height)
    layer.mask = mask

    if children is not None:
        layer.__iter__ = lambda self, _c=children: iter(_c)
        layer.__len__ = MagicMock(return_value=len(children))
    else:
        layer.__iter__ = lambda self: iter([])
        layer.__len__ = MagicMock(return_value=0)

    return layer


def _make_ctx(item, exporter=None, depth=0, parent_name="", parent_left=0, parent_top=0):
    """Build a HandlerContext."""
    return HandlerContext(
        exporter=exporter or _mock_exporter(),
        item=item,
        depth=depth,
        parent_name=parent_name,
        parent_left=parent_left,
        parent_top=parent_top,
        parent_clip_bbox=None,
    )


# ===================================================================
# HandlerResult
# ===================================================================

class TestHandlerResult:
    def test_default_values(self):
        r = HandlerResult()
        assert r.produced == []
        assert r.handled is False

    def test_custom_values(self):
        r = HandlerResult(produced=[{"id": "test"}], handled=True)
        assert len(r.produced) == 1
        assert r.handled is True


# ===================================================================
# ClippingGroupHandler
# ===================================================================

class TestClippingGroupHandler:
    def test_can_handle_tuple(self):
        h = ClippingGroupHandler()
        base = _mock_layer()
        ctx = _make_ctx(item=(base, []))
        assert h.can_handle(ctx) is True

    def test_cannot_handle_non_tuple(self):
        h = ClippingGroupHandler()
        ctx = _make_ctx(item=_mock_layer())
        assert h.can_handle(ctx) is False

    def test_hidden_base_skips(self, capsys):
        h = ClippingGroupHandler()
        exp = _mock_exporter()
        base = _mock_layer(visible=False, name="hidden_base")
        clipped = [_mock_layer(name="clip1")]
        ctx = _make_ctx(item=(base, clipped), exporter=exp)
        r = h.handle(ctx)
        assert r.handled is True
        assert r.produced == []
        assert exp.skipped_count == 2  # base + 1 clipped

    def test_zero_opacity_base_skips(self, capsys):
        h = ClippingGroupHandler()
        exp = _mock_exporter()
        base = _mock_layer(opacity=0, name="transparent_base")
        clipped = [_mock_layer(), _mock_layer()]
        ctx = _make_ctx(item=(base, clipped), exporter=exp)
        r = h.handle(ctx)
        assert r.handled is True
        assert exp.skipped_count == 3  # base + 2 clipped

    def test_no_visible_clipped_exports_base(self, capsys):
        """When all clipped layers are invisible, only base is exported."""
        h = ClippingGroupHandler()
        exp = _mock_exporter()
        base = _mock_layer(visible=True, name="base", is_group=False)
        # All clipped invisible
        clipped = [_mock_layer(visible=False), _mock_layer(opacity=0)]
        exp._export_single_layer.return_value = {"id": "base-1", "type": "image"}
        ctx = _make_ctx(item=(base, clipped), exporter=exp)
        r = h.handle(ctx)
        assert r.handled is True
        assert len(r.produced) == 1
        assert r.produced[0]["id"] == "base-1"

    def test_base_is_group_with_clipped(self, capsys):
        """When base is group + visible clipped layers, exports group + clipped."""
        h = ClippingGroupHandler()
        exp = _mock_exporter()
        base = _mock_layer(is_group=True, name="group_base", visible=True)
        clip1 = _mock_layer(visible=True, opacity=200, name="clip1")
        exp.export_layers.return_value = [{"id": "child1"}]
        exp._export_clipped_layer_against_group_base.return_value = {"id": "clipped1"}
        ctx = _make_ctx(item=(base, [clip1]), exporter=exp)
        r = h.handle(ctx)
        assert r.handled is True
        # group_info + clipped
        assert len(r.produced) == 2

    def test_base_leaf_merge_success(self, capsys):
        """Non-group base + clipped → merged via _merge_clipping_group."""
        h = ClippingGroupHandler()
        exp = _mock_exporter()
        base = _mock_layer(is_group=False, name="leaf_base", visible=True)
        clip1 = _mock_layer(visible=True, opacity=200, name="clip1")
        exp._merge_clipping_group.return_value = {"id": "merged-1", "type": "image"}
        ctx = _make_ctx(item=(base, [clip1]), exporter=exp)
        r = h.handle(ctx)
        assert r.handled is True
        assert len(r.produced) == 1
        assert r.produced[0]["id"] == "merged-1"

    def test_base_leaf_merge_failure_fallback(self, capsys):
        """When merge fails, fallback to individual export."""
        h = ClippingGroupHandler()
        exp = _mock_exporter()
        base = _mock_layer(is_group=False, name="leaf_base", visible=True)
        clip1 = _mock_layer(visible=True, opacity=200, name="clip1")
        exp._merge_clipping_group.return_value = None  # merge failed
        exp._export_single_layer.return_value = {"id": "single-1"}
        ctx = _make_ctx(item=(base, [clip1]), exporter=exp)
        r = h.handle(ctx)
        assert r.handled is True
        # base + clip1 exported individually
        assert len(r.produced) == 2


# ===================================================================
# InvisibleLayerHandler
# ===================================================================

class TestInvisibleLayerHandler:
    def test_can_handle_invisible(self):
        h = InvisibleLayerHandler()
        layer = _mock_layer(visible=False)
        ctx = _make_ctx(item=layer)
        assert h.can_handle(ctx) is True

    def test_can_handle_zero_opacity(self):
        h = InvisibleLayerHandler()
        layer = _mock_layer(visible=True, opacity=0)
        ctx = _make_ctx(item=layer)
        assert h.can_handle(ctx) is True

    def test_cannot_handle_visible(self):
        h = InvisibleLayerHandler()
        layer = _mock_layer(visible=True, opacity=255)
        ctx = _make_ctx(item=layer)
        assert h.can_handle(ctx) is False

    def test_cannot_handle_tuple(self):
        h = InvisibleLayerHandler()
        ctx = _make_ctx(item=(_mock_layer(), []))
        assert h.can_handle(ctx) is False

    def test_handle_invisible(self, capsys):
        h = InvisibleLayerHandler()
        exp = _mock_exporter()
        layer = _mock_layer(visible=False, name="hidden")
        ctx = _make_ctx(item=layer, exporter=exp)
        r = h.handle(ctx)
        assert r.handled is True
        assert r.produced == []
        assert exp.skipped_count == 1

    def test_handle_zero_opacity(self, capsys):
        h = InvisibleLayerHandler()
        exp = _mock_exporter()
        layer = _mock_layer(visible=True, opacity=0, name="transparent")
        ctx = _make_ctx(item=layer, exporter=exp)
        r = h.handle(ctx)
        assert r.handled is True
        assert exp.skipped_count == 1


# ===================================================================
# GroupHandler
# ===================================================================

class TestGroupHandler:
    def test_can_handle_group(self):
        h = GroupHandler()
        layer = _mock_layer(is_group=True)
        ctx = _make_ctx(item=layer)
        assert h.can_handle(ctx) is True

    def test_cannot_handle_non_group(self):
        h = GroupHandler()
        layer = _mock_layer(is_group=False)
        ctx = _make_ctx(item=layer)
        assert h.can_handle(ctx) is False

    def test_cannot_handle_tuple(self):
        h = GroupHandler()
        ctx = _make_ctx(item=(_mock_layer(), []))
        assert h.can_handle(ctx) is False

    @patch("core.extract.compose_cluster.describe_decision")
    @patch("core.extract.compose_cluster.decide_group_merge")
    def test_no_merge_path(self, mock_decide, mock_describe, capsys):
        """no_merge decision → recursive export_layers."""
        decision = MagicMock()
        decision.action = "no_merge"
        decision.merged_clusters = []
        mock_decide.return_value = decision
        mock_describe.return_value = "no_merge"

        h = GroupHandler()
        exp = _mock_exporter()
        child = _mock_layer(name="child1")
        layer = _mock_layer(is_group=True, name="parent", children=[child])
        layer.mask = None
        exp.export_layers.return_value = [{"id": "child-1"}]

        ctx = _make_ctx(item=layer, exporter=exp)

        with patch("config.Config.CONSTRAIN_GROUP_TO_CANVAS", False):
            r = h.handle(ctx)

        assert r.handled is True
        assert len(r.produced) == 1
        assert r.produced[0]["type"] == "group"
        assert r.produced[0]["children"] == [{"id": "child-1"}]

    @patch("core.extract.compose_cluster.describe_decision")
    @patch("core.extract.compose_cluster.decide_group_merge")
    def test_merge_full_success(self, mock_decide, mock_describe, capsys):
        """merge_full → single image output."""
        decision = MagicMock()
        decision.action = "merge_full"
        mock_decide.return_value = decision
        mock_describe.return_value = "merge_full"

        h = GroupHandler()
        exp = _mock_exporter()
        layer = _mock_layer(is_group=True, name="merged_group")
        layer.mask = None
        exp._merge_group_as_single_image.return_value = {"id": "merged-img", "type": "image"}

        ctx = _make_ctx(item=layer, exporter=exp)
        r = h.handle(ctx)
        assert r.handled is True
        assert len(r.produced) == 1
        assert r.produced[0]["id"] == "merged-img"

    @patch("core.extract.compose_cluster.describe_decision")
    @patch("core.extract.compose_cluster.decide_group_merge")
    def test_merge_full_fallback(self, mock_decide, mock_describe, capsys):
        """merge_full fails → fallback to recursive export."""
        decision = MagicMock()
        decision.action = "merge_full"
        decision.merged_clusters = []
        mock_decide.return_value = decision
        mock_describe.return_value = "merge_full"

        h = GroupHandler()
        exp = _mock_exporter()
        layer = _mock_layer(is_group=True, name="fallback_group")
        layer.mask = None
        exp._merge_group_as_single_image.return_value = None  # fail
        exp.export_layers.return_value = [{"id": "child-fallback"}]

        ctx = _make_ctx(item=layer, exporter=exp)

        with patch("config.Config.CONSTRAIN_GROUP_TO_CANVAS", False):
            r = h.handle(ctx)

        assert r.handled is True
        assert r.produced[0]["type"] == "group"

    @patch("core.extract.compose_cluster.describe_decision")
    @patch("core.extract.compose_cluster.decide_group_merge")
    def test_merge_with_text_kept(self, mock_decide, mock_describe, capsys):
        """merge_with_text_kept → bg merged + text children kept."""
        decision = MagicMock()
        decision.action = "merge_with_text_kept"
        decision.merged_clusters = []
        mock_decide.return_value = decision
        mock_describe.return_value = "merge_with_text_kept"

        h = GroupHandler()
        exp = _mock_exporter()
        text_child = _mock_layer(kind="type", name="label", visible=True)
        img_child = _mock_layer(kind="pixel", name="bg", visible=True)
        layer = _mock_layer(is_group=True, name="btn", children=[img_child, text_child])
        layer.mask = None
        exp._merge_group_non_text_as_image.return_value = {"id": "bg-merged"}
        exp.export_layers.return_value = [{"id": "text-child"}]

        ctx = _make_ctx(item=layer, exporter=exp)

        with patch("config.Config.CONSTRAIN_GROUP_TO_CANVAS", False):
            r = h.handle(ctx)

        assert r.handled is True
        group_info = r.produced[0]
        assert group_info["type"] == "group"
        # bg merged should be first child
        assert group_info["children"][0]["id"] == "bg-merged"


# ===================================================================
# LeafLayerHandler
# ===================================================================

class TestLeafLayerHandler:
    def test_can_handle_non_tuple(self):
        h = LeafLayerHandler()
        layer = _mock_layer()
        ctx = _make_ctx(item=layer)
        assert h.can_handle(ctx) is True

    def test_cannot_handle_tuple(self):
        h = LeafLayerHandler()
        ctx = _make_ctx(item=(_mock_layer(), []))
        assert h.can_handle(ctx) is False

    def test_handle_success(self):
        h = LeafLayerHandler()
        exp = _mock_exporter()
        layer = _mock_layer(name="leaf")
        exp._export_single_layer.return_value = {"id": "leaf-1", "type": "image"}
        ctx = _make_ctx(item=layer, exporter=exp)
        r = h.handle(ctx)
        assert r.handled is True
        assert len(r.produced) == 1

    def test_handle_returns_none(self):
        h = LeafLayerHandler()
        exp = _mock_exporter()
        layer = _mock_layer(name="empty")
        exp._export_single_layer.return_value = None
        ctx = _make_ctx(item=layer, exporter=exp)
        r = h.handle(ctx)
        assert r.handled is True
        assert r.produced == []


# ===================================================================
# DEFAULT_HANDLERS ordering
# ===================================================================

class TestDefaultHandlers:
    def test_handler_count(self):
        assert len(DEFAULT_HANDLERS) == 4

    def test_handler_order(self):
        assert isinstance(DEFAULT_HANDLERS[0], ClippingGroupHandler)
        assert isinstance(DEFAULT_HANDLERS[1], InvisibleLayerHandler)
        assert isinstance(DEFAULT_HANDLERS[2], GroupHandler)
        assert isinstance(DEFAULT_HANDLERS[3], LeafLayerHandler)


# ===================================================================
# run_handlers
# ===================================================================

class TestRunHandlers:
    def test_clipping_group_priority(self):
        """Tuple items should be handled by ClippingGroupHandler first."""
        exp = _mock_exporter()
        base = _mock_layer(visible=False, name="base")
        ctx = _make_ctx(item=(base, []), exporter=exp)
        result = run_handlers(ctx)
        assert result == []  # hidden base → skipped

    def test_invisible_before_group(self):
        """Invisible group should be caught by InvisibleLayerHandler, not GroupHandler."""
        exp = _mock_exporter()
        layer = _mock_layer(is_group=True, visible=False, name="hidden_group")
        ctx = _make_ctx(item=layer, exporter=exp)
        result = run_handlers(ctx)
        assert result == []
        assert exp.skipped_count == 1

    def test_leaf_fallback(self):
        """Visible leaf layers handled by LeafLayerHandler."""
        exp = _mock_exporter()
        layer = _mock_layer(is_group=False, visible=True, name="img")
        exp._export_single_layer.return_value = {"id": "img-1"}
        ctx = _make_ctx(item=layer, exporter=exp)
        result = run_handlers(ctx)
        assert len(result) == 1
        assert result[0]["id"] == "img-1"

    def test_custom_handlers(self):
        """Custom handler list is respected."""

        class AlwaysSkip(LayerHandler):
            def can_handle(self, ctx):
                return True
            def handle(self, ctx):
                return HandlerResult(produced=[{"id": "custom"}], handled=True)

        ctx = _make_ctx(item=_mock_layer())
        result = run_handlers(ctx, handlers=[AlwaysSkip()])
        assert result == [{"id": "custom"}]

    def test_no_handler_matches(self):
        """If no handler can handle → empty list."""

        class NeverHandle(LayerHandler):
            def can_handle(self, ctx):
                return False
            def handle(self, ctx):
                return HandlerResult()

        ctx = _make_ctx(item=_mock_layer())
        result = run_handlers(ctx, handlers=[NeverHandle()])
        assert result == []


# ===================================================================
# GroupHandler._collect_non_text_recursive
# ===================================================================

class TestCollectNonTextRecursive:
    def test_flat_group(self):
        """Collect non-text leaves from a flat group."""
        text = _mock_layer(kind="type", visible=True, is_group=False)
        img1 = _mock_layer(kind="pixel", visible=True, is_group=False)
        img2 = _mock_layer(kind="pixel", visible=True, is_group=False)
        group = _mock_layer(is_group=True, children=[text, img1, img2])
        result = GroupHandler._collect_non_text_recursive(group)
        assert len(result) == 2

    def test_nested_group(self):
        """Collect non-text leaves recursively."""
        inner_text = _mock_layer(kind="type", visible=True, is_group=False)
        inner_img = _mock_layer(kind="pixel", visible=True, is_group=False)
        inner_group = _mock_layer(is_group=True, visible=True, children=[inner_text, inner_img])
        outer_img = _mock_layer(kind="pixel", visible=True, is_group=False)
        group = _mock_layer(is_group=True, children=[inner_group, outer_img])
        result = GroupHandler._collect_non_text_recursive(group)
        assert len(result) == 2  # inner_img + outer_img

    def test_hidden_children_excluded(self):
        """Hidden children should not be collected."""
        img = _mock_layer(kind="pixel", visible=False, is_group=False)
        group = _mock_layer(is_group=True, children=[img])
        result = GroupHandler._collect_non_text_recursive(group)
        assert len(result) == 0

    def test_zero_opacity_excluded(self):
        """Zero opacity children excluded."""
        img = _mock_layer(kind="pixel", visible=True, opacity=0, is_group=False)
        group = _mock_layer(is_group=True, children=[img])
        result = GroupHandler._collect_non_text_recursive(group)
        assert len(result) == 0
