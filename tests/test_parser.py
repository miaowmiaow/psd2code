"""Tests for core.psd.parser — PSD → IR parser.

Covers:
- _bbox_from_legacy: edge cases (missing keys, negative width)
- _kind_from_legacy: type string → NodeKind mapping
- _node_from_legacy: GroupNode / TextNode / ShapeNode / ImageNode branches
- _next_auto_id / _LEGACY_ID_COUNTER: auto-increment determinism
- parse_psd_to_ir: full mock integration (PSD → Document)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from core.ir import (
    BBox,
    Document,
    GroupNode,
    ImageNode,
    NodeKind,
    ShapeNode,
    TextNode,
)
from core.psd.parser import (
    _LEGACY_ID_COUNTER,
    _bbox_from_legacy,
    _kind_from_legacy,
    _next_auto_id,
    _node_from_legacy,
    parse_psd_to_ir,
)


# ═══════════════════════════════════════════════════════════════════════════════
# _bbox_from_legacy
# ═══════════════════════════════════════════════════════════════════════════════

class TestBboxFromLegacy:
    def test_normal_values(self):
        d = {"left": 10, "top": 20, "width": 100, "height": 50}
        bbox = _bbox_from_legacy(d)
        assert bbox.left == 10
        assert bbox.top == 20
        assert bbox.right == 110
        assert bbox.bottom == 70

    def test_missing_keys_default_zero(self):
        bbox = _bbox_from_legacy({})
        assert bbox.left == 0
        assert bbox.top == 0
        assert bbox.right == 0
        assert bbox.bottom == 0

    def test_negative_width_clamped(self):
        """Negative width should be clamped to 0 (right = left + max(0, width))."""
        d = {"left": 50, "top": 30, "width": -10, "height": -5}
        bbox = _bbox_from_legacy(d)
        assert bbox.right == 50  # left + max(0, -10) = 50
        assert bbox.bottom == 30  # top + max(0, -5) = 30

    def test_float_values_converted_to_int(self):
        d = {"left": 1.5, "top": 2.7, "width": 10.9, "height": 20.1}
        bbox = _bbox_from_legacy(d)
        assert bbox.left == 1
        assert bbox.top == 2
        assert bbox.right == 11  # 1 + 10
        assert bbox.bottom == 22  # 2 + 20

    def test_string_values_converted(self):
        d = {"left": "5", "top": "10", "width": "100", "height": "200"}
        bbox = _bbox_from_legacy(d)
        assert bbox.left == 5
        assert bbox.right == 105

    def test_zero_dimensions(self):
        d = {"left": 100, "top": 200, "width": 0, "height": 0}
        bbox = _bbox_from_legacy(d)
        assert bbox.width == 0
        assert bbox.height == 0


# ═══════════════════════════════════════════════════════════════════════════════
# _kind_from_legacy
# ═══════════════════════════════════════════════════════════════════════════════

class TestKindFromLegacy:
    def test_group(self):
        assert _kind_from_legacy({"type": "group"}) == NodeKind.GROUP.value

    def test_group_case_insensitive(self):
        assert _kind_from_legacy({"type": "Group"}) == NodeKind.GROUP.value
        assert _kind_from_legacy({"type": "GROUP"}) == NodeKind.GROUP.value

    def test_text(self):
        assert _kind_from_legacy({"type": "text"}) == NodeKind.TEXT.value

    def test_shape(self):
        assert _kind_from_legacy({"type": "shape"}) == NodeKind.SHAPE.value

    def test_image_default(self):
        assert _kind_from_legacy({"type": "image"}) == NodeKind.IMAGE.value

    def test_unknown_defaults_to_image(self):
        assert _kind_from_legacy({"type": "pixel"}) == NodeKind.IMAGE.value
        assert _kind_from_legacy({"type": "unknown"}) == NodeKind.IMAGE.value
        assert _kind_from_legacy({"type": ""}) == NodeKind.IMAGE.value

    def test_missing_type_defaults_to_image(self):
        assert _kind_from_legacy({}) == NodeKind.IMAGE.value


# ═══════════════════════════════════════════════════════════════════════════════
# _next_auto_id / _LEGACY_ID_COUNTER
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutoId:
    def setup_method(self):
        """Reset counter before each test."""
        _LEGACY_ID_COUNTER["n"] = 0

    def test_sequential_ids(self):
        assert _next_auto_id() == "node-1"
        assert _next_auto_id() == "node-2"
        assert _next_auto_id() == "node-3"

    def test_counter_persists(self):
        for _ in range(5):
            _next_auto_id()
        assert _LEGACY_ID_COUNTER["n"] == 5

    def test_reset_resets(self):
        _next_auto_id()
        _LEGACY_ID_COUNTER["n"] = 0
        assert _next_auto_id() == "node-1"


# ═══════════════════════════════════════════════════════════════════════════════
# _node_from_legacy
# ═══════════════════════════════════════════════════════════════════════════════

class TestNodeFromLegacy:
    def setup_method(self):
        _LEGACY_ID_COUNTER["n"] = 0

    def test_group_node(self):
        d = {
            "type": "group",
            "id": "grp-1",
            "name": "Header",
            "left": 0,
            "top": 0,
            "width": 375,
            "height": 64,
            "opacity": 1.0,
            "children": [
                {"type": "text", "id": "t1", "name": "Title", "left": 10, "top": 5,
                 "width": 200, "height": 30, "text": "Hello"},
            ],
        }
        node = _node_from_legacy(d)
        assert isinstance(node, GroupNode)
        assert node.id == "grp-1"
        assert node.name == "Header"
        assert node.kind == NodeKind.GROUP
        assert len(node.children) == 1
        assert isinstance(node.children[0], TextNode)

    def test_text_node(self):
        d = {
            "type": "text",
            "id": "txt-1",
            "name": "Label",
            "left": 20,
            "top": 30,
            "width": 100,
            "height": 20,
            "opacity": 0.8,
            "text": "Click me",
        }
        node = _node_from_legacy(d)
        assert isinstance(node, TextNode)
        assert node.id == "txt-1"
        assert node.text == "Click me"
        assert node.style.opacity == 0.8

    def test_shape_node(self):
        d = {
            "type": "shape",
            "id": "shp-1",
            "name": "Rect",
            "left": 0,
            "top": 0,
            "width": 50,
            "height": 50,
        }
        node = _node_from_legacy(d)
        assert isinstance(node, ShapeNode)
        assert node.id == "shp-1"
        assert node.kind == NodeKind.SHAPE

    def test_image_node_with_src(self):
        d = {
            "type": "image",
            "id": "img-1",
            "name": "Photo",
            "left": 0,
            "top": 0,
            "width": 200,
            "height": 150,
            "src": "images/photo.png",
        }
        node = _node_from_legacy(d)
        assert isinstance(node, ImageNode)
        assert node.asset.src == "images/photo.png"
        assert node.asset.width == 200
        assert node.asset.height == 150

    def test_image_node_fallback_image_key(self):
        d = {
            "type": "image",
            "name": "bg",
            "left": 0,
            "top": 0,
            "width": 100,
            "height": 100,
            "image": "images/bg.png",
        }
        node = _node_from_legacy(d)
        assert isinstance(node, ImageNode)
        assert node.asset.src == "images/bg.png"

    def test_image_node_fallback_path_key(self):
        d = {
            "type": "image",
            "name": "icon",
            "left": 0,
            "top": 0,
            "width": 32,
            "height": 32,
            "path": "images/icon.png",
        }
        node = _node_from_legacy(d)
        assert node.asset.src == "images/icon.png"

    def test_image_node_no_src(self):
        """Image node without src/image/path should have empty AssetRef."""
        d = {
            "type": "image",
            "name": "empty",
            "left": 0,
            "top": 0,
            "width": 50,
            "height": 50,
        }
        node = _node_from_legacy(d)
        assert isinstance(node, ImageNode)
        assert node.asset.src == ""

    def test_auto_id_when_id_missing(self):
        """When legacy dict has no 'id', auto-generate node-N."""
        d = {"type": "text", "name": "No ID", "left": 0, "top": 0, "width": 10, "height": 10}
        node = _node_from_legacy(d)
        assert node.id == "node-1"

    def test_auto_id_when_id_is_empty(self):
        d = {"type": "text", "id": "", "name": "Empty ID", "left": 0, "top": 0, "width": 10, "height": 10}
        node = _node_from_legacy(d)
        assert node.id == "node-1"

    def test_meta_contains_legacy(self):
        d = {"type": "image", "name": "X", "left": 0, "top": 0, "width": 10, "height": 10, "custom_key": 42}
        node = _node_from_legacy(d)
        assert node.meta["legacy"] is d

    def test_nested_group_recursive(self):
        d = {
            "type": "group",
            "id": "outer",
            "name": "Outer",
            "left": 0,
            "top": 0,
            "width": 375,
            "height": 812,
            "children": [
                {
                    "type": "group",
                    "id": "inner",
                    "name": "Inner",
                    "left": 10,
                    "top": 10,
                    "width": 100,
                    "height": 100,
                    "children": [
                        {"type": "image", "id": "leaf", "name": "Leaf",
                         "left": 20, "top": 20, "width": 50, "height": 50, "src": "a.png"},
                    ],
                },
            ],
        }
        node = _node_from_legacy(d)
        assert isinstance(node, GroupNode)
        inner = node.children[0]
        assert isinstance(inner, GroupNode)
        assert inner.id == "inner"
        leaf = inner.children[0]
        assert isinstance(leaf, ImageNode)
        assert leaf.asset.src == "a.png"

    def test_opacity_default_1(self):
        d = {"type": "text", "name": "X", "left": 0, "top": 0, "width": 10, "height": 10}
        node = _node_from_legacy(d)
        assert node.style.opacity == 1.0

    def test_zero_dimension_image_asset(self):
        """Image with zero width/height should have None in AssetRef dimensions."""
        d = {"type": "image", "name": "Zero", "left": 0, "top": 0, "width": 0, "height": 0, "src": "z.png"}
        node = _node_from_legacy(d)
        assert node.asset.width is None  # bbox.width == 0 → width or None == None
        assert node.asset.height is None


# ═══════════════════════════════════════════════════════════════════════════════
# parse_psd_to_ir (mock integration)
# ═══════════════════════════════════════════════════════════════════════════════

class TestParsePsdToIr:
    """Integration tests for parse_psd_to_ir with mocked PSD/LayerExporter."""

    @patch("core.psd.parser.LayerExporter")
    @patch("core.psd.parser.PSDImage")
    def test_basic_parse(self, mock_psd_cls, mock_exporter_cls):
        """parse_psd_to_ir produces a valid Document."""
        # Mock PSD
        mock_psd = MagicMock()
        mock_psd.width = 375
        mock_psd.height = 812
        mock_psd_cls.open.return_value = mock_psd

        # Mock exporter
        mock_exporter = MagicMock()
        mock_exporter.exported_count = 5
        mock_exporter.skipped_count = 1
        mock_exporter.export_layers.return_value = [
            {
                "type": "image",
                "id": "layer-1",
                "name": "bg",
                "left": 0,
                "top": 0,
                "width": 375,
                "height": 812,
                "opacity": 1.0,
                "src": "images/bg.png",
            },
            {
                "type": "text",
                "id": "layer-2",
                "name": "title",
                "left": 20,
                "top": 100,
                "width": 200,
                "height": 30,
                "opacity": 1.0,
                "text": "Welcome",
            },
        ]
        mock_exporter_cls.return_value = mock_exporter

        doc, exporter, legacy_tree = parse_psd_to_ir("/fake/test.psd", "/fake/out")

        assert isinstance(doc, Document)
        assert doc.width == 375
        assert doc.height == 812
        assert doc.source_psd == "/fake/test.psd"
        assert doc.title == "test"
        assert doc.root.id == "root"
        assert len(doc.root.children) == 2
        assert isinstance(doc.root.children[0], ImageNode)
        assert isinstance(doc.root.children[1], TextNode)
        assert doc.meta["exported_count"] == 5
        assert doc.meta["skipped_count"] == 1

    @patch("core.psd.parser.LayerExporter")
    @patch("core.psd.parser.PSDImage")
    def test_counter_reset_between_calls(self, mock_psd_cls, mock_exporter_cls):
        """Counter resets at start of parse, ensuring deterministic IDs."""
        mock_psd = MagicMock()
        mock_psd.width = 100
        mock_psd.height = 100
        mock_psd_cls.open.return_value = mock_psd

        mock_exporter = MagicMock()
        mock_exporter.exported_count = 0
        mock_exporter.skipped_count = 0
        # No explicit 'id' → will use auto-generated IDs
        mock_exporter.export_layers.return_value = [
            {"type": "text", "name": "a", "left": 0, "top": 0, "width": 10, "height": 10},
            {"type": "text", "name": "b", "left": 0, "top": 0, "width": 10, "height": 10},
        ]
        mock_exporter_cls.return_value = mock_exporter

        doc1, _, _ = parse_psd_to_ir("/fake/a.psd", "/fake/out")
        doc2, _, _ = parse_psd_to_ir("/fake/b.psd", "/fake/out")

        # Both calls should produce same IDs (counter reset)
        assert doc1.root.children[0].id == doc2.root.children[0].id
        assert doc1.root.children[1].id == doc2.root.children[1].id

    @patch("core.psd.parser.LayerExporter")
    @patch("core.psd.parser.PSDImage")
    def test_empty_layers(self, mock_psd_cls, mock_exporter_cls):
        """Empty PSD (no layers) → Document with empty root.children."""
        mock_psd = MagicMock()
        mock_psd.width = 200
        mock_psd.height = 300
        mock_psd_cls.open.return_value = mock_psd

        mock_exporter = MagicMock()
        mock_exporter.exported_count = 0
        mock_exporter.skipped_count = 0
        mock_exporter.export_layers.return_value = []
        mock_exporter_cls.return_value = mock_exporter

        doc, _, legacy = parse_psd_to_ir("/fake/empty.psd", "/fake/out")
        assert len(doc.root.children) == 0
        assert legacy == []

    @patch("core.psd.parser.LayerExporter")
    @patch("core.psd.parser.PSDImage")
    def test_nested_groups(self, mock_psd_cls, mock_exporter_cls):
        """Nested groups are recursively converted."""
        mock_psd = MagicMock()
        mock_psd.width = 375
        mock_psd.height = 812
        mock_psd_cls.open.return_value = mock_psd

        mock_exporter = MagicMock()
        mock_exporter.exported_count = 3
        mock_exporter.skipped_count = 0
        mock_exporter.export_layers.return_value = [
            {
                "type": "group",
                "id": "g1",
                "name": "Section",
                "left": 0,
                "top": 0,
                "width": 375,
                "height": 400,
                "children": [
                    {"type": "image", "id": "i1", "name": "card-bg",
                     "left": 10, "top": 10, "width": 355, "height": 380,
                     "src": "images/card.png"},
                    {"type": "text", "id": "t1", "name": "card-title",
                     "left": 20, "top": 20, "width": 300, "height": 30,
                     "text": "Card Title"},
                ],
            },
        ]
        mock_exporter_cls.return_value = mock_exporter

        doc, _, _ = parse_psd_to_ir("/fake/nested.psd", "/fake/out")
        grp = doc.root.children[0]
        assert isinstance(grp, GroupNode)
        assert grp.id == "g1"
        assert len(grp.children) == 2
        assert isinstance(grp.children[0], ImageNode)
        assert isinstance(grp.children[1], TextNode)
        assert grp.children[1].text == "Card Title"

    @patch("core.psd.parser.LayerExporter")
    @patch("core.psd.parser.PSDImage")
    def test_provided_psd_object(self, mock_psd_cls, mock_exporter_cls):
        """When psd object is provided, PSDImage.open is NOT called."""
        mock_psd = MagicMock()
        mock_psd.width = 100
        mock_psd.height = 200

        mock_exporter = MagicMock()
        mock_exporter.exported_count = 0
        mock_exporter.skipped_count = 0
        mock_exporter.export_layers.return_value = []
        mock_exporter_cls.return_value = mock_exporter

        doc, _, _ = parse_psd_to_ir("/fake/x.psd", "/fake/out", psd=mock_psd)
        mock_psd_cls.open.assert_not_called()
        assert doc.width == 100
        assert doc.height == 200

    @patch("core.psd.parser.LayerExporter")
    @patch("core.psd.parser.PSDImage")
    def test_verify_export_called(self, mock_psd_cls, mock_exporter_cls):
        """verify_export() is called after export_layers()."""
        mock_psd = MagicMock()
        mock_psd.width = 100
        mock_psd.height = 100
        mock_psd_cls.open.return_value = mock_psd

        mock_exporter = MagicMock()
        mock_exporter.exported_count = 0
        mock_exporter.skipped_count = 0
        mock_exporter.export_layers.return_value = []
        mock_exporter_cls.return_value = mock_exporter

        parse_psd_to_ir("/fake/v.psd", "/fake/out")
        mock_exporter.verify_export.assert_called_once()

    @patch("core.psd.parser.LayerExporter")
    @patch("core.psd.parser.PSDImage")
    def test_legacy_roots_in_root_meta(self, mock_psd_cls, mock_exporter_cls):
        """doc.root.meta['legacy_roots'] stores the original tree."""
        mock_psd = MagicMock()
        mock_psd.width = 100
        mock_psd.height = 100
        mock_psd_cls.open.return_value = mock_psd

        legacy_tree = [{"type": "image", "id": "x", "name": "X",
                        "left": 0, "top": 0, "width": 50, "height": 50}]
        mock_exporter = MagicMock()
        mock_exporter.exported_count = 1
        mock_exporter.skipped_count = 0
        mock_exporter.export_layers.return_value = legacy_tree
        mock_exporter_cls.return_value = mock_exporter

        doc, _, ret_legacy = parse_psd_to_ir("/fake/m.psd", "/fake/out")
        assert doc.root.meta["legacy_roots"] is legacy_tree
        assert ret_legacy is legacy_tree
