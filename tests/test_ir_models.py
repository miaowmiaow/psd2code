"""Tests for core.ir — Pydantic IR model validation & traversal.

All tests are pure in-memory, no IO or PSD files required.
"""

from __future__ import annotations

import pytest

from core.ir.styles import BBox, Color, FontStyle, Style
from core.ir.nodes import GroupNode, ImageNode, TextNode, ShapeNode, NodeKind
from core.ir.assets import AssetRef
from core.ir.effects import StrokeSpec, DropShadowSpec, ColorOverlaySpec
from core.ir.document import Document


# ===================================================================
# BBox
# ===================================================================

class TestBBox:
    def test_basic(self):
        b = BBox(left=10, top=20, right=110, bottom=120)
        assert b.width == 100
        assert b.height == 100

    def test_zero_size(self):
        b = BBox(left=0, top=0, right=0, bottom=0)
        assert b.width == 0
        assert b.height == 0

    def test_right_less_than_left_raises(self):
        with pytest.raises(ValueError, match="right.*must be >= left"):
            BBox(left=100, top=0, right=50, bottom=100)

    def test_bottom_less_than_top_raises(self):
        with pytest.raises(ValueError, match="bottom.*must be >= top"):
            BBox(left=0, top=100, right=100, bottom=50)


# ===================================================================
# Color
# ===================================================================

class TestColor:
    def test_to_css_opaque(self):
        c = Color(r=255, g=0, b=0)
        assert c.to_css() == "rgb(255, 0, 0)"

    def test_to_css_semi_transparent(self):
        c = Color(r=0, g=128, b=255, a=0.5)
        assert c.to_css() == "rgba(0, 128, 255, 0.5)"

    def test_to_css_nearly_opaque(self):
        """a >= 0.999 should be treated as fully opaque."""
        c = Color(r=0, g=0, b=0, a=0.999)
        assert c.to_css() == "rgb(0, 0, 0)"

    def test_invalid_channel_raises(self):
        with pytest.raises(ValueError):
            Color(r=256, g=0, b=0)

    def test_invalid_alpha_raises(self):
        with pytest.raises(ValueError):
            Color(r=0, g=0, b=0, a=1.5)


# ===================================================================
# Style
# ===================================================================

class TestStyle:
    def test_defaults(self):
        s = Style(bbox=BBox(left=0, top=0, right=100, bottom=100))
        assert s.opacity == 1.0
        assert s.visible is True
        assert s.font is None
        assert s.background_color is None
        assert s.extra == {}

    def test_with_font(self):
        font = FontStyle(family="Arial", size_px=16.0, weight=700)
        s = Style(bbox=BBox(left=0, top=0, right=100, bottom=50), font=font)
        assert s.font is not None
        assert s.font.family == "Arial"

    def test_opacity_range(self):
        with pytest.raises(ValueError):
            Style(bbox=BBox(left=0, top=0, right=1, bottom=1), opacity=1.5)


# ===================================================================
# AssetRef
# ===================================================================

class TestAssetRef:
    def test_basic(self):
        a = AssetRef(src="images/bg.png", width=375, height=200, format="png")
        assert a.kind == "image"
        assert a.src == "images/bg.png"

    def test_default_kind(self):
        a = AssetRef(src="x.png")
        assert a.kind == "image"


# ===================================================================
# Nodes
# ===================================================================

class TestNodes:
    def test_group_node(self):
        g = GroupNode(id="g1", name="group", style=Style(bbox=BBox(left=0, top=0, right=100, bottom=100)))
        assert g.kind == NodeKind.GROUP
        assert g.children == []
        assert g.merged_asset is None

    def test_image_node(self):
        img = ImageNode(
            id="i1", name="photo",
            style=Style(bbox=BBox(left=0, top=0, right=50, bottom=50)),
            asset=AssetRef(src="images/photo.png"),
        )
        assert img.kind == NodeKind.IMAGE

    def test_text_node(self):
        txt = TextNode(
            id="t1", name="label",
            style=Style(bbox=BBox(left=0, top=0, right=200, bottom=30)),
            text="Hello",
        )
        assert txt.kind == NodeKind.TEXT
        assert txt.text == "Hello"

    def test_shape_node(self):
        shp = ShapeNode(
            id="s1", name="rect",
            style=Style(bbox=BBox(left=0, top=0, right=100, bottom=2)),
        )
        assert shp.kind == NodeKind.SHAPE
        assert shp.asset is None


# ===================================================================
# Effects
# ===================================================================

class TestEffects:
    def test_stroke(self):
        s = StrokeSpec(size_px=2.0, color=Color(r=0, g=0, b=0))
        assert s.kind == "stroke"
        assert s.enabled is True

    def test_drop_shadow(self):
        ds = DropShadowSpec(color=Color(r=0, g=0, b=0, a=0.5), blur_px=10)
        assert ds.kind == "drop_shadow"

    def test_color_overlay(self):
        co = ColorOverlaySpec(color=Color(r=255, g=0, b=0))
        assert co.kind == "color_overlay"


# ===================================================================
# Document
# ===================================================================

class TestDocument:
    def test_minimal(self, minimal_document: Document):
        assert minimal_document.width == 375
        assert minimal_document.height == 812
        assert minimal_document.root.kind == NodeKind.GROUP

    def test_iter_nodes_minimal(self, minimal_document: Document):
        nodes = list(minimal_document.iter_nodes())
        assert len(nodes) == 1  # only root
        assert nodes[0].id == "root"

    def test_iter_nodes_sample(self, sample_document: Document):
        nodes = list(sample_document.iter_nodes())
        ids = [n.id for n in nodes]
        # iter_nodes 使用 stack = list(root.children) + pop()（LIFO），
        # 初始 root.children 入栈未 reversed，所以最后一个 child 先出栈；
        # 但 GroupNode 内部 children 入栈时做了 reversed，保证子树内左优先。
        # 实际顺序：root → g2 → img2 → shp1 → txt1 → img1
        assert ids == ["root", "g2", "img2", "shp1", "txt1", "img1"]

    def test_iter_nodes_visits_all(self, sample_document: Document):
        """iter_nodes should visit every node exactly once."""
        nodes = list(sample_document.iter_nodes())
        ids = {n.id for n in nodes}
        assert ids == {"root", "img1", "txt1", "shp1", "g2", "img2"}

    def test_iter_nodes_root_first(self, sample_document: Document):
        """Root should always be the first yielded node."""
        nodes = list(sample_document.iter_nodes())
        assert nodes[0].id == "root"

    def test_iter_nodes_parent_before_child(self, sample_document: Document):
        """A parent group should be visited before its children."""
        nodes = list(sample_document.iter_nodes())
        g2_idx = next(i for i, n in enumerate(nodes) if n.id == "g2")
        img2_idx = next(i for i, n in enumerate(nodes) if n.id == "img2")
        assert g2_idx < img2_idx

    def test_invalid_width(self):
        with pytest.raises(ValueError):
            Document(
                width=0, height=100,
                root=GroupNode(
                    id="r", style=Style(bbox=BBox(left=0, top=0, right=1, bottom=1)),
                ),
            )

    def test_invalid_height(self):
        with pytest.raises(ValueError):
            Document(
                width=100, height=-1,
                root=GroupNode(
                    id="r", style=Style(bbox=BBox(left=0, top=0, right=1, bottom=1)),
                ),
            )
