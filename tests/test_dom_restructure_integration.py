"""DOMRestructure 集成测试（P2）：端到端真实 HTML → Flex DOM 产出。

与 test_dom_restructure.py 的纯逻辑单测互补。这里使用 BeautifulSoup 构造
真实 DOM，调用 restructure_dom() 完成完整流程，验证：

  - restructure_dom() 对不同布局形态的 group 产出正确的 flex DOM
  - _extract_background_leaves / _extract_tall_decor_leaves 完整路径
  - _build_tree → _cluster → _cluster_row 决策链
  - _is_fake_multirow_stack 回退 stack
  - _apply_flex_child_margins 正确写入 margin
  - _render_tree / _render_stack / _render_flex 产出正确 DOM
  - _bg_passes_safety_filter / _is_absorbable_bg_leaf 条件分支
  - _bbox_covers_main_axis / _bbox_dominates_both_axes / _bbox_contains_all
  - _are_x_aligned
  - _absorb_container_backgrounds_pass → _try_reclassify_stack_to_col
  - _apply_flex_to_existing_container / _apply_stack_to_existing_container
  - _parse_position_px / _parse_size_px 静态方法
"""
import pytest
from bs4 import BeautifulSoup
from unittest.mock import MagicMock, patch

from targets.html.postprocess.layout_optimizer.transformers.dom_restructure import (
    BBox,
    ClusterConfig,
    DOMRestructure,
    LeafInfo,
    LayoutNode,
)


# ===========================================================================
# 辅助工具
# ===========================================================================

def _make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, 'html.parser')


def _make_dr(soup, css_rules=None, images_dir=None):
    """创建完整的 DOMRestructure 实例"""
    return DOMRestructure(
        soup=soup,
        css_rules=css_rules or {},
        stats={'dom_restructured': 0},
        images_dir=images_dir,
    )


def _build_group_html(children_specs):
    """构造一个 layer-group 包含多个子 div（模拟真实 PSD layer）

    children_specs: list of dict with keys:
      name, cls, data_type (default 'image')
    """
    children_html = ''
    for spec in children_specs:
        cls = spec['cls']
        name = spec.get('name', cls)
        dt = spec.get('data_type', 'image')
        children_html += (
            f'<div class="{cls}" data-name="{name}" data-type="{dt}"></div>\n'
        )
    return f'<div class="group-1 layer-group" data-name="test-group">\n{children_html}</div>'


def _build_css_for_leaves(specs):
    """构造与 children_specs 对应的 css_rules

    specs: list of dict with keys:
      cls, left, top, width, height, extras (optional dict)
    """
    rules = {}
    for s in specs:
        cls = f".{s['cls']}"
        styles = {
            'left': f"{s['left']}px",
            'top': f"{s['top']}px",
            'width': f"{s['width']}px",
            'height': f"{s['height']}px",
            'position': 'absolute',
        }
        if 'extras' in s:
            styles.update(s['extras'])
        rules[cls] = styles
    return rules


# ===========================================================================
# _bg_passes_safety_filter 测试
# ===========================================================================

class TestBgPassesSafetyFilter:
    def test_image_normal_opaque(self):
        leaf = LeafInfo(
            element=MagicMock(), css_class='.bg', name='bg',
            data_type='image', bbox=BBox(0, 0, 100, 100),
        )
        styles = {'opacity': '1', 'mix-blend-mode': 'normal'}
        assert DOMRestructure._bg_passes_safety_filter(leaf, styles) is True

    def test_not_image(self):
        leaf = LeafInfo(
            element=MagicMock(), css_class='.txt', name='txt',
            data_type='text', bbox=BBox(0, 0, 100, 100),
        )
        styles = {}
        assert DOMRestructure._bg_passes_safety_filter(leaf, styles) is False

    def test_blend_mode_multiply(self):
        leaf = LeafInfo(
            element=MagicMock(), css_class='.bg', name='bg',
            data_type='image', bbox=BBox(0, 0, 100, 100),
        )
        styles = {'mix-blend-mode': 'multiply'}
        assert DOMRestructure._bg_passes_safety_filter(leaf, styles) is False

    def test_low_opacity(self):
        leaf = LeafInfo(
            element=MagicMock(), css_class='.bg', name='bg',
            data_type='image', bbox=BBox(0, 0, 100, 100),
        )
        styles = {'opacity': '0.5'}
        assert DOMRestructure._bg_passes_safety_filter(leaf, styles) is False

    def test_missing_opacity_and_blend(self):
        """无 opacity 和 blend 字段 → 默认通过"""
        leaf = LeafInfo(
            element=MagicMock(), css_class='.bg', name='bg',
            data_type='image', bbox=BBox(0, 0, 100, 100),
        )
        styles = {}
        assert DOMRestructure._bg_passes_safety_filter(leaf, styles) is True

    def test_opacity_edge_099(self):
        """opacity=0.99 刚好通过"""
        leaf = LeafInfo(
            element=MagicMock(), css_class='.bg', name='bg',
            data_type='image', bbox=BBox(0, 0, 100, 100),
        )
        styles = {'opacity': '0.99'}
        assert DOMRestructure._bg_passes_safety_filter(leaf, styles) is True

    def test_opacity_edge_098(self):
        """opacity=0.98 不通过"""
        leaf = LeafInfo(
            element=MagicMock(), css_class='.bg', name='bg',
            data_type='image', bbox=BBox(0, 0, 100, 100),
        )
        styles = {'opacity': '0.98'}
        assert DOMRestructure._bg_passes_safety_filter(leaf, styles) is False

    def test_invalid_opacity_value(self):
        leaf = LeafInfo(
            element=MagicMock(), css_class='.bg', name='bg',
            data_type='image', bbox=BBox(0, 0, 100, 100),
        )
        styles = {'opacity': 'abc'}
        assert DOMRestructure._bg_passes_safety_filter(leaf, styles) is False


# ===========================================================================
# _is_absorbable_bg_leaf 测试
# ===========================================================================

