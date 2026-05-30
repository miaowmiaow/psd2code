"""DOMRestructure 核心算法单元测试。

DOMRestructure 是 110KB/2465 行的核心大文件。本文件针对其纯逻辑方法做
独立单测，不依赖真实 DOM / HTML 渲染：

  - BBox 数据结构（width/height/area/overlap_ratio）
  - ClusterConfig 默认值
  - _split_by_rows（V3 主导重叠率行切分）
  - _split_by_cols（带微重叠容忍的列切分）
  - _is_stack_group（叠图判定）
  - _envelope（多 bbox 包围盒）
  - _next_virtual_id（自增 id）
  - _summarize_tree（布局树文本摘要）
"""
import pytest
from unittest.mock import MagicMock

from targets.html.postprocess.layout_optimizer.transformers.dom_restructure import (
    BBox,
    ClusterConfig,
    DOMRestructure,
    LeafInfo,
    LayoutNode,
)


# ===========================================================================
# 辅助
# ===========================================================================

def _make_dom_restructure(config: ClusterConfig = None) -> DOMRestructure:
    """创建一个轻量级 DOMRestructure 实例（mock soup）"""
    dr = DOMRestructure(
        soup=MagicMock(),
        css_rules={},
        stats={},
        images_dir=None,
    )
    if config:
        dr.config = config
    return dr


def _make_leaf(name: str, left: float, top: float, width: float, height: float,
               data_type: str = "image") -> LeafInfo:
    return LeafInfo(
        element=MagicMock(),
        css_class=f".{name}",
        name=name,
        data_type=data_type,
        bbox=BBox(left=left, top=top, right=left + width, bottom=top + height),
    )


# ===========================================================================
# BBox 测试
# ===========================================================================

class TestBBox:
    def test_width_height(self):
        b = BBox(10, 20, 110, 70)
        assert b.width == 100
        assert b.height == 50

    def test_area(self):
        b = BBox(0, 0, 100, 50)
        assert b.area == 5000

    def test_area_zero_size(self):
        b = BBox(10, 10, 10, 10)
        assert b.area == 0.0

    def test_area_negative_dimensions(self):
        """right < left 的情况下 area 为 0"""
        b = BBox(100, 100, 50, 50)
        assert b.area == 0.0

    def test_overlap_ratio_full_overlap(self):
        """完全重叠 → 1.0"""
        a = BBox(0, 0, 100, 100)
        b = BBox(0, 0, 100, 100)
        assert a.overlap_ratio(b) == 1.0

    def test_overlap_ratio_no_overlap(self):
        """无重叠 → 0.0"""
        a = BBox(0, 0, 50, 50)
        b = BBox(100, 100, 200, 200)
        assert a.overlap_ratio(b) == 0.0

    def test_overlap_ratio_partial(self):
        """部分重叠"""
        a = BBox(0, 0, 100, 100)  # area=10000
        b = BBox(50, 50, 150, 150)  # area=10000
        # overlap: x=[50,100]=50, y=[50,100]=50, inter=2500
        # min_area=10000, ratio=2500/10000=0.25
        assert abs(a.overlap_ratio(b) - 0.25) < 1e-9

    def test_overlap_ratio_contained(self):
        """小 bbox 完全被包含 → 1.0"""
        big = BBox(0, 0, 200, 200)
        small = BBox(50, 50, 100, 100)  # area=2500
        # inter=2500, min(40000, 2500)=2500, ratio=1.0
        assert small.overlap_ratio(big) == 1.0
        assert big.overlap_ratio(small) == 1.0

    def test_overlap_ratio_zero_area(self):
        """零面积 bbox → 0.0"""
        a = BBox(10, 10, 10, 10)  # zero area
        b = BBox(0, 0, 100, 100)
        assert a.overlap_ratio(b) == 0.0

    def test_overlap_ratio_touching_edge(self):
        """仅边接触 → 0.0"""
        a = BBox(0, 0, 50, 50)
        b = BBox(50, 0, 100, 50)
        assert a.overlap_ratio(b) == 0.0


# ===========================================================================
# ClusterConfig 测试
# ===========================================================================

