"""OccludedLayerPruner transformer 单元测试

覆盖范围：
- OccludedPrunerConfig 默认值与自定义
- _is_offscreen_oversized（路径 C 全 4 条件分支）
- _is_no_repeat / _is_zero_position / _is_natural_size（路径 D 闸门）
- _rewrite_bg_url
- _shrink_parents_to_children_envelope
- _resolve_png 安全检查
- _parse_px / _parse_int / _parse_float / _parse_two_px
- _collect_layers（简单 DOM）
- run() 短路（disabled / no html_dir / no canvas）
"""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Tuple
from bs4 import BeautifulSoup

from targets.html.postprocess.layout_optimizer.transformers.occluded_layer_pruner import (
    OccludedLayerPruner,
    OccludedPrunerConfig,
    _LayerRecord,
    _URL_RE,
)


# ===========================================================================
# Helpers
# ===========================================================================

def _make_pruner(
    html='<div id="canvas"></div>',
    css_rules=None,
    html_dir=None,
    config=None,
):
    soup = BeautifulSoup(html, 'html.parser')
    return OccludedLayerPruner(
        soup=soup,
        css_rules=css_rules or {},
        stats={},
        html_dir=html_dir,
        config=config,
    )


def _layer(
    abs_left=0, abs_top=0, width=100, height=100, z_index=1,
    bg_path=None, is_image_leaf=True, opacity_ok=True, blend_ok=True,
):
    return _LayerRecord(
        element=MagicMock(),
        css_class='test-cls',
        selector='.test-cls',
        abs_left=abs_left,
        abs_top=abs_top,
        width=width,
        height=height,
        z_index=z_index,
        bg_path=bg_path,
        is_image_leaf=is_image_leaf,
        opacity_ok=opacity_ok,
        blend_ok=blend_ok,
    )


# ===========================================================================
# OccludedPrunerConfig
# ===========================================================================

class TestOccludedPrunerConfig:
    def test_defaults(self):
        cfg = OccludedPrunerConfig()
        assert cfg.enabled is True
        assert cfg.sample_stride == 4
        assert cfg.self_opaque_threshold == 0.005
        assert cfg.self_visible_alpha == 10
        assert cfg.min_bbox_side == 16
        assert cfg.full_alpha == 250
        assert cfg.full_opacity == 0.99
        assert cfg.max_png_kb == 80
        assert cfg.require_single_layer_coverage is True

    def test_path_c_defaults(self):
        cfg = OccludedPrunerConfig()
        assert cfg.offscreen_prune_enabled is True
        assert cfg.offscreen_overflow_threshold == 100
        assert cfg.offscreen_area_ratio == 0.5
        assert cfg.oversized_dim_ratio == 1.2
        assert cfg.protect_lowest_z_background is True

    def test_path_d_defaults(self):
        cfg = OccludedPrunerConfig()
        assert cfg.trim_enabled is True
        assert cfg.trim_alpha_threshold == 5
        assert cfg.trim_min_waste_ratio == 0.15
        assert cfg.trim_min_orig_kb == 20
        assert cfg.trim_min_trim_pixels == 32
        assert cfg.trim_require_no_repeat is True
        assert cfg.trim_require_zero_position is True
        assert cfg.trim_require_natural_size is True
        assert cfg.trim_size_tolerance_px == 1

    def test_custom(self):
        cfg = OccludedPrunerConfig(enabled=False, max_png_kb=200)
        assert cfg.enabled is False
        assert cfg.max_png_kb == 200


# ===========================================================================
# _is_offscreen_oversized（路径 C）
# ===========================================================================