class TestIsAbsorbableBgLeaf:
    def test_with_background_image(self):
        soup = _make_soup('<div></div>')
        dr = _make_dr(soup)
        leaf = LeafInfo(
            element=MagicMock(), css_class='.bg', name='bg',
            data_type='image', bbox=BBox(0, 0, 100, 100),
        )
        styles = {'background-image': 'url("images/bg.png")'}
        assert dr._is_absorbable_bg_leaf(leaf, styles) is True

    def test_without_background_image(self):
        soup = _make_soup('<div></div>')
        dr = _make_dr(soup)
        leaf = LeafInfo(
            element=MagicMock(), css_class='.bg', name='bg',
            data_type='image', bbox=BBox(0, 0, 100, 100),
        )
        styles = {}
        assert dr._is_absorbable_bg_leaf(leaf, styles) is False

    def test_fails_safety_filter(self):
        soup = _make_soup('<div></div>')
        dr = _make_dr(soup)
        leaf = LeafInfo(
            element=MagicMock(), css_class='.bg', name='bg',
            data_type='text', bbox=BBox(0, 0, 100, 100),
        )
        styles = {'background-image': 'url("images/bg.png")'}
        assert dr._is_absorbable_bg_leaf(leaf, styles) is False


# ===========================================================================
# _bbox_covers_main_axis 测试
# ===========================================================================

class TestBboxCoversMainAxis:
    def test_covers_width(self):
        bbox = BBox(0, 20, 200, 80)
        envelope = BBox(0, 0, 200, 100)
        assert DOMRestructure._bbox_covers_main_axis(bbox, envelope, 2.0) is True

    def test_covers_height(self):
        bbox = BBox(20, 0, 80, 100)
        envelope = BBox(0, 0, 200, 100)
        assert DOMRestructure._bbox_covers_main_axis(bbox, envelope, 2.0) is True

    def test_covers_neither(self):
        bbox = BBox(20, 20, 80, 80)
        envelope = BBox(0, 0, 200, 100)
        assert DOMRestructure._bbox_covers_main_axis(bbox, envelope, 2.0) is False

    def test_with_tolerance(self):
        """允许 2px 容忍度"""
        bbox = BBox(1, 1, 199, 99)
        envelope = BBox(0, 0, 200, 100)
        assert DOMRestructure._bbox_covers_main_axis(bbox, envelope, 2.0) is True


# ===========================================================================
# _bbox_dominates_both_axes 测试
# ===========================================================================

class TestBboxDominatesBothAxes:
    def test_full_coverage(self):
        bbox = BBox(0, 0, 100, 100)
        envelope = BBox(0, 0, 100, 100)
        assert DOMRestructure._bbox_dominates_both_axes(bbox, envelope, 0.8) is True

    def test_80_percent(self):
        """覆盖 80% → 刚好通过"""
        bbox = BBox(10, 10, 90, 90)
        envelope = BBox(0, 0, 100, 100)
        # cover_w = 80/100=0.8, cover_h = 80/100=0.8
        assert DOMRestructure._bbox_dominates_both_axes(bbox, envelope, 0.8) is True

    def test_below_threshold(self):
        """覆盖不到 80% → 不通过"""
        bbox = BBox(30, 30, 70, 70)
        envelope = BBox(0, 0, 100, 100)
        # cover_w = 40/100=0.4, cover_h = 40/100=0.4
        assert DOMRestructure._bbox_dominates_both_axes(bbox, envelope, 0.8) is False

    def test_zero_envelope(self):
        bbox = BBox(0, 0, 100, 100)
        envelope = BBox(50, 50, 50, 50)  # zero-size
        assert DOMRestructure._bbox_dominates_both_axes(bbox, envelope, 0.8) is False


# ===========================================================================
# _bbox_contains_all 测试
# ===========================================================================

class TestBboxContainsAll:
    def test_contains_all(self):
        outer = BBox(0, 0, 200, 200)
        leaves = [
            LeafInfo(MagicMock(), '.a', 'a', 'image', BBox(10, 10, 190, 190)),
            LeafInfo(MagicMock(), '.b', 'b', 'image', BBox(20, 20, 180, 180)),
        ]
        assert DOMRestructure._bbox_contains_all(outer, leaves, 2.0) is True

    def test_not_contains(self):
        outer = BBox(10, 10, 100, 100)
        leaves = [
            LeafInfo(MagicMock(), '.a', 'a', 'image', BBox(0, 0, 50, 50)),  # left < outer.left
        ]
        assert DOMRestructure._bbox_contains_all(outer, leaves, 2.0) is False

    def test_skips_self(self):
        """outer 自身的 bbox 被跳过（通过 is 比较）"""
        outer = BBox(0, 0, 200, 200)
        leaf_with_same_bbox = LeafInfo(MagicMock(), '.a', 'a', 'image', outer)
        # outer 作为某个 leaf 的 bbox 被跳过
        assert DOMRestructure._bbox_contains_all(outer, [leaf_with_same_bbox], 2.0) is True

    def test_within_tolerance(self):
        """在容忍度内算通过"""
        outer = BBox(0, 0, 100, 100)
        # leaf 稍微突出 1px（在 2px 容忍内）
        leaves = [
            LeafInfo(MagicMock(), '.a', 'a', 'image', BBox(-1, -1, 101, 101)),
        ]
        assert DOMRestructure._bbox_contains_all(outer, leaves, 2.0) is True

    def test_outside_tolerance(self):
        """超出容忍度 → 不通过"""
        outer = BBox(0, 0, 100, 100)
        leaves = [
            LeafInfo(MagicMock(), '.a', 'a', 'image', BBox(-5, -5, 105, 105)),
        ]
        assert DOMRestructure._bbox_contains_all(outer, leaves, 2.0) is False


# ===========================================================================
# _are_x_aligned 测试
# ===========================================================================

class TestAreXAligned:
    def test_aligned(self):
        leaves = [
            LeafInfo(MagicMock(), '.a', 'a', 'text', BBox(10, 0, 200, 20)),
            LeafInfo(MagicMock(), '.b', 'b', 'text', BBox(10, 25, 180, 45)),
            LeafInfo(MagicMock(), '.c', 'c', 'text', BBox(10, 50, 190, 70)),
        ]
        assert DOMRestructure._are_x_aligned(leaves, 0.2) is True

    def test_not_aligned(self):
        leaves = [
            LeafInfo(MagicMock(), '.a', 'a', 'text', BBox(10, 0, 200, 20)),
            LeafInfo(MagicMock(), '.b', 'b', 'text', BBox(300, 25, 400, 45)),
        ]
        assert DOMRestructure._are_x_aligned(leaves, 0.2) is False

    def test_single_element(self):
        leaves = [LeafInfo(MagicMock(), '.a', 'a', 'text', BBox(10, 0, 200, 20))]
        assert DOMRestructure._are_x_aligned(leaves, 0.2) is True

    def test_empty(self):
        assert DOMRestructure._are_x_aligned([], 0.2) is True


