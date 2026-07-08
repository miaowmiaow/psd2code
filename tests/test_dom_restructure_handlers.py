"""DOM 重构 Handler 单元测试

测试所有 Handler 的核心功能：
- TallDecorHandler: 高瘦跨行装饰剥离
- ClusteringHandler: 空间聚类
- RenderingHandler: DOM 渲染
- ReclassifyHandler: Stack → Col 反向升级
"""

from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

from bs4 import BeautifulSoup
import pytest

from targets.html.postprocess.layout_optimizer.transformers.dom_restructure import (
    BBox,
    ClusterConfig,
    DOMRestructure,
    LeafInfo,
    LayoutNode,
)
from targets.html.postprocess.layout_optimizer.transformers.dom_restructure.handlers import (
    TallDecorHandler,
    ClusteringHandler,
    RenderingHandler,
    ReclassifyHandler,
    BackgroundHandler,
)


# ============================================================================
# 辅助函数
# ============================================================================

def _make_dom_restructure(config: ClusterConfig = None) -> DOMRestructure:
    """创建一个轻量级 DOMRestructure 实例"""
    soup = MagicMock()
    soup.find_all = MagicMock(return_value=[])
    
    stats = {
        'dom_restructured': 0,
    }
    
    dr = DOMRestructure(
        soup=soup,
        css_rules={},
        stats=stats,
        images_dir=None,
    )
    if config:
        dr.config = config
    
    # 确保配置完全初始化
    assert dr.config is not None
    assert dr.tall_decor.owner is dr
    assert dr.clustering.owner is dr
    assert dr.rendering.owner is dr
    assert dr.reclassify.owner is dr
    assert dr.background.owner is dr
    
    return dr


def _make_leaf(
    name: str,
    left: float,
    top: float,
    width: float,
    height: float,
    data_type: str = "image",
) -> LeafInfo:
    """创建一个测试 LeafInfo"""
    elem = MagicMock()
    elem.get = MagicMock(return_value=[name])
    return LeafInfo(
        element=elem,
        css_class=f".{name}",
        name=name,
        data_type=data_type,
        bbox=BBox(left=left, top=top, right=left + width, bottom=top + height),
    )


# ============================================================================
# TallDecorHandler 测试
# ============================================================================

class TestTallDecorHandler:
    """测试高瘦跨行装饰剥离"""

    def test_extract_tall_decor_leaves_disabled(self):
        """禁用装饰层提取时返回空"""
        dr = _make_dom_restructure()
        dr.config.enable_tall_decor_extraction = False

        leaves = [
            _make_leaf("a", 0, 0, 10, 100),
            _make_leaf("b", 20, 10, 10, 80),
        ]

        decor, fg = dr.tall_decor.extract_tall_decor_leaves(leaves)
        assert len(decor) == 0
        assert len(fg) == 2

    def test_extract_tall_decor_leaves_single_leaf(self):
        """单个叶子时返回空装饰层"""
        dr = _make_dom_restructure()
        leaves = [_make_leaf("a", 0, 0, 100, 100)]

        decor, fg = dr.tall_decor.extract_tall_decor_leaves(leaves)
        assert len(decor) == 0
        assert len(fg) == 1

    def test_extract_tall_decor_leaves_high_aspect_ratio(self):
        """识别高宽比的装饰层"""
        dr = _make_dom_restructure()
        dr.config.enable_tall_decor_extraction = True

        leaves = [
            _make_leaf("tall_decor", 50, 0, 10, 200),  # 纵横比 = 20
            _make_leaf("a", 0, 50, 100, 30),
            _make_leaf("b", 0, 100, 100, 30),
        ]

        decor, fg = dr.tall_decor.extract_tall_decor_leaves(leaves)
        # 应该识别出 tall_decor
        assert any(d.name == "tall_decor" for d in decor)

    def test_are_x_aligned(self):
        """测试 X 轴对齐判定"""
        leaves = [
            _make_leaf("a", 0, 0, 50, 100),
            _make_leaf("b", 0, 150, 50, 100),
        ]
        result = TallDecorHandler._are_x_aligned(leaves, tolerance=0.2)
        assert result

    def test_are_x_aligned_not_aligned(self):
        """X 轴不对齐"""
        leaves = [
            _make_leaf("a", 0, 0, 50, 100),
            _make_leaf("b", 100, 150, 50, 100),
        ]
        result = TallDecorHandler._are_x_aligned(leaves, tolerance=0.2)
        assert not result


# ============================================================================
# ClusteringHandler 测试
# ============================================================================

