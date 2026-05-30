# -*- coding: utf-8 -*-
"""Unit tests for core.ir.adapters (P4).

Tests cover:
  - to_legacy_layers: returns legacy_roots from meta when present
  - to_legacy_layers: synthesizes legacy tree from IR nodes when no legacy_roots
  - _legacy_from_node: uses node.meta["legacy"] shortcut when present
  - _legacy_from_node: constructs correct dict for GroupNode vs leaf nodes
"""

from __future__ import annotations

import pytest

from core.ir.adapters import to_legacy_layers, _legacy_from_node
from core.ir.styles import BBox, Style
from core.ir.nodes import GroupNode, ImageNode, TextNode, ShapeNode
from core.ir.assets import AssetRef
from core.ir.document import Document


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _style(left=0, top=0, right=100, bottom=100, **kw):
    return Style(bbox=BBox(left=left, top=top, right=right, bottom=bottom), **kw)


def _doc(root: GroupNode, width=375, height=812) -> Document:
    return Document(width=width, height=height, root=root)


# ===========================================================================
# to_legacy_layers tests
# ===========================================================================


class TestToLegacyLayers:
    def test_returns_legacy_roots_from_meta(self):
        """When doc.root.meta has legacy_roots, return it as-is."""
        legacy_data = [{"id": "l1", "name": "bg", "type": "image"}]
        root = GroupNode(
            id="root", name="root", style=_style(),
            meta={"legacy_roots": legacy_data},
        )
        doc = _doc(root)
        result = to_legacy_layers(doc)
        assert result is legacy_data

    def test_returns_legacy_roots_preserves_list_identity(self):
        """Returned list is the exact same object (zero-copy)."""
        data = [{"x": 1}]
        root = GroupNode(
            id="root", name="root", style=_style(),
            meta={"legacy_roots": data},
        )
        assert to_legacy_layers(_doc(root)) is data

    def test_empty_meta_synthesizes_from_children(self):
        """No legacy_roots → synthesize from IR children."""
        child = ImageNode(
            id="img1", name="bg",
            style=_style(0, 0, 375, 200),
            asset=AssetRef(src="images/bg.png"),
        )
        root = GroupNode(
            id="root", name="root", style=_style(0, 0, 375, 812),
            children=[child],
        )
        doc = _doc(root)
        result = to_legacy_layers(doc)
        assert len(result) == 1
        assert result[0]["id"] == "img1"
        assert result[0]["name"] == "bg"

    def test_meta_empty_dict_synthesizes(self):
        """meta={} (no legacy_roots key) → synthesize from children."""
        root = GroupNode(id="root", name="root", style=_style())
        doc = _doc(root)
        result = to_legacy_layers(doc)
        assert result == []

    def test_meta_dict_without_legacy_roots_synthesizes(self):
        """meta is dict but doesn't have 'legacy_roots' → synthesize."""
        root = GroupNode(
            id="root", name="root", style=_style(),
            meta={"other_key": 42},
            children=[
                ShapeNode(id="s1", name="divider", style=_style(10, 10, 100, 12)),
            ],
        )
        doc = _doc(root)
        result = to_legacy_layers(doc)
        assert len(result) == 1
        assert result[0]["type"] == "shape"


# ===========================================================================
# _legacy_from_node tests
# ===========================================================================


class TestLegacyFromNode:
    def test_uses_meta_legacy_shortcut(self):
        """If node.meta['legacy'] is a dict, return it directly."""
        legacy_dict = {"id": "override", "name": "custom", "type": "image"}
        node = ImageNode(
            id="img1", name="bg",
            style=_style(0, 0, 100, 50),
            asset=AssetRef(src="images/x.png"),
            meta={"legacy": legacy_dict},
        )
        result = _legacy_from_node(node)
        assert result is legacy_dict

    def test_image_node_fields(self):
        """ImageNode without meta legacy → synthesized dict with correct fields."""
        node = ImageNode(
            id="img1", name="hero",
            style=_style(10, 20, 360, 220),
            asset=AssetRef(src="images/hero.png"),
        )
        result = _legacy_from_node(node)
        assert result["id"] == "img1"
        assert result["name"] == "hero"
        assert result["left"] == 10
        assert result["top"] == 20
        assert result["width"] == 350
        assert result["height"] == 200
        assert result["opacity"] == 1.0
        assert result["type"] == "image"

    def test_text_node_type(self):
        node = TextNode(
            id="t1", name="hello",
            style=_style(5, 5, 100, 30),
            text="Hello",
        )
        result = _legacy_from_node(node)
        assert result["type"] == "text"
        assert result["name"] == "hello"

    def test_shape_node_type(self):
        node = ShapeNode(id="s1", name="rect", style=_style(0, 0, 50, 50))
        result = _legacy_from_node(node)
        assert result["type"] == "shape"

    def test_group_node_has_children(self):
        """GroupNode → type='group' and children list."""
        child = TextNode(
            id="t1", name="label",
            style=_style(0, 0, 80, 20),
            text="Label",
        )
        group = GroupNode(
            id="g1", name="card",
            style=_style(0, 0, 200, 300),
            children=[child],
        )
        result = _legacy_from_node(group)
        assert result["type"] == "group"
        assert len(result["children"]) == 1
        assert result["children"][0]["id"] == "t1"

    def test_nested_groups(self):
        """Nested groups produce nested children."""
        inner = GroupNode(
            id="g2", name="inner",
            style=_style(10, 10, 90, 90),
            children=[
                ShapeNode(id="s1", name="bg", style=_style(10, 10, 90, 90)),
            ],
        )
        outer = GroupNode(
            id="g1", name="outer",
            style=_style(0, 0, 100, 100),
            children=[inner],
        )
        result = _legacy_from_node(outer)
        assert result["children"][0]["type"] == "group"
        assert result["children"][0]["children"][0]["type"] == "shape"

    def test_opacity_preserved(self):
        """Node opacity is included in legacy dict."""
        node = ImageNode(
            id="i1", name="ghost",
            style=_style(0, 0, 50, 50, opacity=0.5),
            asset=AssetRef(src="images/ghost.png"),
        )
        result = _legacy_from_node(node)
        assert result["opacity"] == 0.5

    def test_empty_group(self):
        """GroupNode with no children → empty children list."""
        group = GroupNode(
            id="g1", name="empty",
            style=_style(0, 0, 100, 100),
        )
        result = _legacy_from_node(group)
        assert result["children"] == []
