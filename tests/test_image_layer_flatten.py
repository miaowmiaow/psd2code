"""ImageLayerFlatten transformer 单元测试

覆盖范围：
- FlattenConfig 默认值与自定义
- _can_flatten_container（阻断属性 / overflow / opacity / blend）
- _parse_container_background（无 bg / 单 URL / 多 URL / 非法 size 等）
- _parse_image_child 全分支
- _envelope 几何计算
- _bbox_distance L∞ 距离
- _are_neighbors_connected 邻接连通
- _can_expand_container 护栏
- _flex_parent_axis 判定
- _expand_flex_parent 父扩大
- _read_canvas_area
- _parse_url_to_local_png
- _parse_px / _parse_int / _parse_two_px / _read_png_size
- run() 短路路径（disabled / no images_dir / no canvas）
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path
from bs4 import BeautifulSoup

from targets.html.postprocess.layout_optimizer.transformers.image_layer_flatten import (
    ImageLayerFlatten,
    FlattenConfig,
    _ImageChild,
    _ContainerBg,
    _PARENT_BLOCKING_PROPS,
    _PARENT_BLOCKING_OVERFLOW_VALUES,
    _URL_RE,
)


# ===========================================================================
# Helpers
# ===========================================================================

def _make_flatten(
    html='<div id="canvas"></div>',
    css_rules=None,
    images_dir=None,
    config=None,
):
    soup = BeautifulSoup(html, 'html.parser')
    return ImageLayerFlatten(
        soup=soup,
        css_rules=css_rules or {},
        stats={},
        images_dir=images_dir,
        config=config,
    )


def _child(left=0, top=0, width=100, height=100, z=0):
    return _ImageChild(
        element=MagicMock(),
        css_class='.img',
        class_name='img',
        left=left,
        top=top,
        width=width,
        height=height,
        png_path=Path('/tmp/test.png'),
        z_index=z,
    )


# ===========================================================================
# FlattenConfig
# ===========================================================================

class TestFlattenConfig:
    def test_default_disabled(self):
        cfg = FlattenConfig()
        assert cfg.enabled is False

    def test_default_values(self):
        cfg = FlattenConfig()
        assert cfg.min_total_layers == 2
        assert cfg.max_area_ratio == 0.5
        assert cfg.max_neighbor_gap_px == 10
        assert cfg.max_canvas_px == 8192

    def test_custom_values(self):
        cfg = FlattenConfig(enabled=True, min_total_layers=3, max_area_ratio=0.8)
        assert cfg.enabled is True
        assert cfg.min_total_layers == 3
        assert cfg.max_area_ratio == 0.8


# ===========================================================================
# _can_flatten_container
# ===========================================================================

class TestCanFlattenContainer:
    def _inst(self):
        return _make_flatten()

    def test_empty_styles_allowed(self):
        inst = self._inst()
        assert inst._can_flatten_container({}) is True

    def test_blocking_border_radius(self):
        inst = self._inst()
        assert inst._can_flatten_container({'border-radius': '10px'}) is False

    def test_blocking_border_radius_none_allowed(self):
        inst = self._inst()
        assert inst._can_flatten_container({'border-radius': 'none'}) is True

    def test_blocking_border_radius_zero_allowed(self):
        inst = self._inst()
        assert inst._can_flatten_container({'border-radius': '0'}) is True
        assert inst._can_flatten_container({'border-radius': '0px'}) is True

    def test_blocking_box_shadow(self):
        inst = self._inst()
        assert inst._can_flatten_container({'box-shadow': '0 2px 4px rgba(0,0,0,0.1)'}) is False

    def test_blocking_filter(self):
        inst = self._inst()
        assert inst._can_flatten_container({'filter': 'blur(4px)'}) is False

    def test_blocking_transform(self):
        inst = self._inst()
        assert inst._can_flatten_container({'transform': 'rotate(5deg)'}) is False

    def test_blocking_clip_path(self):
        inst = self._inst()
        assert inst._can_flatten_container({'clip-path': 'circle(50%)'}) is False

    def test_blocking_mask(self):
        inst = self._inst()
        assert inst._can_flatten_container({'mask': 'url(#m)'}) is False
        assert inst._can_flatten_container({'mask-image': 'url(m.png)'}) is False

    def test_overflow_hidden_blocks(self):
        inst = self._inst()
        assert inst._can_flatten_container({'overflow': 'hidden'}) is False
        assert inst._can_flatten_container({'overflow': 'clip'}) is False
        assert inst._can_flatten_container({'overflow': 'scroll'}) is False
        assert inst._can_flatten_container({'overflow': 'auto'}) is False

    def test_overflow_visible_allowed(self):
        inst = self._inst()
        assert inst._can_flatten_container({'overflow': 'visible'}) is True

    def test_opacity_not_one_blocks(self):
        inst = self._inst()
        assert inst._can_flatten_container({'opacity': '0.5'}) is False

    def test_opacity_one_allowed(self):
        inst = self._inst()
        assert inst._can_flatten_container({'opacity': '1'}) is True
        assert inst._can_flatten_container({'opacity': '1.0'}) is True

    def test_opacity_invalid_blocks(self):
        inst = self._inst()
        assert inst._can_flatten_container({'opacity': 'abc'}) is False

    def test_blend_mode_blocks(self):
        inst = self._inst()
        assert inst._can_flatten_container({'mix-blend-mode': 'multiply'}) is False

    def test_blend_mode_normal_allowed(self):
        inst = self._inst()
        assert inst._can_flatten_container({'mix-blend-mode': 'normal'}) is True

    def test_all_blocking_props_recognized(self):
        """每个 _PARENT_BLOCKING_PROPS 成员的非中性值都应阻断"""
        inst = self._inst()
        for prop in _PARENT_BLOCKING_PROPS:
            assert inst._can_flatten_container({prop: '5px'}) is False, f"{prop} should block"


# ===========================================================================
# _parse_container_background
# ===========================================================================

class TestParseContainerBackground:
    def _inst(self, images_dir=None):
        return _make_flatten(images_dir=images_dir)

    def test_no_background_returns_none(self):
        inst = self._inst()
        result = inst._parse_container_background({'width': '100px', 'height': '50px'})
        assert result is None

    def test_empty_background_returns_none(self):
        inst = self._inst()
        result = inst._parse_container_background({
            'width': '100px', 'height': '50px', 'background-image': ''
        })
        assert result is None

    def test_multiple_urls_returns_invalid(self):
        inst = self._inst()
        result = inst._parse_container_background({
            'width': '100px', 'height': '50px',
            'background-image': 'url("a.png"), url("b.png")'
        })
        assert result == 'invalid'

    def test_cover_size_returns_invalid(self):
        inst = self._inst(images_dir=Path('/tmp/images'))
        # mock _parse_url_to_local_png
        inst._parse_url_to_local_png = lambda v: Path('/tmp/images/x.png')
        result = inst._parse_container_background({
            'width': '100px', 'height': '50px',
            'background-image': 'url("images/x.png")',
            'background-size': 'cover',
        })
        assert result == 'invalid'

    def test_contain_size_returns_invalid(self):
        inst = self._inst(images_dir=Path('/tmp/images'))
        inst._parse_url_to_local_png = lambda v: Path('/tmp/images/x.png')
        result = inst._parse_container_background({
            'width': '100px', 'height': '50px',
            'background-image': 'url("images/x.png")',
            'background-size': 'contain',
        })
        assert result == 'invalid'

    def test_no_width_height_returns_invalid(self):
        inst = self._inst(images_dir=Path('/tmp/images'))
        inst._parse_url_to_local_png = lambda v: Path('/tmp/images/x.png')
        result = inst._parse_container_background({
            'background-image': 'url("images/x.png")',
        })
        assert result == 'invalid'

    def test_repeat_not_no_repeat_returns_invalid(self):
        inst = self._inst(images_dir=Path('/tmp/images'))
        inst._parse_url_to_local_png = lambda v: Path('/tmp/images/x.png')
        inst._read_png_size = lambda p: (100, 50)
        result = inst._parse_container_background({
            'width': '100px', 'height': '50px',
            'background-image': 'url("images/x.png")',
            'background-repeat': 'repeat',
        })
        assert result == 'invalid'

    def test_valid_container_bg(self):
        inst = self._inst(images_dir=Path('/tmp/images'))
        inst._parse_url_to_local_png = lambda v: Path('/tmp/images/x.png')
        inst._read_png_size = lambda p: (100, 50)
        result = inst._parse_container_background({
            'width': '100px', 'height': '50px',
            'background-image': 'url("images/x.png")',
            'background-repeat': 'no-repeat',
            'background-position': '0 0',
        })
        assert isinstance(result, _ContainerBg)
        assert result.png_path == Path('/tmp/images/x.png')
        assert result.pos_x == 0
        assert result.pos_y == 0
        assert result.size_w == 100
        assert result.size_h == 50

    def test_explicit_position_px(self):
        inst = self._inst(images_dir=Path('/tmp/images'))
        inst._parse_url_to_local_png = lambda v: Path('/tmp/images/x.png')
        inst._read_png_size = lambda p: (80, 60)
        result = inst._parse_container_background({
            'width': '100px', 'height': '50px',
            'background-image': 'url("images/x.png")',
            'background-position': '10px 20px',
            'background-size': '80px 60px',
        })
        assert isinstance(result, _ContainerBg)
        assert result.pos_x == 10
        assert result.pos_y == 20
        assert result.size_w == 80
        assert result.size_h == 60


# ===========================================================================
# _envelope
# ===========================================================================

class TestEnvelope:
    def test_single_child_no_bg(self):
        children = [_child(left=10, top=20, width=50, height=30)]
        w, h, ox, oy = ImageLayerFlatten._envelope(children, None)
        assert (w, h, ox, oy) == (50, 30, 10, 20)

    def test_multiple_children(self):
        children = [
            _child(left=0, top=0, width=50, height=50),
            _child(left=30, top=40, width=20, height=20),
        ]
        w, h, ox, oy = ImageLayerFlatten._envelope(children, None)
        # envelope: x[0..50], y[0..60]
        assert (w, h, ox, oy) == (50, 60, 0, 0)

    def test_with_container_bg(self):
        children = [_child(left=10, top=10, width=20, height=20)]
        bg = _ContainerBg(png_path=Path('/x.png'), pos_x=0, pos_y=0, size_w=50, size_h=50)
        w, h, ox, oy = ImageLayerFlatten._envelope(children, bg)
        # envelope: x[0..50], y[0..50] (bg covers more)
        assert (w, h) == (50, 50)
        assert (ox, oy) == (0, 0)

    def test_empty_children_no_bg(self):
        w, h, ox, oy = ImageLayerFlatten._envelope([], None)
        assert (w, h, ox, oy) == (0, 0, 0, 0)

    def test_negative_origin(self):
        children = [_child(left=-10, top=-5, width=50, height=30)]
        w, h, ox, oy = ImageLayerFlatten._envelope(children, None)
        assert (w, h, ox, oy) == (50, 30, -10, -5)


# ===========================================================================
# _bbox_distance
# ===========================================================================

class TestBboxDistance:
    def test_overlapping(self):
        a = _child(left=0, top=0, width=50, height=50)
        b = _child(left=30, top=30, width=50, height=50)
        assert ImageLayerFlatten._bbox_distance(a, b) == 0

    def test_adjacent_touching(self):
        a = _child(left=0, top=0, width=50, height=50)
        b = _child(left=50, top=0, width=50, height=50)
        assert ImageLayerFlatten._bbox_distance(a, b) == 0

    def test_gap_horizontal(self):
        a = _child(left=0, top=0, width=50, height=50)
        b = _child(left=60, top=0, width=50, height=50)
        assert ImageLayerFlatten._bbox_distance(a, b) == 10

    def test_gap_vertical(self):
        a = _child(left=0, top=0, width=50, height=50)
        b = _child(left=0, top=70, width=50, height=50)
        assert ImageLayerFlatten._bbox_distance(a, b) == 20

    def test_gap_diagonal(self):
        a = _child(left=0, top=0, width=10, height=10)
        b = _child(left=20, top=30, width=10, height=10)
        # dx = max(0, max(0,20) - min(10,30)) = max(0, 20-10)=10
        # dy = max(0, max(0,30) - min(10,40)) = max(0, 30-10)=20
        # L∞ = max(10,20) = 20
        assert ImageLayerFlatten._bbox_distance(a, b) == 20


# ===========================================================================
# _are_neighbors_connected
# ===========================================================================

class TestAreNeighborsConnected:
    def _inst(self, gap_px=10):
        return _make_flatten(config=FlattenConfig(max_neighbor_gap_px=gap_px))

    def test_single_child_always_connected(self):
        inst = self._inst()
        assert inst._are_neighbors_connected([_child()]) is True

    def test_two_overlapping_connected(self):
        inst = self._inst()
        children = [
            _child(left=0, top=0, width=50, height=50),
            _child(left=30, top=30, width=50, height=50),
        ]
        assert inst._are_neighbors_connected(children) is True

    def test_two_far_apart_disconnected(self):
        inst = self._inst(gap_px=5)
        children = [
            _child(left=0, top=0, width=10, height=10),
            _child(left=100, top=100, width=10, height=10),
        ]
        assert inst._are_neighbors_connected(children) is False

    def test_chain_connected(self):
        """A-B 连通, B-C 连通 → 整体连通"""
        inst = self._inst(gap_px=10)
        children = [
            _child(left=0, top=0, width=10, height=10),
            _child(left=15, top=0, width=10, height=10),   # gap=5 from A
            _child(left=30, top=0, width=10, height=10),   # gap=5 from B
        ]
        assert inst._are_neighbors_connected(children) is True

    def test_island_disconnected(self):
        """A-B 连通, C 孤立 → 不连通"""
        inst = self._inst(gap_px=5)
        children = [
            _child(left=0, top=0, width=10, height=10),
            _child(left=12, top=0, width=10, height=10),   # gap=2 from A
            _child(left=100, top=100, width=10, height=10),  # far away
        ]
        assert inst._are_neighbors_connected(children) is False


# ===========================================================================
# _can_expand_container
# ===========================================================================

class TestCanExpandContainer:
    def _inst(self):
        return _make_flatten()

    def test_plain_styles_allowed(self):
        inst = self._inst()
        assert inst._can_expand_container({'position': 'absolute', 'left': '10px'}) is True

    def test_flex_container_blocked(self):
        inst = self._inst()
        assert inst._can_expand_container({'display': 'flex'}) is False
        assert inst._can_expand_container({'display': 'inline-flex'}) is False
        assert inst._can_expand_container({'display': 'grid'}) is False
        assert inst._can_expand_container({'display': 'inline-grid'}) is False

    def test_right_position_blocked(self):
        inst = self._inst()
        assert inst._can_expand_container({'right': '0px'}) is False

    def test_bottom_position_blocked(self):
        inst = self._inst()
        assert inst._can_expand_container({'bottom': '10px'}) is False

    def test_flex_basis_non_auto_blocked(self):
        inst = self._inst()
        assert inst._can_expand_container({'flex-basis': '100px'}) is False

    def test_flex_basis_auto_allowed(self):
        inst = self._inst()
        assert inst._can_expand_container({'flex-basis': 'auto'}) is True

    def test_flex_basis_zero_allowed(self):
        inst = self._inst()
        assert inst._can_expand_container({'flex-basis': '0'}) is True
        assert inst._can_expand_container({'flex-basis': '0px'}) is True


# ===========================================================================
# _flex_parent_axis
# ===========================================================================

class TestFlexParentAxis:
    def test_no_parent(self):
        inst = _make_flatten()
        container = MagicMock()
        container.parent = None
        assert inst._flex_parent_axis(container) is None

    def test_parent_not_flex(self):
        inst = _make_flatten(css_rules={'.parent': {'display': 'block'}})
        parent = MagicMock()
        parent.get = lambda k, d=None: ['parent'] if k == 'class' else d
        container = MagicMock()
        container.parent = parent
        assert inst._flex_parent_axis(container) is None

    def test_parent_flex_row(self):
        inst = _make_flatten(css_rules={'.parent': {'display': 'flex', 'flex-direction': 'row'}})
        parent = MagicMock()
        parent.get = lambda k, d=None: ['parent'] if k == 'class' else d
        container = MagicMock()
        container.parent = parent
        assert inst._flex_parent_axis(container) == 'row'

    def test_parent_flex_column(self):
        inst = _make_flatten(css_rules={'.parent': {'display': 'flex', 'flex-direction': 'column'}})
        parent = MagicMock()
        parent.get = lambda k, d=None: ['parent'] if k == 'class' else d
        container = MagicMock()
        container.parent = parent
        assert inst._flex_parent_axis(container) == 'column'

    def test_parent_flex_default_row(self):
        """flex-direction 缺省时默认 row"""
        inst = _make_flatten(css_rules={'.parent': {'display': 'flex'}})
        parent = MagicMock()
        parent.get = lambda k, d=None: ['parent'] if k == 'class' else d
        container = MagicMock()
        container.parent = parent
        assert inst._flex_parent_axis(container) == 'row'

    def test_parent_grid(self):
        inst = _make_flatten(css_rules={'.parent': {'display': 'grid'}})
        parent = MagicMock()
        parent.get = lambda k, d=None: ['parent'] if k == 'class' else d
        container = MagicMock()
        container.parent = parent
        assert inst._flex_parent_axis(container) == 'grid'


# ===========================================================================
# _expand_flex_parent
# ===========================================================================

class TestExpandFlexParent:
    def test_grid_does_nothing(self):
        css = {'.parent': {'display': 'grid', 'width': '100px', 'height': '200px'}}
        inst = _make_flatten(css_rules=css)
        parent = MagicMock()
        parent.get = lambda k, d=None: ['parent'] if k == 'class' else d
        container = MagicMock()
        container.parent = parent
        inst._expand_flex_parent(container, 'grid', 10, 20)
        assert css['.parent']['width'] == '100px'
        assert css['.parent']['height'] == '200px'

    def test_column_expands_height(self):
        css = {'.parent': {'display': 'flex', 'flex-direction': 'column', 'height': '200px'}}
        inst = _make_flatten(css_rules=css)
        parent = MagicMock()
        parent.get = lambda k, d=None: ['parent'] if k == 'class' else d
        container = MagicMock()
        container.parent = parent
        inst._expand_flex_parent(container, 'column', 10, 30)
        assert css['.parent']['height'] == '230px'

    def test_row_expands_width(self):
        css = {'.parent': {'display': 'flex', 'width': '500px'}}
        inst = _make_flatten(css_rules=css)
        parent = MagicMock()
        parent.get = lambda k, d=None: ['parent'] if k == 'class' else d
        container = MagicMock()
        container.parent = parent
        inst._expand_flex_parent(container, 'row', 25, 10)
        assert css['.parent']['width'] == '525px'

    def test_no_parent_css_no_crash(self):
        inst = _make_flatten(css_rules={})
        container = MagicMock()
        container.parent = MagicMock()
        container.parent.get = lambda k, d=None: ['unknown'] if k == 'class' else d
        # Should not raise
        inst._expand_flex_parent(container, 'row', 10, 10)

    def test_auto_size_no_change(self):
        """父主轴尺寸 auto（无 height）→ 不干预"""
        css = {'.parent': {'display': 'flex', 'flex-direction': 'column'}}
        inst = _make_flatten(css_rules=css)
        parent = MagicMock()
        parent.get = lambda k, d=None: ['parent'] if k == 'class' else d
        container = MagicMock()
        container.parent = parent
        inst._expand_flex_parent(container, 'column', 10, 30)
        assert 'height' not in css['.parent']


# ===========================================================================
# _read_canvas_area
# ===========================================================================

class TestReadCanvasArea:
    def test_valid_canvas(self):
        inst = _make_flatten(css_rules={'#canvas': {'width': '750px', 'height': '1000px'}})
        assert inst._read_canvas_area() == 750000.0

    def test_missing_canvas(self):
        inst = _make_flatten(css_rules={})
        assert inst._read_canvas_area() is None

    def test_missing_width(self):
        inst = _make_flatten(css_rules={'#canvas': {'height': '1000px'}})
        assert inst._read_canvas_area() is None


# ===========================================================================
# _parse_px / _parse_int / _parse_two_px
# ===========================================================================

class TestParsers:
    def test_parse_px_normal(self):
        assert ImageLayerFlatten._parse_px('100px') == 100.0

    def test_parse_px_no_unit(self):
        assert ImageLayerFlatten._parse_px('50') == 50.0

    def test_parse_px_none(self):
        assert ImageLayerFlatten._parse_px(None) is None

    def test_parse_px_empty(self):
        assert ImageLayerFlatten._parse_px('') is None

    def test_parse_px_invalid(self):
        assert ImageLayerFlatten._parse_px('abc') is None

    def test_parse_int_normal(self):
        assert ImageLayerFlatten._parse_int('5') == 5

    def test_parse_int_none(self):
        assert ImageLayerFlatten._parse_int(None) is None

    def test_parse_two_px_normal(self):
        assert ImageLayerFlatten._parse_two_px('10px 20px') == (10, 20)

    def test_parse_two_px_no_unit(self):
        assert ImageLayerFlatten._parse_two_px('5 15') == (5, 15)

    def test_parse_two_px_single(self):
        assert ImageLayerFlatten._parse_two_px('10px') is None

    def test_parse_two_px_three(self):
        assert ImageLayerFlatten._parse_two_px('1 2 3') is None


# ===========================================================================
# _URL_RE
# ===========================================================================

class TestURLRegex:
    def test_double_quotes(self):
        m = _URL_RE.match('url("images/test.png")')
        assert m is not None
        assert m.group(1) == 'images/test.png'

    def test_single_quotes(self):
        m = _URL_RE.match("url('images/test.png')")
        assert m is not None
        assert m.group(2) == 'images/test.png'

    def test_no_quotes(self):
        m = _URL_RE.match('url(images/test.png)')
        assert m is not None
        assert m.group(3) == 'images/test.png'

    def test_spaces(self):
        m = _URL_RE.match('  url( "images/test.png" )  ')
        assert m is not None
        assert m.group(1) == 'images/test.png'


# ===========================================================================
# run() 短路测试
# ===========================================================================

class TestRunShortCircuit:
    def test_disabled_config(self):
        inst = _make_flatten(config=FlattenConfig(enabled=False))
        inst.run()  # should not raise

    def test_no_images_dir(self):
        inst = _make_flatten(config=FlattenConfig(enabled=True), images_dir=None)
        inst.run()

    def test_nonexistent_images_dir(self):
        inst = _make_flatten(
            config=FlattenConfig(enabled=True),
            images_dir=Path('/nonexistent_dir_xxx'),
        )
        inst.run()

    def test_no_canvas_in_css(self):
        """images_dir 存在但 CSS 中无 #canvas → 跳过"""
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            inst = _make_flatten(
                config=FlattenConfig(enabled=True),
                images_dir=Path(td),
                css_rules={},
            )
            inst.run()
            assert inst.stats['image_layer_containers_flattened'] == 0