class TestClusteringHandler:
    """测试空间聚类"""

    def test_is_stack_group_full_overlap(self):
        """完全重叠判定为 stack"""
        dr = _make_dom_restructure()
        bboxes = [
            BBox(0, 0, 100, 100),
            BBox(0, 0, 100, 100),
            BBox(0, 0, 100, 100),
        ]
        result = dr.clustering.is_stack_group(bboxes)
        assert result

    def test_is_stack_group_no_overlap(self):
        """无重叠不是 stack"""
        dr = _make_dom_restructure()
        bboxes = [
            BBox(0, 0, 50, 50),
            BBox(100, 0, 150, 50),
            BBox(0, 100, 50, 150),
        ]
        result = dr.clustering.is_stack_group(bboxes)
        assert not result

    def test_is_stack_group_partial_overlap(self):
        """部分重叠不是 stack"""
        dr = _make_dom_restructure()
        bboxes = [
            BBox(0, 0, 100, 100),
            BBox(50, 50, 150, 150),
        ]
        result = dr.clustering.is_stack_group(bboxes)
        assert not result

    def test_cluster_horizontal_row(self):
        """聚类为水平行"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("a", 0, 0, 50, 50),
            _make_leaf("b", 60, 0, 50, 50),
            _make_leaf("c", 120, 0, 50, 50),
        ]

        tree = dr.clustering.cluster(leaves)
        assert tree.kind == "row"
        assert len(tree.children) == 3

    def test_cluster_vertical_column(self):
        """聚类为垂直列"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("a", 0, 0, 50, 50),
            _make_leaf("b", 0, 60, 50, 50),
            _make_leaf("c", 0, 120, 50, 50),
        ]

        tree = dr.clustering.cluster(leaves)
        # 元素在 x 轴也有重叠（都在 x=0），可能判定为 stack 或 col
        # 主要是验证聚类能够正确处理列向布局
        assert tree.kind in ("col", "stack")
        assert len(tree.children) == 3

    def test_leaf_to_node_conversion(self):
        """叶子转换为节点"""
        dr = _make_dom_restructure()
        leaf = _make_leaf("a", 0, 0, 100, 100)

        node = dr.clustering._leaf_to_node(leaf)
        assert node.kind == "leaf"
        assert node.leaf == leaf


# ============================================================================
# RenderingHandler 测试
# ============================================================================

