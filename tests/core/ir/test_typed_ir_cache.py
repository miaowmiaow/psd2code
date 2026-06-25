"""TypedIRCache 单元测试（第4周 Day 18）。"""

from __future__ import annotations

import pytest

from core.ir import (
    Document,
    GroupNode,
    ImageNode,
    TextNode,
    Node,
    Style,
    BBox,
    Color,
    FontStyle,
    AssetRef,
)
from core.ir.typed_ir_cache import TypedIRCache


@pytest.fixture
def sample_document() -> Document:
    """构造样本 IR 文档用于测试。"""
    # root (group)
    #   ├─ img1 (image)
    #   └─ group2 (group)
    #       ├─ text1 (text, with font)
    #       └─ img2 (image)

    bbox_root = BBox(left=0, top=0, right=100, bottom=100)
    bbox_img1 = BBox(left=10, top=10, right=30, bottom=30)
    bbox_group2 = BBox(left=40, top=40, right=80, bottom=80)
    bbox_text1 = BBox(left=50, top=50, right=70, bottom=60)
    bbox_img2 = BBox(left=55, top=65, right=75, bottom=75)

    font_style = FontStyle(family="Arial", size_px=16.0, weight=500, color=Color(r=0, g=0, b=0))

    img1 = ImageNode(
        id="img-1",
        name="image1",
        style=Style(bbox=bbox_img1, opacity=1.0, z_index=1),
        asset=AssetRef(kind="image", src="img1.png"),
    )

    text1 = TextNode(
        id="text-1",
        name="text1",
        style=Style(bbox=bbox_text1, opacity=0.8, z_index=2, font=font_style),
        text="Hello World",
    )

    img2 = ImageNode(
        id="img-2",
        name="image2",
        style=Style(bbox=bbox_img2, opacity=0.5, z_index=3),
        asset=AssetRef(kind="image", src="img2.png"),
    )

    group2 = GroupNode(
        id="group-2",
        name="group2",
        style=Style(bbox=bbox_group2, opacity=1.0, z_index=0),
        children=[text1, img2],
    )

    root = GroupNode(
        id="root",
        name="root",
        style=Style(bbox=bbox_root, opacity=1.0, z_index=0),
        children=[img1, group2],
    )

    return Document(width=100, height=100, root=root, title="Test")


class TestTypedIRCacheConstruction:
    """测试缓存构造。"""

    def test_cache_builds_successfully(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        assert cache is not None
        assert len(cache._node_map) == 5  # root + img1 + group2 + text1 + img2

    def test_all_nodes_cached(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        node_ids = {node.id for node in cache.iter_all_nodes()}
        assert node_ids == {"root", "img-1", "group-2", "text-1", "img-2"}


class TestTypedIRCacheLookup:
    """测试 lookup 功能。"""

    def test_get_node(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        node = cache.get_node("img-1")
        assert node is not None
        assert node.name == "image1"

    def test_get_node_not_found(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        node = cache.get_node("non-existent")
        assert node is None

    def test_get_style_dict(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        style = cache.get_style_dict("img-1")
        assert style is not None
        assert style["opacity"] == 1.0
        assert style["z_index"] == 1
        assert style["bbox"]["width"] == 20
        assert style["bbox"]["height"] == 20

    def test_get_effects_empty(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        effects = cache.get_effects("img-1")
        assert effects == []

    def test_get_font_info(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        style = cache.get_style_dict("text-1")
        assert style is not None
        font = style["font"]
        assert font is not None
        assert font["family"] == "Arial"
        assert font["size_px"] == 16.0
        assert font["weight"] == 500


class TestTypedIRCacheDepth:
    """测试深度计算。"""

    def test_node_depth(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        assert cache.get_node_depth("root") == 0
        assert cache.get_node_depth("img-1") == 1
        assert cache.get_node_depth("group-2") == 1
        assert cache.get_node_depth("text-1") == 2
        assert cache.get_node_depth("img-2") == 2

    def test_is_root(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        assert cache.is_root("root")
        assert not cache.is_root("img-1")
        assert not cache.is_root("group-2")


class TestTypedIRCacheIterators:
    """测试迭代器。"""

    def test_iter_all_nodes(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        nodes = list(cache.iter_all_nodes())
        assert len(nodes) == 5

    def test_iter_leaf_nodes(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        leaf_ids = {node.id for node in cache.iter_leaf_nodes()}
        assert leaf_ids == {"img-1", "text-1", "img-2"}

    def test_iter_group_nodes(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        group_ids = {node.id for node in cache.iter_group_nodes()}
        assert group_ids == {"root", "group-2"}

    def test_iter_nodes_at_depth(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        depth_1_ids = {node.id for node in cache.iter_nodes_at_depth(1)}
        assert depth_1_ids == {"img-1", "group-2"}

    def test_iter_visible_nodes(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        visible_ids = {node.id for node in cache.iter_visible_nodes()}
        # 所有节点都可见（opacity > 0, visible=True）
        assert len(visible_ids) == 5


class TestTypedIRCacheStats:
    """测试统计信息。"""

    def test_stats_keys(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        stats = cache.stats()
        assert "nodes_cached" in stats
        assert "styles_cached" in stats
        assert "effects_cached" in stats
        assert "total_effects" in stats
        assert "max_tree_depth" in stats
        assert "avg_children_per_group" in stats

    def test_stats_values(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        stats = cache.stats()
        assert stats["nodes_cached"] == 5
        assert stats["styles_cached"] == 5
        assert stats["max_tree_depth"] == 2
        assert stats["avg_children_per_group"] == 2.0  # root: 2 children, group2: 2 children, avg = 4/2 = 2.0

    def test_dump_summary(self, sample_document: Document) -> None:
        cache = TypedIRCache(sample_document)
        summary = cache.dump_summary()
        assert "nodes=" in summary
        assert "effects=" in summary
        assert "depth=" in summary