# ===========================================================================
# _is_fake_multirow_stack 测试
# ===========================================================================

class TestIsFakeMultirowStack:
    def test_two_rows_single_elem_x_aligned(self):
        """2 行各 1 元素 + X 完全对齐 → fake stack"""
        soup = _make_soup('<div></div>')
        dr = _make_dr(soup)
        rows = [
            [LeafInfo(MagicMock(), '.a', 'a', 'image', BBox(0, 0, 100, 50))],
            [LeafInfo(MagicMock(), '.b', 'b', 'image', BBox(0, 60, 100, 110))],
        ]
        assert dr._is_fake_multirow_stack(rows) is True

    def test_too_many_rows(self):
        """≥ 4 行（默认 fake_multirow_max_rows=4） → 不回退"""
        soup = _make_soup('<div></div>')
        dr = _make_dr(soup)
        rows = [
            [LeafInfo(MagicMock(), f'.r{i}', f'r{i}', 'image',
                      BBox(0, i * 60, 100, i * 60 + 50))]
            for i in range(4)
        ]
        assert dr._is_fake_multirow_stack(rows) is False

    def test_multi_elem_row(self):
        """某行有多个元素 → 不回退（真正的网格行）"""
        soup = _make_soup('<div></div>')
        dr = _make_dr(soup)
        rows = [
            [
                LeafInfo(MagicMock(), '.a', 'a', 'image', BBox(0, 0, 50, 50)),
                LeafInfo(MagicMock(), '.b', 'b', 'image', BBox(60, 0, 110, 50)),
            ],
            [LeafInfo(MagicMock(), '.c', 'c', 'image', BBox(0, 60, 100, 110))],
        ]
        assert dr._is_fake_multirow_stack(rows) is False

    def test_x_not_aligned(self):
        """2 行各 1 元素但 X 不对齐 → 不回退"""
        soup = _make_soup('<div></div>')
        dr = _make_dr(soup)
        rows = [
            [LeafInfo(MagicMock(), '.a', 'a', 'image', BBox(0, 0, 50, 50))],
            [LeafInfo(MagicMock(), '.b', 'b', 'image', BBox(200, 60, 400, 110))],
        ]
        assert dr._is_fake_multirow_stack(rows) is False


# ===========================================================================
# _extract_tall_decor_leaves 测试
# ===========================================================================

class TestExtractTallDecorLeaves:
    def _make_dr_for_decor(self, css_rules=None):
        soup = _make_soup('<div class="g layer-group"><div class="a"></div></div>')
        dr = _make_dr(soup, css_rules or {})
        return dr

    def test_tall_decor_extracted(self):
        """高瘦 icon 跨过 3 个对齐文本 → 剥离"""
        dr = self._make_dr_for_decor()
        # 3 行文本 (left=10, width=200) + 1 个高瘦 icon (left=5, width=40, height=84)
        texts = [
            LeafInfo(MagicMock(), f'.t{i}', f't{i}', 'text',
                     BBox(10, i * 30, 210, i * 30 + 20))
            for i in range(3)
        ]
        icon = LeafInfo(MagicMock(), '.icon', 'icon', 'image',
                        BBox(5, 5, 45, 89))  # width=40, height=84
        leaves = texts + [icon]
        decor, fg = dr._extract_tall_decor_leaves(leaves)
        assert len(decor) == 1
        assert decor[0].name == 'icon'
        assert len(fg) == 3

    def test_not_tall_enough(self):
        """icon 高度不够 → 不剥离"""
        dr = self._make_dr_for_decor()
        texts = [
            LeafInfo(MagicMock(), f'.t{i}', f't{i}', 'text',
                     BBox(10, i * 30, 210, i * 30 + 20))
            for i in range(3)
        ]
        # height=30, 中位 text height=20, ratio=30/20=1.5 < 2.0（默认）
        icon = LeafInfo(MagicMock(), '.icon', 'icon', 'image',
                        BBox(5, 5, 45, 35))  # height=30
        leaves = texts + [icon]
        decor, fg = dr._extract_tall_decor_leaves(leaves)
        assert len(decor) == 0
        assert len(fg) == 4

    def test_disabled_by_config(self):
        """配置关闭 → 不剥离"""
        dr = self._make_dr_for_decor()
        dr.config.enable_tall_decor_extraction = False
        texts = [
            LeafInfo(MagicMock(), f'.t{i}', f't{i}', 'text',
                     BBox(10, i * 30, 210, i * 30 + 20))
            for i in range(3)
        ]
        icon = LeafInfo(MagicMock(), '.icon', 'icon', 'image',
                        BBox(5, 5, 45, 89))
        decor, fg = dr._extract_tall_decor_leaves(texts + [icon])
        assert len(decor) == 0

    def test_crossed_leaves_not_x_aligned(self):
        """被跨过的 leaves 不在同列 → 不剥离"""
        dr = self._make_dr_for_decor()
        # 3 个文本但 X 完全不对齐
        texts = [
            LeafInfo(MagicMock(), '.t0', 't0', 'text', BBox(10, 0, 100, 20)),
            LeafInfo(MagicMock(), '.t1', 't1', 'text', BBox(300, 30, 400, 50)),
            LeafInfo(MagicMock(), '.t2', 't2', 'text', BBox(600, 60, 700, 80)),
        ]
        icon = LeafInfo(MagicMock(), '.icon', 'icon', 'image',
                        BBox(5, 0, 45, 84))  # height=84 >> 20*2
        leaves = texts + [icon]
        decor, fg = dr._extract_tall_decor_leaves(leaves)
        assert len(decor) == 0


# ===========================================================================
# _extract_background_leaves 测试
# ===========================================================================