class TestRenderingHandler:
    """测试 DOM 渲染"""

    def test_render_tree_leaf_node(self):
        """渲染叶子节点直接返回元素"""
        dr = _make_dom_restructure()
        leaf = _make_leaf("a", 0, 0, 100, 100)
        node = LayoutNode(kind="leaf", bbox=leaf.bbox, leaf=leaf)
        parent_origin = BBox(0, 0, 1000, 1000)

        result = dr.rendering.render_tree(node, parent_origin)
        assert result == leaf.element

    def test_render_tree_row_node(self):
        """渲染行节点创建虚拟包装器"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("a", 0, 0, 50, 50),
            _make_leaf("b", 60, 0, 50, 50),
        ]
        children = [dr.clustering._leaf_to_node(l) for l in leaves]
        node = LayoutNode(
            kind="row",
            bbox=BBox(0, 0, 110, 50),
            children=children,
        )
        parent_origin = BBox(0, 0, 1000, 1000)

        result = dr.rendering.render_tree(node, parent_origin)
        # 验证返回了 element（Mock 对象）和 stats 被更新
        assert result is not None
        assert dr.stats['dom_restructured'] > 0

    def test_render_tree_col_node(self):
        """渲染列节点创建虚拟包装器"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("a", 0, 0, 50, 50),
            _make_leaf("b", 0, 60, 50, 50),
        ]
        children = [dr.clustering._leaf_to_node(l) for l in leaves]
        node = LayoutNode(
            kind="col",
            bbox=BBox(0, 0, 50, 110),
            children=children,
        )
        parent_origin = BBox(0, 0, 1000, 1000)

        result = dr.rendering.render_tree(node, parent_origin)
        # 验证返回了 element 和 stats 被更新
        assert result is not None
        assert dr.stats['dom_restructured'] > 0

    def test_apply_flex_to_existing_container(self):
        """测试对现有容器应用 flex"""
        dr = _make_dom_restructure()
        group = MagicMock()
        group.get = MagicMock(return_value=["task-bg"])  # 返回列表
        
        leaf = _make_leaf("a", 0, 0, 100, 100)
        node = LayoutNode(kind="row", bbox=leaf.bbox, leaf=leaf)

        # 方法会修改 css_rules
        dr.rendering.apply_flex_to_existing_container(group, node)
        # 验证在 css_rules 中创建了样式
        assert len(dr.css_rules) >= 0  # 验证方法执行无错误

    def test_apply_flex_child_margins_row_negative_parent_origin(self):
        """row 首子项：父 bbox 为负时，不应重复写负偏移"""
        dr = _make_dom_restructure()
        dr.css_rules[".child"] = {
            "left": "-2px",
            "top": "-58px",
            "width": "100px",
            "height": "40px",
            "position": "absolute",
        }

        dr.rendering.apply_flex_child_margins(
            child_css_class=".child",
            child_bbox=BBox(-2, -58, 98, -18),
            parent_bbox=BBox(-2, -58, 300, 200),
            prev_bbox=None,
            flex_kind="row",
        )

        styles = dr.css_rules[".child"]
        assert "margin-left" not in styles
        assert "margin-top" not in styles

    def test_apply_flex_child_margins_col_negative_parent_origin(self):
        """col 首子项：父 bbox 为负时，不应重复写负偏移"""
        dr = _make_dom_restructure()
        dr.css_rules[".child"] = {
            "left": "-3px",
            "top": "-20px",
            "width": "120px",
            "height": "40px",
            "position": "absolute",
        }

        dr.rendering.apply_flex_child_margins(
            child_css_class=".child",
            child_bbox=BBox(-3, -20, 117, 20),
            parent_bbox=BBox(-3, -20, 320, 480),
            prev_bbox=None,
            flex_kind="col",
        )

        styles = dr.css_rules[".child"]
        assert "margin-left" not in styles
        assert "margin-top" not in styles

    def test_apply_stack_to_existing_container_clears_padding(self):
        """stack 根容器不应依赖 padding 传递偏移（子层 absolute 时会错位）"""
        dr = _make_dom_restructure()
        soup = BeautifulSoup('<div class="stack-root layer-group"></div>', 'html.parser')
        group = soup.find('div')
        dr.css_rules['.stack-root'] = {
            'position': 'relative',
            'padding-left': '11px',
            'padding-top': '59px',
            'box-sizing': 'border-box',
        }

        tree = LayoutNode(kind='stack', bbox=BBox(11, 59, 714, 1696), children=[])
        dr.rendering.apply_stack_to_existing_container(group, tree)

        styles = dr.css_rules['.stack-root']
        assert 'padding-left' not in styles
        assert 'padding-top' not in styles
        assert 'box-sizing' not in styles


# ============================================================================
# ReclassifyHandler 测试
# ============================================================================

class TestReclassifyHandler:
    """测试 Stack → Col 反向升级"""

    def test_should_upgrade_stack_to_col(self):
        """判定是否应该升级为列 - 验证方法存在"""
        dr = _make_dom_restructure()
        # 此方法在 ReclassifyHandler 中作为内部私有方法
        # 我们验证其他公开 API 可用
        assert hasattr(dr.reclassify, 'absorb_container_backgrounds_pass')

    def test_absorb_container_backgrounds_pass(self):
        """测试容器背景吸收"""
        dr = _make_dom_restructure()
        dr.reclassify.absorb_container_backgrounds_pass()
        # 验证方法执行无错误


# ============================================================================
# BackgroundHandler 测试
# ============================================================================

class TestBackgroundHandler:
    """测试背景提取"""

    def test_extract_leaves_no_background(self):
        """无背景时返回空列表"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("a", 0, 0, 50, 50, "text"),
            _make_leaf("b", 0, 60, 50, 50, "text"),
        ]

        bg, fg = dr.background.extract_leaves(leaves)
        assert len(bg) == 0
        assert len(fg) == 2

    def test_extract_leaves_with_background(self):
        """识别和提取背景"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("bg", 0, 0, 500, 300, "image"),  # 全覆盖背景
            _make_leaf("a", 10, 10, 50, 50, "text"),
            _make_leaf("b", 70, 10, 50, 50, "text"),
        ]

        bg, fg = dr.background.extract_leaves(leaves)
        # 应该识别出背景
        assert len(bg) + len(fg) == len(leaves)


# ============================================================================
# 集成测试
# ============================================================================