class TestIsOffscreenOversized:
    def _inst(self, **kwargs):
        cfg = OccludedPrunerConfig(**kwargs)
        return _make_pruner(config=cfg)

    def test_lowest_z_protected(self):
        """条件 4：z 最低层保护"""
        inst = self._inst()
        X = _layer(abs_left=-200, abs_top=0, width=2000, height=2000, z_index=1)
        hit, _ = inst._is_offscreen_oversized(X, 750, 1000, lowest_z=1)
        assert hit is False

    def test_not_overflowing_enough(self):
        """条件 1：没有超出画布 >= offscreen_overflow_threshold"""
        inst = self._inst(offscreen_overflow_threshold=100)
        # 只超出 50px
        X = _layer(abs_left=-50, abs_top=0, width=800, height=1000, z_index=2)
        hit, _ = inst._is_offscreen_oversized(X, 750, 1000, lowest_z=1)
        assert hit is False

    def test_not_oversized_dim(self):
        """条件 3：长边不够大"""
        inst = self._inst(oversized_dim_ratio=1.2)
        # 长边 = 1000，canvas_max = 1000, 1000 < 1000*1.2=1200 → 不满足
        X = _layer(abs_left=-200, abs_top=0, width=1000, height=500, z_index=2)
        hit, _ = inst._is_offscreen_oversized(X, 750, 1000, lowest_z=1)
        assert hit is False

    def test_not_enough_offscreen_area(self):
        """条件 2：离屏面积占比不够"""
        inst = self._inst(
            offscreen_overflow_threshold=100,
            oversized_dim_ratio=1.0,
            offscreen_area_ratio=0.8,
        )
        # 大部分在画布内
        X = _layer(abs_left=-150, abs_top=0, width=1500, height=1000, z_index=2)
        # on_canvas = min(750, 1350) - max(0, -150) = 750 * 1000 = 750000
        # self_area = 1500 * 1000 = 1500000
        # offscreen_ratio = 1 - 750000/1500000 = 0.5 < 0.8
        hit, _ = inst._is_offscreen_oversized(X, 750, 1000, lowest_z=1)
        assert hit is False

    def test_all_conditions_met(self):
        """全部条件满足 → 命中"""
        inst = self._inst(
            offscreen_overflow_threshold=100,
            oversized_dim_ratio=1.2,
            offscreen_area_ratio=0.5,
        )
        # 图层 bbox: left=-500, width=2000, height=2500 → 超出左边 500px
        # 长边 2500 > 1000*1.2=1200 ✓
        # on_canvas = min(750, 1500) - max(0, -500) = 750 * min(1000, 2500-0=2500→1000) = 750*1000=750000
        # self_area = 2000 * 2500 = 5000000
        # offscreen_ratio = 1 - 750000/5000000 = 0.85 > 0.5 ✓
        X = _layer(abs_left=-500, abs_top=0, width=2000, height=2500, z_index=2)
        hit, reason = inst._is_offscreen_oversized(X, 750, 1000, lowest_z=1)
        assert hit is True
        assert '跨画布废图层' in reason

    def test_zero_self_area(self):
        """自身面积为 0 → 不命中"""
        inst = self._inst()
        X = _layer(abs_left=-500, abs_top=0, width=0, height=0, z_index=2)
        hit, _ = inst._is_offscreen_oversized(X, 750, 1000, lowest_z=1)
        assert hit is False

    def test_lowest_z_none_no_protection(self):
        """protect_lowest_z_background=False 时 lowest_z=None"""
        inst = self._inst(
            protect_lowest_z_background=False,
            offscreen_overflow_threshold=100,
            oversized_dim_ratio=1.0,
            offscreen_area_ratio=0.3,
        )
        X = _layer(abs_left=-500, abs_top=0, width=2000, height=2000, z_index=1)
        hit, _ = inst._is_offscreen_oversized(X, 750, 1000, lowest_z=None)
        assert hit is True


# ===========================================================================
# _is_no_repeat
# ===========================================================================

class TestIsNoRepeat:
    def test_explicit_no_repeat(self):
        assert OccludedLayerPruner._is_no_repeat({'background-repeat': 'no-repeat'}) is True

    def test_explicit_repeat(self):
        assert OccludedLayerPruner._is_no_repeat({'background-repeat': 'repeat'}) is False

    def test_explicit_round(self):
        assert OccludedLayerPruner._is_no_repeat({'background-repeat': 'round'}) is False

    def test_shorthand_contains_no_repeat(self):
        rule = {'background': 'url(x.png) no-repeat center'}
        assert OccludedLayerPruner._is_no_repeat(rule) is True

    def test_shorthand_without_no_repeat(self):
        rule = {'background': 'url(x.png) center'}
        assert OccludedLayerPruner._is_no_repeat(rule) is False

    def test_both_empty(self):
        assert OccludedLayerPruner._is_no_repeat({}) is False