class TestExtractBackgroundLeaves:
    def test_full_cover_bg_extracted(self):
        """image 完全覆盖所有 leaves → 被剥离"""
        html = _build_group_html([
            {'cls': 'bg', 'data_type': 'image'},
            {'cls': 'txt', 'data_type': 'text'},
        ])
        soup = _make_soup(html)
        css = {
            '.bg': {
                'left': '0px', 'top': '0px',
                'width': '300px', 'height': '200px',
                'position': 'absolute',
                'background-image': 'url("images/bg.png")',
            },
            '.txt': {
                'left': '10px', 'top': '10px',
                'width': '100px', 'height': '30px',
                'position': 'absolute',
            },
        }
        dr = _make_dr(soup, css)
        group = soup.find('div', class_='layer-group')
        leaves = dr._collect_leaves(group)
        bg_leaves, fg_leaves = dr._extract_background_leaves(leaves)
        assert len(bg_leaves) == 1
        assert bg_leaves[0].name == 'bg'
        assert len(fg_leaves) == 1

    def test_non_image_not_extracted(self):
        """非 image 类型不能做背景剥离"""
        html = _build_group_html([
            {'cls': 'big', 'data_type': 'text'},
            {'cls': 'small', 'data_type': 'image'},
        ])
        soup = _make_soup(html)
        css = {
            '.big': {
                'left': '0px', 'top': '0px',
                'width': '300px', 'height': '200px',
                'position': 'absolute',
            },
            '.small': {
                'left': '10px', 'top': '10px',
                'width': '50px', 'height': '50px',
                'position': 'absolute',
            },
        }
        dr = _make_dr(soup, css)
        group = soup.find('div', class_='layer-group')
        leaves = dr._collect_leaves(group)
        bg_leaves, fg_leaves = dr._extract_background_leaves(leaves)
        assert len(bg_leaves) == 0
        assert len(fg_leaves) == 2

    def test_low_opacity_bg_not_extracted(self):
        """opacity < 0.99 的 image 不做背景剥离"""
        html = _build_group_html([
            {'cls': 'bg', 'data_type': 'image'},
            {'cls': 'fg', 'data_type': 'image'},
        ])
        soup = _make_soup(html)
        css = {
            '.bg': {
                'left': '0px', 'top': '0px',
                'width': '300px', 'height': '200px',
                'position': 'absolute',
                'opacity': '0.5',
                'background-image': 'url("images/bg.png")',
            },
            '.fg': {
                'left': '10px', 'top': '10px',
                'width': '50px', 'height': '50px',
                'position': 'absolute',
            },
        }
        dr = _make_dr(soup, css)
        group = soup.find('div', class_='layer-group')
        leaves = dr._collect_leaves(group)
        bg_leaves, fg_leaves = dr._extract_background_leaves(leaves)
        assert len(bg_leaves) == 0


# ===========================================================================
# _build_tree 聚类决策链测试
# ===========================================================================

class TestBuildTree:
    def _setup(self, specs):
        """从 specs 构建 DR + leaves"""
        html = _build_group_html([{'cls': s['cls'], 'data_type': s.get('data_type', 'image')} for s in specs])
        soup = _make_soup(html)
        css = _build_css_for_leaves(specs)
        dr = _make_dr(soup, css)
        group = soup.find('div', class_='layer-group')
        leaves = dr._collect_leaves(group)
        return dr, leaves

    def test_row_layout(self):
        """水平排列 → row"""
        dr, leaves = self._setup([
            {'cls': 'a', 'left': 0, 'top': 0, 'width': 80, 'height': 50},
            {'cls': 'b', 'left': 100, 'top': 5, 'width': 80, 'height': 45},
            {'cls': 'c', 'left': 200, 'top': 3, 'width': 80, 'height': 48},
        ])
        tree = dr._build_tree(leaves)
        assert tree.kind == 'row'
        assert len(tree.children) == 3

    def test_col_layout(self):
        """垂直排列 → col（5 行超过 fake_multirow_max_rows=4）"""
        dr, leaves = self._setup([
            {'cls': 'a', 'left': 0, 'top': 0, 'width': 100, 'height': 30},
            {'cls': 'b', 'left': 5, 'top': 80, 'width': 90, 'height': 30},
            {'cls': 'c', 'left': 3, 'top': 160, 'width': 95, 'height': 30},
            {'cls': 'd', 'left': 2, 'top': 240, 'width': 98, 'height': 30},
            {'cls': 'e', 'left': 4, 'top': 320, 'width': 92, 'height': 30},
        ])
        tree = dr._build_tree(leaves)
        assert tree.kind == 'col'
        assert len(tree.children) == 5

    def test_stack_layout(self):
        """完全重叠 → stack"""
        dr, leaves = self._setup([
            {'cls': 'a', 'left': 0, 'top': 0, 'width': 100, 'height': 100},
            {'cls': 'b', 'left': 5, 'top': 5, 'width': 95, 'height': 95},
            {'cls': 'c', 'left': 10, 'top': 10, 'width': 85, 'height': 85},
        ])
        tree = dr._build_tree(leaves)
        assert tree.kind == 'stack'

    def test_bg_extraction_produces_stack(self):
        """大底图 + 前景行 → stack(bg + row)"""
        dr, leaves = self._setup([
            {'cls': 'bg', 'left': 0, 'top': 0, 'width': 300, 'height': 200,
             'data_type': 'image', 'extras': {'background-image': 'url("images/bg.png")'}},
            {'cls': 'a', 'left': 10, 'top': 80, 'width': 80, 'height': 40},
            {'cls': 'b', 'left': 110, 'top': 85, 'width': 80, 'height': 35},
            {'cls': 'c', 'left': 210, 'top': 82, 'width': 80, 'height': 38},
        ])
        tree = dr._build_tree(leaves)
        assert tree.kind == 'stack'
        # 应有 bg leaf + row 子树
        kinds = [c.kind for c in tree.children]
        assert 'leaf' in kinds  # bg
        assert 'row' in kinds  # fg row

    def test_single_leaf(self):
        dr, leaves = self._setup([
            {'cls': 'a', 'left': 0, 'top': 0, 'width': 100, 'height': 50},
        ])
        tree = dr._build_tree(leaves)
        assert tree.kind == 'leaf'


# ===========================================================================
# _apply_flex_child_margins 测试
# ===========================================================================