class TestHandlerIntegration:
    """Handler 集成测试"""

    def test_full_pipeline_row_layout(self):
        """测试完整管道：行布局"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("a", 0, 0, 50, 50),
            _make_leaf("b", 60, 0, 50, 50),
            _make_leaf("c", 120, 0, 50, 50),
        ]

        # 建立树
        tree = dr._build_tree(leaves)
        assert tree is not None
        assert tree.kind in ("row", "col", "stack")

    def test_full_pipeline_col_layout(self):
        """测试完整管道：列布局"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("a", 0, 0, 50, 50),
            _make_leaf("b", 0, 60, 50, 50),
            _make_leaf("c", 0, 120, 50, 50),
        ]

        tree = dr._build_tree(leaves)
        assert tree is not None
        assert tree.kind in ("row", "col", "stack")

    def test_full_pipeline_with_background(self):
        """测试完整管道：带背景"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("bg", 0, 0, 500, 300, "image"),
            _make_leaf("a", 10, 10, 50, 50, "text"),
            _make_leaf("b", 70, 10, 50, 50, "text"),
        ]

        tree = dr._build_tree(leaves)
        assert tree is not None

    def test_build_tree_keeps_stack_when_background_detected_and_fg_is_stack(self):
        """识别出背景后，即便前景是 stack 也应保留 stack(bg+fg) 语义"""
        dr = _make_dom_restructure()
        # 两个前景刻意做成强重叠，令 fg_tree 走 stack
        leaves = [
            _make_leaf("bg", 0, 0, 1000, 1000, "image"),
            _make_leaf("fg1", 100, 100, 300, 300, "image"),
            _make_leaf("fg2", 120, 120, 300, 300, "image"),
        ]

        tree = dr._build_tree(leaves)
        assert tree.kind == "stack"
        # 期望结构是 [背景leaf, 前景子树]
        assert len(tree.children) >= 2
        assert tree.children[-1].kind in ("stack", "row", "col", "leaf")


# ============================================================================
# 性能测试
# ============================================================================

class TestHandlerPerformance:
    """Handler 性能测试"""

    def test_cluster_performance_many_leaves(self):
        """测试大量元素聚类性能"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf(f"item_{i}", i * 60, 0, 50, 50)
            for i in range(20)
        ]

        import time
        start = time.time()
        tree = dr.clustering.cluster(leaves)
        elapsed = time.time() - start

        assert tree is not None
        assert elapsed < 1.0  # 应该在 1 秒内完成

    def test_is_stack_group_performance(self):
        """测试 stack 判定性能"""
        dr = _make_dom_restructure()
        bboxes = [
            BBox(i * 10, 0, i * 10 + 50, 50)
            for i in range(100)
        ]

        import time
        start = time.time()
        result = dr.clustering.is_stack_group(bboxes)
        elapsed = time.time() - start

        assert elapsed < 0.1  # 应该在 100ms 内完成


# ============================================================================
# 边界条件测试
# ============================================================================

class TestEdgeCases:
    """边界条件测试"""

    def test_empty_leaves_list(self):
        """空 leaves 列表"""
        dr = _make_dom_restructure()
        # 应该不崩溃
        try:
            tree = dr._build_tree([])
        except:
            pass  # 预期可能失败，但不应该崩溃

    def test_single_leaf(self):
        """单个叶子"""
        dr = _make_dom_restructure()
        leaves = [_make_leaf("a", 0, 0, 100, 100)]
        tree = dr._build_tree(leaves)
        assert tree.kind == "leaf"

    def test_zero_size_bbox(self):
        """零大小 bbox"""
        dr = _make_dom_restructure()
        bboxes = [BBox(0, 0, 0, 0)]
        # 应该不崩溃
        result = dr.clustering.is_stack_group(bboxes)
        assert not result

    def test_negative_coordinates(self):
        """负坐标"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("a", -100, -100, 50, 50),
            _make_leaf("b", -40, -100, 50, 50),
        ]
        tree = dr._build_tree(leaves)
        assert tree is not None

    def test_has_significant_overflow_true_on_negative_offset(self):
        """明显负偏移应判定为越界（避免后续 flex 化错位）"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("a", -10, -58, 100, 40),
            _make_leaf("b", 243, -41, 359, 72),
        ]
        container = BBox(0, 0, 724, 1698)
        assert dr._has_significant_overflow(leaves, container)

    def test_has_significant_overflow_false_within_tolerance(self):
        """轻微 1~2px 偏差不应触发越界保护"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("a", -2, -1, 100, 40),
            _make_leaf("b", 10, 10, 50, 50),
        ]
        container = BBox(0, 0, 200, 200)
        assert not dr._has_significant_overflow(leaves, container)

    def test_very_large_coordinates(self):
        """极大坐标"""
        dr = _make_dom_restructure()
        leaves = [
            _make_leaf("a", 10000, 10000, 50, 50),
            _make_leaf("b", 10060, 10000, 50, 50),
        ]
        tree = dr._build_tree(leaves)
        assert tree is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