# ===========================================================================
# _is_zero_position
# ===========================================================================

class TestIsZeroPosition:
    def test_explicit_0_0(self):
        assert OccludedLayerPruner._is_zero_position({'background-position': '0 0'}) is True

    def test_explicit_0px_0px(self):
        assert OccludedLayerPruner._is_zero_position({'background-position': '0px 0px'}) is True

    def test_explicit_left_top(self):
        assert OccludedLayerPruner._is_zero_position({'background-position': 'left top'}) is True

    def test_explicit_0_percent(self):
        assert OccludedLayerPruner._is_zero_position({'background-position': '0% 0%'}) is True

    def test_non_zero_position(self):
        assert OccludedLayerPruner._is_zero_position({'background-position': '10px 20px'}) is False

    def test_center(self):
        assert OccludedLayerPruner._is_zero_position({'background-position': 'center'}) is False

    def test_missing_no_shorthand(self):
        """缺省 + 无 shorthand → 默认 0 0"""
        assert OccludedLayerPruner._is_zero_position({}) is True

    def test_missing_with_shorthand(self):
        """缺省 + 有 shorthand（保守返回 False）"""
        rule = {'background': 'url(x.png) 10px 20px'}
        assert OccludedLayerPruner._is_zero_position(rule) is False


# ===========================================================================
# _is_natural_size
# ===========================================================================

class TestIsNaturalSize:
    def test_empty_size_no_shorthand(self):
        assert OccludedLayerPruner._is_natural_size({}, 100, 50) is True

    def test_auto(self):
        assert OccludedLayerPruner._is_natural_size({'background-size': 'auto'}, 100, 50) is True

    def test_auto_auto(self):
        assert OccludedLayerPruner._is_natural_size({'background-size': 'auto auto'}, 100, 50) is True

    def test_matching_px_size(self):
        assert OccludedLayerPruner._is_natural_size({'background-size': '100px 50px'}, 100, 50) is True

    def test_non_matching_px_size(self):
        assert OccludedLayerPruner._is_natural_size({'background-size': '200px 100px'}, 100, 50) is False

    def test_cover(self):
        assert OccludedLayerPruner._is_natural_size({'background-size': 'cover'}, 100, 50) is False

    def test_contain(self):
        assert OccludedLayerPruner._is_natural_size({'background-size': 'contain'}, 100, 50) is False

    def test_percentage(self):
        assert OccludedLayerPruner._is_natural_size({'background-size': '50% 50%'}, 100, 50) is False

    def test_shorthand_cover(self):
        """缺省 size + shorthand 含 cover → 不裁"""
        rule = {'background': 'url(x.png) cover no-repeat'}
        assert OccludedLayerPruner._is_natural_size(rule, 100, 50) is False

    def test_shorthand_contain(self):
        rule = {'background': 'url(x.png) contain no-repeat'}
        assert OccludedLayerPruner._is_natural_size(rule, 100, 50) is False


# ===========================================================================
# _rewrite_bg_url
# ===========================================================================

class TestRewriteBgUrl:
    def test_background_image_rewrite(self):
        rule = {'background-image': 'url("images/old.png")'}
        OccludedLayerPruner._rewrite_bg_url(rule, 'old.png', 'new.png')
        assert 'new.png' in rule['background-image']
        assert 'old.png' not in rule['background-image']

    def test_background_shorthand_rewrite(self):
        rule = {'background': 'url("images/old.png") no-repeat'}
        OccludedLayerPruner._rewrite_bg_url(rule, 'old.png', 'new.png')
        assert 'new.png' in rule['background']

    def test_no_match_no_change(self):
        rule = {'background-image': 'url("images/other.png")'}
        OccludedLayerPruner._rewrite_bg_url(rule, 'old.png', 'new.png')
        assert 'other.png' in rule['background-image']

    def test_both_fields(self):
        rule = {
            'background-image': 'url("images/old.png")',
            'background': 'url("images/old.png") no-repeat',
        }
        OccludedLayerPruner._rewrite_bg_url(rule, 'old.png', 'new.png')
        assert 'new.png' in rule['background-image']
        assert 'new.png' in rule['background']


# ===========================================================================
# _shrink_parents_to_children_envelope
# ===========================================================================