class TestApplyFlexChildMargins:
    def test_row_first_child(self):
        """row 第一个子元素：margin-left = child.left - parent.left"""
        soup = _make_soup('<div></div>')
        css = {'.child': {'left': '20px', 'top': '10px', 'width': '50px', 'height': '50px', 'position': 'absolute'}}
        dr = _make_dr(soup, css)
        parent_bbox = BBox(10, 5, 200, 100)
        child_bbox = BBox(20, 10, 70, 60)
        dr._apply_flex_child_margins(
            '.child', child_bbox, parent_bbox, prev_bbox=None, flex_kind='row')
        styles = css['.child']
        assert styles.get('margin-left') == '10px'
        assert styles.get('margin-top') == '5px'
        assert 'left' not in styles
        assert 'top' not in styles

    def test_row_second_child(self):
        """row 第二个子元素：margin-left = child.left - prev.right"""
        soup = _make_soup('<div></div>')
        css = {'.child': {'position': 'absolute'}}
        dr = _make_dr(soup, css)
        parent_bbox = BBox(0, 0, 200, 100)
        child_bbox = BBox(110, 5, 160, 55)
        prev_bbox = BBox(0, 0, 100, 50)
        dr._apply_flex_child_margins(
            '.child', child_bbox, parent_bbox, prev_bbox=prev_bbox, flex_kind='row')
        styles = css['.child']
        assert styles.get('margin-left') == '10px'
        assert styles.get('margin-top') == '5px'

    def test_col_first_child(self):
        """col 第一个子元素：margin-top = child.top - parent.top"""
        soup = _make_soup('<div></div>')
        css = {'.child': {'position': 'absolute'}}
        dr = _make_dr(soup, css)
        parent_bbox = BBox(5, 10, 100, 200)
        child_bbox = BBox(15, 30, 80, 60)
        dr._apply_flex_child_margins(
            '.child', child_bbox, parent_bbox, prev_bbox=None, flex_kind='col')
        styles = css['.child']
        assert styles.get('margin-top') == '20px'
        assert styles.get('margin-left') == '10px'

    def test_col_second_child(self):
        """col 第二个子元素：margin-top = child.top - prev.bottom"""
        soup = _make_soup('<div></div>')
        css = {'.child': {'position': 'absolute'}}
        dr = _make_dr(soup, css)
        parent_bbox = BBox(0, 0, 100, 200)
        child_bbox = BBox(5, 80, 80, 120)
        prev_bbox = BBox(0, 0, 100, 60)
        dr._apply_flex_child_margins(
            '.child', child_bbox, parent_bbox, prev_bbox=prev_bbox, flex_kind='col')
        styles = css['.child']
        assert styles.get('margin-top') == '20px'
        assert styles.get('margin-left') == '5px'

    def test_flex_shrink_set(self):
        """flex-shrink: 0 总是被设置"""
        soup = _make_soup('<div></div>')
        css = {'.child': {'position': 'absolute'}}
        dr = _make_dr(soup, css)
        dr._apply_flex_child_margins(
            '.child', BBox(0, 0, 50, 50), BBox(0, 0, 100, 100),
            prev_bbox=None, flex_kind='row')
        assert css['.child']['flex-shrink'] == '0'

    def test_position_relative_for_stack_child(self):
        """stack wrapper 子元素保持 position:relative"""
        soup = _make_soup('<div></div>')
        css = {'.child': {'position': 'absolute'}}
        dr = _make_dr(soup, css)
        dr._apply_flex_child_margins(
            '.child', BBox(0, 0, 50, 50), BBox(0, 0, 100, 100),
            prev_bbox=None, flex_kind='row', child_position='relative')
        assert css['.child']['position'] == 'relative'

    def test_z_index_forces_relative(self):
        """有 z-index 的 static 子元素 → 强制 relative"""
        soup = _make_soup('<div></div>')
        css = {'.child': {'position': 'absolute', 'z-index': '5'}}
        dr = _make_dr(soup, css)
        dr._apply_flex_child_margins(
            '.child', BBox(0, 0, 50, 50), BBox(0, 0, 100, 100),
            prev_bbox=None, flex_kind='row', child_position='static')
        assert css['.child']['position'] == 'relative'


# ===========================================================================
# _apply_flex_to_existing_container 测试
# ===========================================================================

class TestApplyFlexToExistingContainer:
    def test_row_flex(self):
        html = '<div class="group-1 layer-group"></div>'
        soup = _make_soup(html)
        css = {'.group-1': {'position': 'absolute', 'width': '300px', 'height': '200px'}}
        dr = _make_dr(soup, css)
        group = soup.find('div', class_='layer-group')
        tree = LayoutNode(kind='row', bbox=BBox(0, 0, 300, 200), children=[])
        dr._apply_flex_to_existing_container(group, tree)
        assert css['.group-1']['display'] == 'flex'
        assert css['.group-1']['flex-direction'] == 'row'
        assert 'v-row' in group.get('class', [])

    def test_col_flex(self):
        html = '<div class="group-1 layer-group"></div>'
        soup = _make_soup(html)
        css = {'.group-1': {'position': 'absolute', 'width': '300px', 'height': '200px'}}
        dr = _make_dr(soup, css)
        group = soup.find('div', class_='layer-group')
        tree = LayoutNode(kind='col', bbox=BBox(0, 0, 300, 200), children=[])
        dr._apply_flex_to_existing_container(group, tree)
        assert css['.group-1']['display'] == 'flex'
        assert css['.group-1']['flex-direction'] == 'column'
        assert 'v-col' in group.get('class', [])

    def test_padding_for_offset(self):
        """envelope 不从原点开始 → 写 padding"""
        html = '<div class="group-1 layer-group"></div>'
        soup = _make_soup(html)
        css = {'.group-1': {'position': 'absolute', 'width': '300px', 'height': '200px'}}
        dr = _make_dr(soup, css)
        group = soup.find('div', class_='layer-group')
        tree = LayoutNode(kind='row', bbox=BBox(20, 15, 300, 200), children=[])
        dr._apply_flex_to_existing_container(group, tree)
        assert css['.group-1']['padding-left'] == '20px'
        assert css['.group-1']['padding-top'] == '15px'
        assert css['.group-1']['box-sizing'] == 'border-box'


# ===========================================================================
# _apply_stack_to_existing_container 测试
# ===========================================================================

class TestApplyStackToExistingContainer:
    def test_adds_relative(self):
        """无 position → 加 relative"""
        html = '<div class="group-1 layer-group"></div>'
        soup = _make_soup(html)
        css = {'.group-1': {'width': '300px', 'height': '200px'}}
        dr = _make_dr(soup, css)
        group = soup.find('div', class_='layer-group')
        tree = LayoutNode(kind='stack', bbox=BBox(0, 0, 300, 200), children=[])
        dr._apply_stack_to_existing_container(group, tree)
        assert css['.group-1']['position'] == 'relative'
        assert 'v-stack' in group.get('class', [])

    def test_keeps_absolute(self):
        """已有 absolute → 保持不变"""
        html = '<div class="group-1 layer-group"></div>'
        soup = _make_soup(html)
        css = {'.group-1': {'position': 'absolute', 'width': '300px', 'height': '200px'}}
        dr = _make_dr(soup, css)
        group = soup.find('div', class_='layer-group')
        tree = LayoutNode(kind='stack', bbox=BBox(0, 0, 300, 200), children=[])
        dr._apply_stack_to_existing_container(group, tree)
        assert css['.group-1']['position'] == 'absolute'