# ===========================================================================
# _parse_image_child 测试
# ===========================================================================

class TestParseImageChild:
    def _inst(self, css_rules=None):
        return _make_flatten(
            images_dir=Path('/tmp/images'),
            css_rules=css_rules or {},
        )

    def _make_child_tag(self, classes=None, data_type='image', inner_div=False):
        cls_str = f' class="{" ".join(classes)}"' if classes else ''
        dt_str = f' data-type="{data_type}"' if data_type else ''
        inner = '<div></div>' if inner_div else ''
        html = f'<div{cls_str}{dt_str}>{inner}</div>'
        return BeautifulSoup(html, 'html.parser').find('div')

    def test_not_image_type(self):
        inst = self._inst()
        tag = self._make_child_tag(classes=['c1'], data_type='text')
        assert inst._parse_image_child(tag) is None

    def test_no_data_type(self):
        inst = self._inst()
        tag = self._make_child_tag(classes=['c1'], data_type=None)
        assert inst._parse_image_child(tag) is None

    def test_has_inner_div(self):
        inst = self._inst(css_rules={'.c1': {'position': 'absolute'}})
        tag = self._make_child_tag(classes=['c1'], inner_div=True)
        assert inst._parse_image_child(tag) is None

    def test_no_classes(self):
        inst = self._inst()
        tag = self._make_child_tag(classes=None)
        assert inst._parse_image_child(tag) is None

    def test_no_css_rule(self):
        inst = self._inst(css_rules={})
        tag = self._make_child_tag(classes=['c1'])
        assert inst._parse_image_child(tag) is None

    def test_not_absolute_position(self):
        inst = self._inst(css_rules={'.c1': {
            'position': 'relative', 'left': '0px', 'top': '0px',
            'width': '100px', 'height': '100px',
            'background-image': 'url("images/x.png")',
        }})
        tag = self._make_child_tag(classes=['c1'])
        assert inst._parse_image_child(tag) is None

    def test_missing_dimensions(self):
        inst = self._inst(css_rules={'.c1': {
            'position': 'absolute', 'left': '0px', 'top': '0px',
            'background-image': 'url("images/x.png")',
        }})
        tag = self._make_child_tag(classes=['c1'])
        assert inst._parse_image_child(tag) is None

    def test_zero_width(self):
        inst = self._inst(css_rules={'.c1': {
            'position': 'absolute', 'left': '0px', 'top': '0px',
            'width': '0px', 'height': '100px',
            'background-image': 'url("images/x.png")',
        }})
        tag = self._make_child_tag(classes=['c1'])
        assert inst._parse_image_child(tag) is None

    def test_no_background_image(self):
        inst = self._inst(css_rules={'.c1': {
            'position': 'absolute', 'left': '0px', 'top': '0px',
            'width': '100px', 'height': '100px',
        }})
        tag = self._make_child_tag(classes=['c1'])
        assert inst._parse_image_child(tag) is None

    def test_multiple_urls_in_bg(self):
        inst = self._inst(css_rules={'.c1': {
            'position': 'absolute', 'left': '0px', 'top': '0px',
            'width': '100px', 'height': '100px',
            'background-image': 'url("a.png"), url("b.png")',
        }})
        tag = self._make_child_tag(classes=['c1'])
        assert inst._parse_image_child(tag) is None

    def test_opacity_not_one(self):
        inst = self._inst(css_rules={'.c1': {
            'position': 'absolute', 'left': '0px', 'top': '0px',
            'width': '100px', 'height': '100px',
            'background-image': 'url("images/x.png")',
            'opacity': '0.8',
        }})
        inst._parse_url_to_local_png = lambda v: Path('/tmp/images/x.png')
        tag = self._make_child_tag(classes=['c1'])
        assert inst._parse_image_child(tag) is None

    def test_blend_mode_not_normal(self):
        inst = self._inst(css_rules={'.c1': {
            'position': 'absolute', 'left': '0px', 'top': '0px',
            'width': '100px', 'height': '100px',
            'background-image': 'url("images/x.png")',
            'mix-blend-mode': 'overlay',
        }})
        inst._parse_url_to_local_png = lambda v: Path('/tmp/images/x.png')
        tag = self._make_child_tag(classes=['c1'])
        assert inst._parse_image_child(tag) is None

    def test_blocking_child_props(self):
        inst = self._inst(css_rules={'.c1': {
            'position': 'absolute', 'left': '0px', 'top': '0px',
            'width': '100px', 'height': '100px',
            'background-image': 'url("images/x.png")',
            'border-radius': '5px',
        }})
        inst._parse_url_to_local_png = lambda v: Path('/tmp/images/x.png')
        tag = self._make_child_tag(classes=['c1'])
        assert inst._parse_image_child(tag) is None

    def test_valid_child(self):
        inst = self._inst(css_rules={'.c1': {
            'position': 'absolute', 'left': '10px', 'top': '20px',
            'width': '100px', 'height': '50px',
            'background-image': 'url("images/x.png")',
            'z-index': '3',
        }})
        inst._parse_url_to_local_png = lambda v: Path('/tmp/images/x.png')
        tag = self._make_child_tag(classes=['c1'])
        result = inst._parse_image_child(tag)
        assert result is not None
        assert result.left == 10
        assert result.top == 20
        assert result.width == 100
        assert result.height == 50
        assert result.z_index == 3
        assert result.png_path == Path('/tmp/images/x.png')