class TestShrinkParentsToChildrenEnvelope:
    def _make_parent_with_children(self, parent_rule, child_rules):
        """构造 HTML + CSS 并返回 pruner"""
        children_html = ''
        css = {}
        for i, cr in enumerate(child_rules):
            cls_name = f'child-{i}'
            children_html += f'<div class="{cls_name}"></div>'
            css[f'.{cls_name}'] = cr

        parent_cls = 'parent-grp'
        html = f'<div id="canvas"><div class="{parent_cls}">{children_html}</div></div>'
        css[f'.{parent_cls}'] = parent_rule

        pruner = _make_pruner(html=html, css_rules=css)
        parent_elem = pruner.soup.find('div', class_=parent_cls)
        return pruner, parent_elem

    def test_shrinks_to_envelope(self):
        parent_rule = {
            'position': 'absolute', 'left': '0px', 'top': '0px',
            'width': '200px', 'height': '200px',
        }
        child_rules = [
            {'left': '50px', 'top': '60px', 'width': '80px', 'height': '70px'},
            {'left': '70px', 'top': '80px', 'width': '60px', 'height': '50px'},
        ]
        pruner, parent_elem = self._make_parent_with_children(parent_rule, child_rules)
        pruner._shrink_parents_to_children_envelope([parent_elem])

        # envelope: x[50..130], y[60..130] → w=80, h=70
        rule = pruner.css_rules['.parent-grp']
        assert rule['left'] == '50px'
        assert rule['top'] == '60px'
        assert rule['width'] == '80px'
        assert rule['height'] == '70px'

        # children shifted: child-0 left=0, top=0; child-1 left=20, top=20
        assert pruner.css_rules['.child-0']['left'] == '0px'
        assert pruner.css_rules['.child-0']['top'] == '0px'
        assert pruner.css_rules['.child-1']['left'] == '20px'
        assert pruner.css_rules['.child-1']['top'] == '20px'

    def test_no_shift_when_envelope_at_origin(self):
        """envelope 起点 < 1px → 不收缩"""
        parent_rule = {
            'position': 'absolute', 'left': '10px', 'top': '10px',
            'width': '100px', 'height': '100px',
        }
        child_rules = [
            {'left': '0px', 'top': '0px', 'width': '100px', 'height': '100px'},
        ]
        pruner, parent_elem = self._make_parent_with_children(parent_rule, child_rules)
        pruner._shrink_parents_to_children_envelope([parent_elem])
        # No change
        rule = pruner.css_rules['.parent-grp']
        assert rule['left'] == '10px'
        assert rule['top'] == '10px'

    def test_skips_non_absolute(self):
        """非 absolute 定位 → 跳过"""
        parent_rule = {
            'position': 'relative', 'left': '0px', 'top': '0px',
            'width': '200px', 'height': '200px',
        }
        child_rules = [
            {'left': '50px', 'top': '60px', 'width': '80px', 'height': '70px'},
        ]
        pruner, parent_elem = self._make_parent_with_children(parent_rule, child_rules)
        pruner._shrink_parents_to_children_envelope([parent_elem])
        # No change
        rule = pruner.css_rules['.parent-grp']
        assert rule['width'] == '200px'

    def test_no_children_skip(self):
        """无子 → 跳过"""
        html = '<div id="canvas"><div class="empty-parent"></div></div>'
        css = {'.empty-parent': {
            'position': 'absolute', 'left': '0px', 'top': '0px',
            'width': '200px', 'height': '200px',
        }}
        pruner = _make_pruner(html=html, css_rules=css)
        parent_elem = pruner.soup.find('div', class_='empty-parent')
        pruner._shrink_parents_to_children_envelope([parent_elem])
        assert css['.empty-parent']['width'] == '200px'

    def test_skip_parent_with_background_image(self):
        """父容器自带背景图时，不应做 envelope 收缩（避免背景图尺寸失配）"""
        parent_rule = {
            'position': 'absolute', 'left': '0px', 'top': '0px',
            'width': '750px', 'height': '1162px',
            'background-image': 'url("images/batai-524ee3.png")',
            'background-repeat': 'no-repeat',
        }
        child_rules = [
            {'left': '51px', 'top': '533px', 'width': '568px', 'height': '306px'},
        ]
        pruner, parent_elem = self._make_parent_with_children(parent_rule, child_rules)
        pruner._shrink_parents_to_children_envelope([parent_elem])

        rule = pruner.css_rules['.parent-grp']
        assert rule['left'] == '0px'
        assert rule['top'] == '0px'
        assert rule['width'] == '750px'
        assert rule['height'] == '1162px'