# ===========================================================================
# _parse_position_px / _parse_size_px 测试
# ===========================================================================

class TestParsePositionPx:
    def test_left_top(self):
        assert DOMRestructure._parse_position_px('left top') == (0, 0)

    def test_zero_zero(self):
        assert DOMRestructure._parse_position_px('0 0') == (0, 0)

    def test_px_values(self):
        assert DOMRestructure._parse_position_px('10px 20px') == (10, 20)

    def test_negative(self):
        assert DOMRestructure._parse_position_px('-5px -10px') == (-5, -10)

    def test_invalid(self):
        assert DOMRestructure._parse_position_px('center center') is None

    def test_float_values(self):
        assert DOMRestructure._parse_position_px('10.6px 20.4px') == (11, 20)


class TestParseSizePx:
    def test_px_values(self):
        assert DOMRestructure._parse_size_px('100px 200px') == (100, 200)

    def test_100_percent(self):
        assert DOMRestructure._parse_size_px('100% 100%') is None

    def test_auto(self):
        assert DOMRestructure._parse_size_px('auto') is None

    def test_cover(self):
        assert DOMRestructure._parse_size_px('cover') is None

    def test_float_values(self):
        assert DOMRestructure._parse_size_px('99.6px 50.4px') == (100, 50)

    def test_invalid(self):
        assert DOMRestructure._parse_size_px('50% auto') is None


# ===========================================================================
# restructure_dom() 端到端集成测试
# ===========================================================================

class TestRestructureDomEndToEnd:
    def test_horizontal_row(self):
        """3 个水平排列的 image → group 变为 flex-row"""
        specs = [
            {'cls': 'a', 'left': 0, 'top': 0, 'width': 80, 'height': 50},
            {'cls': 'b', 'left': 100, 'top': 5, 'width': 80, 'height': 45},
            {'cls': 'c', 'left': 200, 'top': 3, 'width': 80, 'height': 48},
        ]
        html = _build_group_html([{'cls': s['cls']} for s in specs])
        soup = _make_soup(html)
        css = _build_css_for_leaves(specs)
        css['.group-1'] = {
            'position': 'absolute', 'width': '280px', 'height': '53px',
        }
        dr = _make_dr(soup, css)
        dr.restructure_dom()
        # group 应变为 flex-row
        assert css['.group-1']['display'] == 'flex'
        assert css['.group-1']['flex-direction'] == 'row'

    def test_vertical_col(self):
        """5 个垂直排列的 image → group 变为 flex-column"""
        specs = [
            {'cls': 'a', 'left': 0, 'top': 0, 'width': 100, 'height': 30},
            {'cls': 'b', 'left': 5, 'top': 80, 'width': 90, 'height': 30},
            {'cls': 'c', 'left': 3, 'top': 160, 'width': 95, 'height': 30},
            {'cls': 'd', 'left': 2, 'top': 240, 'width': 98, 'height': 30},
            {'cls': 'e', 'left': 4, 'top': 320, 'width': 92, 'height': 30},
        ]
        html = _build_group_html([{'cls': s['cls']} for s in specs])
        soup = _make_soup(html)
        css = _build_css_for_leaves(specs)
        css['.group-1'] = {
            'position': 'absolute', 'width': '100px', 'height': '350px',
        }
        dr = _make_dr(soup, css)
        dr.restructure_dom()
        assert css['.group-1']['display'] == 'flex'
        assert css['.group-1']['flex-direction'] == 'column'

    def test_stack_group_preserved(self):
        """全重叠 → 保持 absolute（不变为 flex）"""
        specs = [
            {'cls': 'a', 'left': 0, 'top': 0, 'width': 100, 'height': 100},
            {'cls': 'b', 'left': 5, 'top': 5, 'width': 90, 'height': 90},
            {'cls': 'c', 'left': 10, 'top': 10, 'width': 80, 'height': 80},
        ]
        html = _build_group_html([{'cls': s['cls']} for s in specs])
        soup = _make_soup(html)
        css = _build_css_for_leaves(specs)
        css['.group-1'] = {
            'position': 'absolute', 'width': '100px', 'height': '100px',
        }
        dr = _make_dr(soup, css)
        dr.restructure_dom()
        # 不应有 display:flex
        assert 'display' not in css['.group-1'] or css['.group-1'].get('display') != 'flex'

    def test_too_few_leaves_skipped(self):
        """只有 1 个 leaf → 跳过不处理"""
        specs = [
            {'cls': 'a', 'left': 0, 'top': 0, 'width': 100, 'height': 50},
        ]
        html = _build_group_html([{'cls': s['cls']} for s in specs])
        soup = _make_soup(html)
        css = _build_css_for_leaves(specs)
        css['.group-1'] = {
            'position': 'absolute', 'width': '100px', 'height': '50px',
        }
        dr = _make_dr(soup, css)
        dr.restructure_dom()
        assert 'display' not in css['.group-1']

    def test_grid_layout_produces_col_of_rows(self):
        """2行×2列网格 → col 包含 2 个 row"""
        specs = [
            {'cls': 'a', 'left': 0, 'top': 0, 'width': 80, 'height': 50},
            {'cls': 'b', 'left': 150, 'top': 5, 'width': 80, 'height': 45},
            {'cls': 'c', 'left': 0, 'top': 100, 'width': 80, 'height': 50},
            {'cls': 'd', 'left': 150, 'top': 105, 'width': 80, 'height': 45},
        ]
        html = _build_group_html([{'cls': s['cls']} for s in specs])
        soup = _make_soup(html)
        css = _build_css_for_leaves(specs)
        css['.group-1'] = {
            'position': 'absolute', 'width': '230px', 'height': '155px',
        }
        dr = _make_dr(soup, css)
        dr.restructure_dom()
        # group 是 col
        assert css['.group-1']['flex-direction'] == 'column'
        # 应有 2 个虚拟 wrapper
        wrappers = soup.find_all('div', attrs={'data-virtual': True})
        assert len(wrappers) >= 2

    def test_bg_absorption_in_restructure(self):
        """大底图 + 前景行 → 底图被吸收为 group background-image"""
        specs = [
            {'cls': 'bg', 'left': 0, 'top': 0, 'width': 300, 'height': 200,
             'data_type': 'image',
             'extras': {'background-image': 'url("images/bg.png")'}},
            {'cls': 'a', 'left': 10, 'top': 80, 'width': 80, 'height': 40},
            {'cls': 'b', 'left': 110, 'top': 85, 'width': 80, 'height': 35},
            {'cls': 'c', 'left': 210, 'top': 82, 'width': 80, 'height': 38},
        ]
        html = _build_group_html([
            {'cls': s['cls'], 'data_type': s.get('data_type', 'image')}
            for s in specs
        ])
        soup = _make_soup(html)
        css = _build_css_for_leaves(specs)
        css['.group-1'] = {
            'position': 'absolute', 'width': '300px', 'height': '200px',
        }
        dr = _make_dr(soup, css)
        dr.restructure_dom()
        # bg 应被吸收：group-1 应有 background-image
        assert 'background-image' in css['.group-1']
        # bg leaf 的 CSS 应被移除
        assert '.bg' not in css
        # group 应变为 flex-row（前景是 row 布局）
        assert css['.group-1']['display'] == 'flex'
        assert css['.group-1']['flex-direction'] == 'row'