class TestClusterConfig:
    def test_defaults(self):
        cfg = ClusterConfig()
        assert cfg.row_gap_px == 8.0
        assert cfg.col_gap_px == 8.0
        assert cfg.stack_pair_threshold == 0.6
        assert cfg.stack_majority == 0.5
        assert cfg.min_children_to_cluster == 2
        assert cfg.enable_container_bg_absorb_pass is True
        assert cfg.enable_stack_to_col_reclassify is True
        assert cfg.enable_tall_decor_extraction is True

    def test_custom_config(self):
        cfg = ClusterConfig(row_gap_px=20, stack_pair_threshold=0.8)
        assert cfg.row_gap_px == 20
        assert cfg.stack_pair_threshold == 0.8


# ===========================================================================
# _split_by_rows 行切分
# ===========================================================================

class TestSplitByRows:
    def test_single_row(self):
        """所有元素在同一水平线 → 单行"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("a", 0, 0, 100, 50),
            _make_leaf("b", 120, 5, 80, 45),
            _make_leaf("c", 220, 3, 60, 48),
        ]
        rows = dr._split_by_rows(leaves)
        assert len(rows) == 1
        assert len(rows[0]) == 3

    def test_two_rows_separated(self):
        """两行明显分离"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("a", 0, 0, 100, 30),
            _make_leaf("b", 120, 5, 80, 25),
            # 第二行距第一行很远
            _make_leaf("c", 0, 200, 100, 30),
            _make_leaf("d", 120, 205, 80, 25),
        ]
        rows = dr._split_by_rows(leaves)
        assert len(rows) == 2
        names = [[l.name for l in row] for row in rows]
        assert "a" in names[0] and "b" in names[0]
        assert "c" in names[1] and "d" in names[1]

    def test_dominant_overlap_threshold(self):
        """碰边但重叠率不足 → 切行"""
        dr = _make_dom_restructure(ClusterConfig(row_dominant_overlap_ratio=0.5))
        # a: height=100, top=0, bottom=100
        # b: height=100, top=95, bottom=195
        # overlap=5, min_h=100, ratio=0.05 < 0.5 → 不同行
        leaves = [
            _make_leaf("a", 0, 0, 80, 100),
            _make_leaf("b", 0, 95, 80, 100),
        ]
        rows = dr._split_by_rows(leaves)
        assert len(rows) == 2

    def test_dominant_overlap_same_row(self):
        """充分重叠 → 同行"""
        dr = _make_dom_restructure(ClusterConfig(row_dominant_overlap_ratio=0.5))
        # a: top=0, bottom=100, height=100
        # b: top=30, bottom=80, height=50
        # overlap=50, min_h=50, ratio=1.0 ≥ 0.5 → 同行
        leaves = [
            _make_leaf("a", 0, 0, 80, 100),
            _make_leaf("b", 100, 30, 60, 50),
        ]
        rows = dr._split_by_rows(leaves)
        assert len(rows) == 1

    def test_empty_input(self):
        dr = _make_dom_restructure()
        assert dr._split_by_rows([]) == []

    def test_single_element(self):
        dr = _make_dom_restructure()
        leaves = [_make_leaf("a", 0, 0, 100, 50)]
        rows = dr._split_by_rows(leaves)
        assert len(rows) == 1
        assert len(rows[0]) == 1

    def test_many_rows(self):
        """5 行各 1 个元素"""
        dr = _make_dom_restructure()
        leaves = [_make_leaf(f"r{i}", 0, i * 100, 50, 30) for i in range(5)]
        rows = dr._split_by_rows(leaves)
        assert len(rows) == 5

    def test_unsorted_input(self):
        """输入无序但结果正确按 top 排序分行"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("c", 0, 200, 50, 30),
            _make_leaf("a", 0, 0, 50, 30),
            _make_leaf("b", 0, 100, 50, 30),
        ]
        rows = dr._split_by_rows(leaves)
        assert len(rows) == 3
        # 第一行应包含 top 最小的
        assert rows[0][0].name == "a"
        assert rows[1][0].name == "b"
        assert rows[2][0].name == "c"


# ===========================================================================
# _split_by_cols 列切分
# ===========================================================================

class TestSplitByCols:
    def test_single_column(self):
        """所有元素在同一列"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("a", 0, 0, 100, 50),
            _make_leaf("b", 10, 60, 80, 50),
            _make_leaf("c", 5, 120, 90, 50),
        ]
        cols = dr._split_by_cols(leaves)
        # 都在 x=[0,100] 范围内高度重叠
        assert len(cols) == 1

    def test_two_columns_separated(self):
        """两列明显分离"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("a", 0, 0, 100, 50),
            _make_leaf("b", 200, 0, 80, 50),
        ]
        cols = dr._split_by_cols(leaves)
        assert len(cols) == 2

    def test_slight_overlap_same_column(self):
        """微重叠超过 tolerance → 归入同列"""
        dr = _make_dom_restructure(ClusterConfig(overlap_split_ratio=0.2, col_gap_px=8))
        # a: left=0, right=100
        # b: left=70, right=170
        # overlap_x = 100 - 70 = 30
        # min_width = min(100, 100) = 100
        # tolerance = max(100*0.2, 8*0.5) = max(20, 4) = 20
        # 30 > 20 → 同列
        leaves = [
            _make_leaf("a", 0, 0, 100, 50),
            _make_leaf("b", 70, 0, 100, 50),
        ]
        cols = dr._split_by_cols(leaves)
        assert len(cols) == 1

    def test_empty_input(self):
        dr = _make_dom_restructure()
        assert dr._split_by_cols([]) == []

    def test_many_columns(self):
        """4 列各 1 个元素"""
        dr = _make_dom_restructure()
        leaves = [_make_leaf(f"c{i}", i * 200, 0, 50, 50) for i in range(4)]
        cols = dr._split_by_cols(leaves)
        assert len(cols) == 4


# ===========================================================================
# _is_stack_group 叠图判定
# ===========================================================================

class TestIsStackGroup:
    def test_all_overlapping(self):
        """所有 bbox 互相大量重叠 → 叠图"""
        dr = _make_dom_restructure()
        bboxes = [
            BBox(0, 0, 100, 100),
            BBox(5, 5, 105, 105),
            BBox(10, 10, 110, 110),
        ]
        assert dr._is_stack_group(bboxes) is True

    def test_no_overlap(self):
        """无重叠 → 非叠图"""
        dr = _make_dom_restructure()
        bboxes = [
            BBox(0, 0, 50, 50),
            BBox(200, 200, 300, 300),
            BBox(400, 0, 500, 50),
        ]
        assert dr._is_stack_group(bboxes) is False

    def test_partial_overlap_below_threshold(self):
        """部分重叠但低于阈值 → 非叠图"""
        dr = _make_dom_restructure(ClusterConfig(
            stack_pair_threshold=0.6,
            stack_majority=0.5,
        ))
        bboxes = [
            BBox(0, 0, 100, 100),     # area=10000
            BBox(80, 80, 180, 180),   # overlap with first: 20x20=400/10000=0.04 < 0.6
            BBox(160, 160, 260, 260), # no overlap with first
        ]
        assert dr._is_stack_group(bboxes) is False

    def test_single_element(self):
        """< 2 个有效 bbox → False"""
        dr = _make_dom_restructure()
        assert dr._is_stack_group([BBox(0, 0, 100, 100)]) is False

    def test_zero_area_bbox_excluded(self):
        """零面积 bbox 被排除，不稀释叠图判定"""
        dr = _make_dom_restructure(ClusterConfig(
            stack_pair_threshold=0.6,
            stack_majority=0.5,
        ))
        # 两个有效 bbox 完全重叠
        bboxes = [
            BBox(0, 0, 100, 100),
            BBox(0, 0, 100, 100),
            BBox(50, 50, 50, 50),  # 零面积，应被排除
        ]
        assert dr._is_stack_group(bboxes) is True

    def test_majority_threshold(self):
        """叠图对刚好占一半 → 通过"""
        dr = _make_dom_restructure(ClusterConfig(
            stack_pair_threshold=0.6,
            stack_majority=0.5,
        ))
        # 3 个 bbox: total_pairs=3, 需要 >= 1.5 → 至少 2 对
        bboxes = [
            BBox(0, 0, 100, 100),
            BBox(10, 10, 110, 110),  # 高度重叠 #0
            BBox(500, 500, 600, 600),  # 不与前两者重叠
        ]
        # pair(0,1) overlap 90x90=8100/10000=0.81 ≥ 0.6 ✓
        # pair(0,2) 0.0 ✗
        # pair(1,2) 0.0 ✗
        # stack_pairs=1, 1/3=0.33 < 0.5 → False
        assert dr._is_stack_group(bboxes) is False


# ===========================================================================
# _envelope
# ===========================================================================

class TestEnvelope:
    def test_basic_envelope(self):
        bboxes = [
            BBox(10, 20, 100, 80),
            BBox(50, 5, 200, 60),
            BBox(0, 30, 150, 90),
        ]
        env = DOMRestructure._envelope(bboxes)
        assert env.left == 0
        assert env.top == 5
        assert env.right == 200
        assert env.bottom == 90

    def test_single_bbox(self):
        b = BBox(10, 20, 30, 40)
        env = DOMRestructure._envelope([b])
        assert env.left == 10
        assert env.top == 20
        assert env.right == 30
        assert env.bottom == 40


# ===========================================================================
# _next_virtual_id
# ===========================================================================

class TestNextVirtualId:
    def test_incremental(self):
        dr = _make_dom_restructure()
        assert dr._next_virtual_id("row") == "v-row-1"
        assert dr._next_virtual_id("col") == "v-col-2"
        assert dr._next_virtual_id("stack") == "v-stack-3"


# ===========================================================================
# _summarize_tree
# ===========================================================================

class TestSummarizeTree:
    def test_leaf(self):
        dr = _make_dom_restructure()
        node = LayoutNode(kind='leaf', bbox=BBox(0, 0, 10, 10))
        assert dr._summarize_tree(node) == "leaf"

    def test_row_with_leaves(self):
        dr = _make_dom_restructure()
        child1 = LayoutNode(kind='leaf', bbox=BBox(0, 0, 10, 10))
        child2 = LayoutNode(kind='leaf', bbox=BBox(20, 0, 30, 10))
        row = LayoutNode(kind='row', bbox=BBox(0, 0, 30, 10), children=[child1, child2])
        assert dr._summarize_tree(row) == "R[leaf,leaf]"

    def test_nested(self):
        dr = _make_dom_restructure()
        leaf1 = LayoutNode(kind='leaf', bbox=BBox(0, 0, 10, 10))
        leaf2 = LayoutNode(kind='leaf', bbox=BBox(0, 20, 10, 30))
        col = LayoutNode(kind='col', bbox=BBox(0, 0, 10, 30), children=[leaf1, leaf2])
        leaf3 = LayoutNode(kind='leaf', bbox=BBox(20, 0, 30, 30))
        row = LayoutNode(kind='row', bbox=BBox(0, 0, 30, 30), children=[col, leaf3])
        assert dr._summarize_tree(row) == "R[C[leaf,leaf],leaf]"

    def test_stack(self):
        dr = _make_dom_restructure()
        leaf1 = LayoutNode(kind='leaf', bbox=BBox(0, 0, 100, 100))
        leaf2 = LayoutNode(kind='leaf', bbox=BBox(0, 0, 100, 100))
        stack = LayoutNode(kind='stack', bbox=BBox(0, 0, 100, 100), children=[leaf1, leaf2])
        assert dr._summarize_tree(stack) == "S[leaf,leaf]"


# ===========================================================================
# LayoutNode / LeafInfo 数据结构
# ===========================================================================

class TestDataStructures:
    def test_layout_node_defaults(self):
        node = LayoutNode(kind='leaf', bbox=BBox(0, 0, 10, 10))
        assert node.leaf is None
        assert node.children == []

    def test_leaf_info(self):
        leaf = _make_leaf("test", 10, 20, 100, 50, "text")
        assert leaf.name == "test"
        assert leaf.css_class == ".test"
        assert leaf.data_type == "text"
        assert leaf.bbox.width == 100
        assert leaf.bbox.height == 50