# ===========================================================================
# _resolve_png
# ===========================================================================

class TestResolvePng:
    def test_empty_url(self):
        inst = _make_pruner(html_dir=Path('/tmp'))
        assert inst._resolve_png('') is None

    def test_http_url(self):
        inst = _make_pruner(html_dir=Path('/tmp'))
        assert inst._resolve_png('https://example.com/image.png') is None

    def test_data_uri(self):
        inst = _make_pruner(html_dir=Path('/tmp'))
        assert inst._resolve_png('data:image/png;base64,abc') is None

    def test_not_png(self):
        inst = _make_pruner(html_dir=Path('/tmp'))
        assert inst._resolve_png('images/photo.jpg') is None

    def test_path_traversal(self):
        inst = _make_pruner(html_dir=Path('/tmp'))
        assert inst._resolve_png('../etc/passwd.png') is None

    def test_no_html_dir(self):
        inst = _make_pruner(html_dir=None)
        assert inst._resolve_png('images/test.png') is None

    def test_nonexistent_file(self):
        inst = _make_pruner(html_dir=Path('/tmp'))
        assert inst._resolve_png('images/nonexistent_xyz_abc.png') is None


# ===========================================================================
# _parse_px / _parse_int / _parse_float
# ===========================================================================

class TestParsers:
    def test_parse_px_normal(self):
        assert OccludedLayerPruner._parse_px('100px') == 100.0

    def test_parse_px_no_unit(self):
        assert OccludedLayerPruner._parse_px('50') == 50.0

    def test_parse_px_none(self):
        assert OccludedLayerPruner._parse_px(None) is None

    def test_parse_px_empty(self):
        assert OccludedLayerPruner._parse_px('') is None

    def test_parse_px_invalid(self):
        assert OccludedLayerPruner._parse_px('abc') is None

    def test_parse_px_float(self):
        assert OccludedLayerPruner._parse_px('12.5px') == 12.5

    def test_parse_int_normal(self):
        assert OccludedLayerPruner._parse_int('5') == 5

    def test_parse_int_none(self):
        assert OccludedLayerPruner._parse_int(None) == 0

    def test_parse_int_default(self):
        assert OccludedLayerPruner._parse_int(None, 99) == 99

    def test_parse_int_invalid(self):
        assert OccludedLayerPruner._parse_int('abc', 0) == 0

    def test_parse_float_normal(self):
        assert OccludedLayerPruner._parse_float('0.5') == 0.5

    def test_parse_float_none(self):
        assert OccludedLayerPruner._parse_float(None) == 0.0

    def test_parse_float_default(self):
        assert OccludedLayerPruner._parse_float(None, 1.0) == 1.0

    def test_parse_float_invalid(self):
        assert OccludedLayerPruner._parse_float('xyz', 2.0) == 2.0


# ===========================================================================
# _URL_RE
# ===========================================================================

class TestURLRegex:
    def test_double_quotes(self):
        m = _URL_RE.search('url("images/test.png")')
        assert m is not None
        assert m.group(1) == 'images/test.png'

    def test_single_quotes(self):
        m = _URL_RE.search("url('images/test.png')")
        assert m is not None
        assert m.group(2) == 'images/test.png'

    def test_no_quotes(self):
        m = _URL_RE.search('url(images/test.png)')
        assert m is not None
        assert m.group(3) == 'images/test.png'


# ===========================================================================
# run() 短路测试
# ===========================================================================