# ===========================================================================
# _render_stack 测试（使用真实 DOM）
# ===========================================================================

class TestRenderStack:
    def test_children_get_absolute(self):
        """stack 子元素被设为 absolute + 相对 stack 原点的 left/top"""
        specs = [
            {'cls': 'a', 'left': 10, 'top': 20, 'width': 80, 'height': 80},
            {'cls': 'b', 'left': 15, 'top': 25, 'width': 70, 'height': 70},
        ]
        html = _build_group_html([{'cls': s['cls']} for s in specs])
        soup = _make_soup(html)
        css = _build_css_for_leaves(specs)
        css['.group-1'] = {'position': 'absolute', 'width': '100px', 'height': '100px'}
        dr = _make_dr(soup, css)
        dr.stats['dom_restructured'] = 0
        group = soup.find('div', class_='layer-group')
        leaves = dr._collect_leaves(group)
        # 构建 stack 节点
        node = LayoutNode(
            kind='stack',
            bbox=BBox(10, 20, 90, 100),
            children=[LayoutNode(kind='leaf', bbox=l.bbox, leaf=l) for l in leaves],
        )
        parent_origin = BBox(0, 0, 100, 100)
        wrapper = dr._render_stack(node, parent_origin)
        # 子元素应该都有 position:absolute
        assert css['.a']['position'] == 'absolute'
        assert css['.b']['position'] == 'absolute'
        # 坐标相对 stack 原点
        assert css['.a']['left'] == '0px'
        assert css['.a']['top'] == '0px'
        assert css['.b']['left'] == '5px'
        assert css['.b']['top'] == '5px'


# ===========================================================================
# _render_flex 测试（使用真实 DOM）
# ===========================================================================

class TestRenderFlex:
    def test_row_wrapper(self):
        """row wrapper 子元素有正确 margin"""
        specs = [
            {'cls': 'a', 'left': 0, 'top': 0, 'width': 80, 'height': 50},
            {'cls': 'b', 'left': 100, 'top': 5, 'width': 80, 'height': 45},
        ]
        html = _build_group_html([{'cls': s['cls']} for s in specs])
        soup = _make_soup(html)
        css = _build_css_for_leaves(specs)
        css['.group-1'] = {'position': 'absolute', 'width': '180px', 'height': '55px'}
        dr = _make_dr(soup, css)
        dr.stats['dom_restructured'] = 0
        group = soup.find('div', class_='layer-group')
        leaves = dr._collect_leaves(group)
        node = LayoutNode(
            kind='row',
            bbox=BBox(0, 0, 180, 50),
            children=[LayoutNode(kind='leaf', bbox=l.bbox, leaf=l) for l in leaves],
        )
        parent_origin = BBox(0, 0, 200, 100)
        wrapper = dr._render_flex(node, parent_origin)
        # 第一个子：margin-left=0(不写), margin-top=0(不写)
        assert 'margin-left' not in css['.a'] or css['.a'].get('margin-left') == '0px'
        # 第二个子：margin-left = 100 - 80 = 20
        assert css['.b'].get('margin-left') == '20px'
        # wrapper 有 display:flex
        wrapper_cls = wrapper.get('class', [])
        assert any('v-row' in c for c in wrapper_cls)


# ===========================================================================
# _collect_leaves 测试
# ===========================================================================

class TestCollectLeaves:
    def test_collects_direct_children(self):
        specs = [
            {'cls': 'a', 'left': 10, 'top': 20, 'width': 50, 'height': 50},
            {'cls': 'b', 'left': 70, 'top': 20, 'width': 50, 'height': 50},
        ]
        html = _build_group_html([{'cls': s['cls']} for s in specs])
        soup = _make_soup(html)
        css = _build_css_for_leaves(specs)
        dr = _make_dr(soup, css)
        group = soup.find('div', class_='layer-group')
        leaves = dr._collect_leaves(group)
        assert len(leaves) == 2
        assert leaves[0].name == 'a'
        assert leaves[1].name == 'b'
        assert leaves[0].bbox.left == 10

    def test_skips_missing_css(self):
        """没有 CSS 的子元素被跳过"""
        html = _build_group_html([{'cls': 'a'}, {'cls': 'b'}])
        soup = _make_soup(html)
        css = {'.a': {'left': '0px', 'top': '0px', 'width': '50px', 'height': '50px'}}
        dr = _make_dr(soup, css)
        group = soup.find('div', class_='layer-group')
        leaves = dr._collect_leaves(group)
        assert len(leaves) == 1
        assert leaves[0].name == 'a'


# ===========================================================================
# _make_wrapper_div / _write_wrapper_css 测试
# ===========================================================================

class TestMakeWrapperDiv:
    def test_creates_div_with_classes(self):
        soup = _make_soup('<div></div>')
        css = {}
        dr = _make_dr(soup, css)
        wrapper = dr._make_wrapper_div('row')
        assert wrapper.name == 'div'
        classes = wrapper.get('class', [])
        assert 'v-row-1' in classes
        assert 'v-row' in classes
        assert wrapper.get('data-virtual') == 'row'
        assert '.v-row-1' in dr.css_rules

    def test_write_wrapper_css_row(self):
        soup = _make_soup('<div></div>')
        css = {}
        dr = _make_dr(soup, css)
        wrapper = dr._make_wrapper_div('row')
        dr._write_wrapper_css(
            wrapper,
            self_bbox=BBox(10, 20, 210, 120),
            parent_origin=BBox(0, 0, 300, 200),
            flex_kind='row',
        )
        cls = f'.{wrapper.get("class")[0]}'
        assert dr.css_rules[cls]['width'] == '200px'
        assert dr.css_rules[cls]['height'] == '100px'
        assert dr.css_rules[cls]['display'] == 'flex'
        assert dr.css_rules[cls]['flex-direction'] == 'row'

    def test_write_wrapper_css_stack(self):
        soup = _make_soup('<div></div>')
        css = {}
        dr = _make_dr(soup, css)
        wrapper = dr._make_wrapper_div('stack')
        dr._write_wrapper_css(
            wrapper,
            self_bbox=BBox(0, 0, 100, 100),
            parent_origin=BBox(0, 0, 100, 100),
            flex_kind=None,
        )
        cls = f'.{wrapper.get("class")[0]}'
        assert dr.css_rules[cls]['position'] == 'relative'
        assert 'display' not in dr.css_rules[cls]


# ===========================================================================
# _has_blend_mode_descendant 测试
# ===========================================================================

class TestHasBlendModeDescendant:
    def test_no_blend(self):
        html = '<div class="parent"><div class="child"></div></div>'
        soup = _make_soup(html)
        css = {'.child': {'color': 'red'}}
        dr = _make_dr(soup, css)
        parent = soup.find('div', class_='parent')
        assert dr._has_blend_mode_descendant(parent) is False

    def test_with_blend(self):
        html = '<div class="parent"><div class="child"></div></div>'
        soup = _make_soup(html)
        css = {'.child': {'mix-blend-mode': 'multiply'}}
        dr = _make_dr(soup, css)
        parent = soup.find('div', class_='parent')
        assert dr._has_blend_mode_descendant(parent) is True

    def test_normal_blend_is_false(self):
        html = '<div class="parent"><div class="child"></div></div>'
        soup = _make_soup(html)
        css = {'.child': {'mix-blend-mode': 'normal'}}
        dr = _make_dr(soup, css)
        parent = soup.find('div', class_='parent')
        assert dr._has_blend_mode_descendant(parent) is False


# ===========================================================================
# _sibling_index_in_dom 测试
# ===========================================================================

class TestSiblingIndexInDom:
    def test_returns_index(self):
        html = '<div class="parent"><div class="a"></div><div class="b"></div><div class="c"></div></div>'
        soup = _make_soup(html)
        parent = soup.find('div', class_='parent')
        children = list(parent.find_all(recursive=False))
        leaf_a = LeafInfo(children[0], '.a', 'a', 'image', BBox(0, 0, 10, 10))
        leaf_b = LeafInfo(children[1], '.b', 'b', 'image', BBox(0, 0, 10, 10))
        leaf_c = LeafInfo(children[2], '.c', 'c', 'image', BBox(0, 0, 10, 10))
        assert DOMRestructure._sibling_index_in_dom(leaf_a) == 0
        assert DOMRestructure._sibling_index_in_dom(leaf_b) == 1
        assert DOMRestructure._sibling_index_in_dom(leaf_c) == 2


# ===========================================================================
# _container_css_bbox 测试
# ===========================================================================

class TestContainerCssBbox:
    def test_from_css(self):
        html = '<div class="group-1 layer-group"></div>'
        soup = _make_soup(html)
        css = {'.group-1': {'width': '200px', 'height': '100px'}}
        dr = _make_dr(soup, css)
        group = soup.find('div', class_='layer-group')
        bbox = dr._container_css_bbox(group, fallback=BBox(5, 5, 195, 95))
        assert bbox.left == 0
        assert bbox.top == 0
        assert bbox.right == 200
        assert bbox.bottom == 100

    def test_fallback(self):
        html = '<div class="group-1 layer-group"></div>'
        soup = _make_soup(html)
        css = {'.group-1': {}}  # 无 width/height
        dr = _make_dr(soup, css)
        group = soup.find('div', class_='layer-group')
        fallback = BBox(5, 5, 195, 95)
        bbox = dr._container_css_bbox(group, fallback=fallback)
        assert bbox is fallback


# ===========================================================================
# _cluster 综合测试
# ===========================================================================

class TestCluster:
    def _setup_dr(self, specs):
        html = _build_group_html([{'cls': s['cls'], 'data_type': s.get('data_type', 'image')} for s in specs])
        soup = _make_soup(html)
        css = _build_css_for_leaves(specs)
        dr = _make_dr(soup, css)
        group = soup.find('div', class_='layer-group')
        leaves = dr._collect_leaves(group)
        return dr, leaves

    def test_nested_col_of_rows(self):
        """多行多列 → col(row, row)"""
        specs = [
            {'cls': 'a', 'left': 0, 'top': 0, 'width': 50, 'height': 30},
            {'cls': 'b', 'left': 100, 'top': 5, 'width': 50, 'height': 25},
            {'cls': 'c', 'left': 0, 'top': 100, 'width': 50, 'height': 30},
            {'cls': 'd', 'left': 100, 'top': 105, 'width': 50, 'height': 25},
        ]
        dr, leaves = self._setup_dr(specs)
        tree = dr._cluster(leaves)
        assert tree.kind == 'col'
        assert all(c.kind == 'row' for c in tree.children)

    def test_single_row_multiple_cols(self):
        """单行多列 → row"""
        specs = [
            {'cls': 'a', 'left': 0, 'top': 0, 'width': 50, 'height': 50},
            {'cls': 'b', 'left': 100, 'top': 0, 'width': 50, 'height': 50},
            {'cls': 'c', 'left': 200, 'top': 0, 'width': 50, 'height': 50},
        ]
        dr, leaves = self._setup_dr(specs)
        tree = dr._cluster(leaves)
        assert tree.kind == 'row'
        assert len(tree.children) == 3

    def test_fake_multirow_fallback(self):
        """2 个上下贴边的同宽元素 → stack（fake multirow 回退）"""
        specs = [
            {'cls': 'a', 'left': 0, 'top': 0, 'width': 100, 'height': 50},
            {'cls': 'b', 'left': 0, 'top': 55, 'width': 100, 'height': 50},
        ]
        dr, leaves = self._setup_dr(specs)
        # 确保 row_dominant_overlap_ratio 让它们切成 2 行
        dr.config.row_dominant_overlap_ratio = 0.5
        tree = dr._cluster(leaves)
        # 应该回退为 stack（fake multirow：2 行各 1 元素 + X 100% 对齐）
        assert tree.kind == 'stack'