class TestRunShortCircuit:
    def test_disabled(self):
        inst = _make_pruner(config=OccludedPrunerConfig(enabled=False))
        inst.run()
        assert inst.stats.get('occluded_layers_pruned', 0) == 0

    def test_no_html_dir(self):
        inst = _make_pruner(html_dir=None, config=OccludedPrunerConfig())
        inst.run()

    def test_nonexistent_html_dir(self):
        inst = _make_pruner(
            html_dir=Path('/nonexistent_xyz_abc_def'),
            config=OccludedPrunerConfig(),
        )
        inst.run()

    def test_no_canvas_element(self):
        """HTML 中无 #canvas → 跳过"""
        inst = _make_pruner(
            html='<div class="root"></div>',
            config=OccludedPrunerConfig(),
            html_dir=Path('/tmp'),
        )
        # PIL/numpy import 必须存在
        try:
            import numpy  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            pytest.skip("numpy/PIL not available")
        inst.run()


# ===========================================================================
# _collect_layers（简单 DOM）
# ===========================================================================

class TestCollectLayers:
    def test_basic_dom(self):
        html = '''<div id="canvas">
            <div class="layer1" data-type="image"></div>
            <div class="layer2" data-type="image"></div>
        </div>'''
        css_rules = {
            '.layer1': {
                'position': 'absolute', 'left': '10px', 'top': '20px',
                'width': '100px', 'height': '50px', 'z-index': '1',
                'background-image': 'url("images/a.png")',
            },
            '.layer2': {
                'position': 'absolute', 'left': '30px', 'top': '40px',
                'width': '80px', 'height': '60px', 'z-index': '2',
                'background-image': 'url("images/b.png")',
            },
        }
        pruner = _make_pruner(html=html, css_rules=css_rules, html_dir=Path('/tmp'))
        # mock _resolve_png to return Path for any url
        pruner._resolve_png = lambda url: Path(f'/tmp/{url}') if url else None

        canvas = pruner.soup.find(id='canvas')
        records = pruner._collect_layers(canvas)
        assert len(records) == 2
        assert records[0].css_class == 'layer1'
        assert records[0].abs_left == 10
        assert records[0].abs_top == 20
        assert records[0].z_index == 1
        assert records[0].is_image_leaf is True
        assert records[1].css_class == 'layer2'
        assert records[1].abs_left == 30
        assert records[1].abs_top == 40

    def test_nested_dom_accumulates_coords(self):
        html = '''<div id="canvas">
            <div class="group1">
                <div class="inner1" data-type="image"></div>
            </div>
        </div>'''
        css_rules = {
            '.group1': {
                'position': 'absolute', 'left': '100px', 'top': '200px',
                'width': '300px', 'height': '400px',
            },
            '.inner1': {
                'position': 'absolute', 'left': '10px', 'top': '20px',
                'width': '50px', 'height': '60px', 'z-index': '5',
                'background-image': 'url("images/c.png")',
            },
        }
        pruner = _make_pruner(html=html, css_rules=css_rules, html_dir=Path('/tmp'))
        pruner._resolve_png = lambda url: Path(f'/tmp/{url}') if url else None

        canvas = pruner.soup.find(id='canvas')
        records = pruner._collect_layers(canvas)
        # group1 + inner1 = 2 records
        assert len(records) == 2
        inner = [r for r in records if r.css_class == 'inner1'][0]
        # 绝对坐标 = 父 100+10=110, 200+20=220
        assert inner.abs_left == 110
        assert inner.abs_top == 220
        assert inner.z_index == 5
        assert inner.is_image_leaf is True

    def test_non_leaf_not_image(self):
        """含子 div 的节点不是 image leaf"""
        html = '''<div id="canvas">
            <div class="group1">
                <div class="inner1"></div>
            </div>
        </div>'''
        css_rules = {
            '.group1': {
                'position': 'absolute', 'left': '0px', 'top': '0px',
                'width': '100px', 'height': '100px',
                'background-image': 'url("images/x.png")',
            },
            '.inner1': {
                'position': 'absolute', 'left': '0px', 'top': '0px',
                'width': '50px', 'height': '50px',
            },
        }
        pruner = _make_pruner(html=html, css_rules=css_rules, html_dir=Path('/tmp'))
        pruner._resolve_png = lambda url: Path(f'/tmp/{url}') if url else None

        canvas = pruner.soup.find(id='canvas')
        records = pruner._collect_layers(canvas)
        group = [r for r in records if r.css_class == 'group1'][0]
        # group1 有子 div → 不是 image leaf
        assert group.is_image_leaf is False
