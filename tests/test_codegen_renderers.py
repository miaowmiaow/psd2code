# -*- coding: utf-8 -*-
"""Tests for targets/html/codegen/renderers/ — Registry, css_helpers, and Renderers."""

import struct
from pathlib import Path


from targets.html.codegen.renderers.base import (
    NodeRenderer,
    RendererRegistry,
    register_renderer,
)
from targets.html.codegen.renderers.css_helpers import (
    position_css_lines,
    read_png_size,
    semantic_css_class,
    _png_size_cache,
)


# =====================================================================
# RendererRegistry
# =====================================================================

class TestRendererRegistry:
    """RendererRegistry: type → Renderer mapping."""

    def test_registered_types(self):
        types = RendererRegistry.types()
        assert "group" in types
        assert "image" in types
        assert "text" in types

    def test_get_known_type(self):
        cls = RendererRegistry.get("image")
        assert cls is not None
        assert issubclass(cls, NodeRenderer)

    def test_get_unknown_type_falls_back_to_image(self):
        cls = RendererRegistry.get("unknown_type_xyz")
        image_cls = RendererRegistry.get("image")
        assert cls is image_cls

    def test_register_decorator(self):
        """@register_renderer creates a new entry."""
        # We use a unique type name to avoid polluting the global registry
        @register_renderer("__test_dummy__")
        class DummyRenderer(NodeRenderer):
            def render(self, layer, indent, parent, siblings, class_name):
                return ""

        assert RendererRegistry.get("__test_dummy__") is DummyRenderer
        # Cleanup
        RendererRegistry._registry.pop("__test_dummy__", None)


# =====================================================================
# semantic_css_class
# =====================================================================

class TestSemanticCssClass:
    """semantic_css_class: extract first token from multi-class string."""

    def test_two_classes(self):
        assert semantic_css_class("btn__27 layer-group") == "btn__27"

    def test_single_class(self):
        assert semantic_css_class("bg__3") == "bg__3"

    def test_three_classes(self):
        assert semantic_css_class("a__1 b c") == "a__1"


# =====================================================================
# position_css_lines
# =====================================================================

class TestPositionCssLines:
    """position_css_lines: generate positioning CSS."""

    def _layer(self, **kw):
        base = {
            "left": 10, "top": 20, "width": 100, "height": 50,
            "opacity": 1, "blend_mode": "normal", "z_index": 3,
        }
        base.update(kw)
        return base

    def test_basic_output(self):
        css = position_css_lines(self._layer())
        assert "position: absolute;" in css
        assert "left: 10px;" in css
        assert "top: 20px;" in css
        assert "width: 100px;" in css
        assert "height: 50px;" in css
        assert "opacity: 1;" in css
        assert "mix-blend-mode: normal;" in css
        assert "z-index: 3;" in css

    def test_override_width_height(self):
        css = position_css_lines(self._layer(), width=200, height=80)
        assert "width: 200px;" in css
        assert "height: 80px;" in css

    def test_override_left_top(self):
        css = position_css_lines(self._layer(), left=0, top=0)
        assert "left: 0px;" in css
        assert "top: 0px;" in css

    def test_no_blend_mode(self):
        css = position_css_lines(self._layer(), include_blend=False)
        assert "mix-blend-mode" not in css

    def test_with_blend_mode(self):
        css = position_css_lines(self._layer(), include_blend=True)
        assert "mix-blend-mode: normal;" in css

    def test_custom_indent(self):
        css = position_css_lines(self._layer(), indent="  ")
        lines = css.split("\n")
        for line in lines:
            if line.strip():
                assert line.startswith("  ")

    def test_z_index_always_present(self):
        css = position_css_lines(self._layer(z_index=99))
        assert "z-index: 99;" in css


# =====================================================================
# read_png_size
# =====================================================================

class TestReadPngSize:
    """read_png_size: parse PNG IHDR for width/height."""

    def _make_png(self, w: int, h: int, path: Path) -> None:
        """Write a minimal valid PNG header."""
        signature = b'\x89PNG\r\n\x1a\n'
        # IHDR chunk: length(4) + 'IHDR'(4) + width(4) + height(4)
        ihdr_data = struct.pack('>II', w, h) + b'\x08\x02\x00\x00\x00'
        import zlib
        crc = struct.pack('>I', zlib.crc32(b'IHDR' + ihdr_data) & 0xffffffff)
        ihdr_len = struct.pack('>I', len(ihdr_data))
        chunk = ihdr_len + b'IHDR' + ihdr_data + crc
        path.write_bytes(signature + chunk)

    def setup_method(self):
        _png_size_cache.clear()

    def test_valid_png(self, tmp_path):
        p = tmp_path / "test.png"
        self._make_png(320, 240, p)
        assert read_png_size(p) == (320, 240)

    def test_cached(self, tmp_path):
        p = tmp_path / "test.png"
        self._make_png(100, 200, p)
        read_png_size(p)
        # Second call uses cache
        assert read_png_size(p) == (100, 200)

    def test_nonexistent_file(self, tmp_path):
        p = tmp_path / "nope.png"
        assert read_png_size(p) is None

    def test_not_a_png(self, tmp_path):
        p = tmp_path / "fake.png"
        p.write_bytes(b"not a png file contents here, nothing to see")
        assert read_png_size(p) is None

    def test_truncated_file(self, tmp_path):
        p = tmp_path / "short.png"
        p.write_bytes(b'\x89PNG\r\n\x1a\n\x00\x00')
        assert read_png_size(p) is None


# =====================================================================
# GroupRenderer — _calculate_actual_bounds
# =====================================================================

from targets.html.codegen.renderers.group_renderer import _calculate_actual_bounds  # noqa: E402 - 按测试分区分段导入


class TestCalculateActualBounds:
    """_calculate_actual_bounds: recursive child bounds with overflow detection."""

    def test_non_group_layer(self):
        layer = {"type": "image", "width": 100, "height": 50}
        assert _calculate_actual_bounds(layer) == (100, 50)

    def test_empty_residual_group(self):
        """Empty group with width=0, height=0 → (0, 0)."""
        layer = {"type": "group", "width": 0, "height": 0, "children": []}
        assert _calculate_actual_bounds(layer) == (0, 0)

    def test_group_children_within_bounds(self):
        """子层完全内缩 → 返回 bbox，不加保护边距。

        历史行为：`max_right` 从 bbox 起步，导致 `max_right >= width - 1` 恒真，
        于是**每个**有子层的组都被无条件 +2px（容器比 PSD bbox 大 2px）。
        2026-11 改为以子层实际边界起步：内缩不加、贴边/溢出才加。
        """
        layer = {
            "type": "group", "width": 200, "height": 200,
            "children": [
                {"type": "image", "left": 10, "top": 10, "width": 50, "height": 50},
            ],
        }
        # 子层边界 60×60，远未触及 200 → 保持 bbox
        assert _calculate_actual_bounds(layer) == (200, 200)

    def test_group_children_near_edge(self):
        """只有宽度方向贴边 → 只扩展宽度，高度保持 bbox（分轴判断）。"""
        layer = {
            "type": "group", "width": 100, "height": 100,
            "children": [
                {"type": "image", "left": 0, "top": 0, "width": 100, "height": 50},
            ],
        }
        w, h = _calculate_actual_bounds(layer)
        # child_right=100 >= 99 → 102；child_bottom=50 未贴边 → 保持 100
        assert w == 102
        assert h == 100

    def test_group_children_overflow(self):
        """Child extends beyond parent → expanded bounds."""
        layer = {
            "type": "group", "width": 100, "height": 100,
            "children": [
                {"type": "image", "left": 50, "top": 50, "width": 80, "height": 80},
            ],
        }
        w, h = _calculate_actual_bounds(layer)
        # child_right=130 > 100, child_bottom=130 > 100
        assert w == 132  # 130 + 2
        assert h == 132

    def test_skip_empty_residual_child_group(self):
        """Empty child group (w=0, h=0, no children) is skipped."""
        layer = {
            "type": "group", "width": 200, "height": 200,
            "children": [
                {"type": "group", "left": -1459, "top": -999,
                 "width": 0, "height": 0, "children": []},
                {"type": "image", "left": 10, "top": 10, "width": 50, "height": 50},
            ],
        }
        w, h = _calculate_actual_bounds(layer)
        # 空残留子组被忽略；剩余子层 60×60 内缩 → 保持 bbox
        assert w == 200
        assert h == 200

    def test_nested_group_bounds(self):
        """Recursive: inner group's children contribute to outer bounds."""
        layer = {
            "type": "group", "width": 100, "height": 100,
            "children": [
                {
                    "type": "group", "left": 0, "top": 0,
                    "width": 80, "height": 80,
                    "children": [
                        {"type": "image", "left": 0, "top": 0,
                         "width": 80, "height": 80},
                    ],
                },
            ],
        }
        w, h = _calculate_actual_bounds(layer)
        # 内层：子层 80 贴边 → 82；外层：子组右边界 82 < 99 → 保持 bbox 100
        assert w == 100
        assert h == 100


# =====================================================================
# GroupRenderer.render (integration-level, with minimal context)
# =====================================================================

from targets.html.codegen.context import CodegenContext  # noqa: E402 - 按测试分区分段导入
from targets.html.codegen.layer_renderer import LayerRenderer  # noqa: E402 - 按测试分区分段导入


class TestGroupRendererIntegration:
    """GroupRenderer.render via LayerRenderer (integration)."""

    def setup_method(self):
        self.ctx = CodegenContext(
            psd_width=375, psd_height=812,
            output_dir=Path("/tmp/test_output"),
            psd_name="test",
        )
        self.renderer = LayerRenderer(self.ctx)

    def test_empty_group(self):
        layer = {
            "id": "group-1", "name": "container", "type": "group",
            "left": 0, "top": 0, "width": 200, "height": 200,
            "opacity": 1, "blend_mode": "normal", "z_index": 1,
            "children": [],
        }
        html = self.renderer.render(layer, indent=2)
        assert '<div id="group-1"' in html
        assert 'data-type="group"' in html
        assert 'layer-group' in html
        # CSS should have been collected
        assert len(self.ctx.css_rules) == 1
        assert "overflow: hidden;" in self.ctx.css_rules[0]

    def test_group_with_children(self):
        layer = {
            "id": "group-1", "name": "card", "type": "group",
            "left": 0, "top": 0, "width": 200, "height": 200,
            "opacity": 1, "blend_mode": "normal", "z_index": 1,
            "children": [
                {
                    "id": "layer-1", "name": "bg", "type": "image",
                    "left": 0, "top": 0, "width": 200, "height": 200,
                    "opacity": 1, "blend_mode": "normal", "z_index": 2,
                },
            ],
        }
        html = self.renderer.render(layer, indent=2)
        assert '<div id="layer-1"' in html
        assert 'data-type="image"' in html
        # 2 CSS rules: group + image
        assert len(self.ctx.css_rules) == 2

    def test_group_bleed_padding_only_when_child_touches_edge(self):
        """子层贴满组 bbox 时容器多出 2px 保护边距；没贴边则保持 bbox。

        `_GROUP_BLEED_PADDING` 是抗锯齿保护（非效果扩展），它会让组容器的 CSS
        尺寸比 PSD bbox 大 2px —— 这是 `_mapping_report.md` 里"尺寸对不上"的
        来源，此用例把它固定住，避免被误当成 bug 随手改掉。
        """
        def render(width: int, height: int, child_w: int, child_h: int) -> str:
            self.ctx.css_rules.clear()
            layer = {
                "id": "group-1", "name": "card", "type": "group",
                "left": 0, "top": 0, "width": width, "height": height,
                "opacity": 1, "blend_mode": "normal", "z_index": 1,
                "children": [{
                    "id": "layer-1", "name": "bg", "type": "image",
                    "left": 0, "top": 0, "width": child_w, "height": child_h,
                    "opacity": 1, "blend_mode": "normal", "z_index": 2,
                }],
            }
            self.renderer.render(layer, indent=2)
            return self.ctx.css_rules[0]

        # 贴边 → +2
        touching = render(200, 200, 200, 200)
        assert "width: 202px;" in touching
        assert "height: 202px;" in touching
        # 不贴边（子层只占一半）→ 保持 bbox，且仍然裁剪
        inside = render(200, 200, 100, 100)
        assert "width: 200px;" in inside
        assert "overflow: hidden;" in inside
        # 溢出（子层比组大）→ 也按实际尺寸 +2，且不裁剪
        overflowing = render(200, 200, 260, 220)
        assert "width: 262px;" in overflowing
        assert "overflow: hidden;" not in overflowing


# =====================================================================
# ImageRenderer.render (integration-level)
# =====================================================================

class TestImageRendererIntegration:
    """ImageRenderer.render via LayerRenderer (integration)."""

    def setup_method(self):
        self.ctx = CodegenContext(
            psd_width=375, psd_height=812,
            output_dir=Path("/tmp/test_output"),
            psd_name="test",
        )
        self.renderer = LayerRenderer(self.ctx)

    def test_image_with_path(self):
        layer = {
            "id": "layer-1", "name": "hero", "type": "image",
            "left": 0, "top": 0, "width": 375, "height": 200,
            "opacity": 1, "blend_mode": "normal", "z_index": 1,
            "image_path": "images/hero.png",
        }
        html = self.renderer.render(layer, indent=2)
        assert '<div id="layer-1"' in html
        assert 'data-type="image"' in html
        css = self.ctx.css_rules[0]
        assert 'background-image: url("images/hero.png");' in css
        assert 'background-repeat: no-repeat;' in css

    def test_image_without_path(self):
        layer = {
            "id": "layer-2", "name": "shape", "type": "image",
            "left": 10, "top": 10, "width": 50, "height": 50,
            "opacity": 0.5, "blend_mode": "normal", "z_index": 2,
        }
        self.renderer.render(layer, indent=2)
        css = self.ctx.css_rules[0]
        assert "background-image" not in css

    def test_image_with_matching_png_size(self, tmp_path):
        """When PNG pixel size matches CSS dimensions → no background-size."""
        # Create a real mini PNG
        png_path = tmp_path / "images" / "match.png"
        png_path.parent.mkdir(parents=True, exist_ok=True)
        sig = b'\x89PNG\r\n\x1a\n'
        import zlib
        ihdr_data = struct.pack('>II', 100, 50) + b'\x08\x02\x00\x00\x00'
        crc = struct.pack('>I', zlib.crc32(b'IHDR' + ihdr_data) & 0xffffffff)
        ihdr_len = struct.pack('>I', len(ihdr_data))
        png_path.write_bytes(sig + ihdr_len + b'IHDR' + ihdr_data + crc)

        _png_size_cache.clear()
        ctx = CodegenContext(
            psd_width=375, psd_height=812,
            output_dir=tmp_path,
            psd_name="test",
        )
        renderer = LayerRenderer(ctx)
        layer = {
            "id": "layer-3", "name": "icon", "type": "image",
            "left": 0, "top": 0, "width": 100, "height": 50,
            "opacity": 1, "blend_mode": "normal", "z_index": 1,
            "image_path": "images/match.png",
        }
        renderer.render(layer, indent=2)
        css = ctx.css_rules[0]
        assert "background-size" not in css


# =====================================================================
# TextRenderer.render (integration-level)
# =====================================================================

class TestTextRendererIntegration:
    """TextRenderer.render via LayerRenderer (integration).

    文字一律栅格化为图片：渲染结果与 ImageRenderer 一致（PNG 背景），
    只保留 ``data-type="text"`` 标记，不再输出可编辑文本节点。
    """

    def setup_method(self):
        self.ctx = CodegenContext(
            psd_width=375, psd_height=812,
            output_dir=Path("/tmp/test_output"),
            psd_name="test",
        )
        self.renderer = LayerRenderer(self.ctx)

    def _text_layer(self, **overrides):
        base = {
            "id": "layer-1", "name": "title", "type": "text",
            "left": 20, "top": 100, "width": 200, "height": 30,
            "opacity": 1, "blend_mode": "normal", "z_index": 5,
            "image_path": "images/title.png",
        }
        base.update(overrides)
        return base

    def test_renders_as_image_with_text_marker(self):
        layer = self._text_layer()
        html = self.renderer.render(layer, indent=2)
        assert 'data-type="text"' in html
        assert "<span" not in html
        css = self.ctx.css_rules[0]
        assert 'background-image: url("images/title.png");' in css
        # 不再输出文本排版属性
        assert "font-size:" not in css
        assert "line-height:" not in css

    def test_uses_image_geometry(self):
        """文字图层宽度不再 +2（与 PNG 实际像素对齐）。"""
        layer = self._text_layer()
        self.renderer.render(layer, indent=2)
        css = self.ctx.css_rules[0]
        assert "width: 200px;" in css
        assert "width: 202px;" not in css

    def test_text_layer_without_png(self):
        """没有 PNG 时与 ImageRenderer 一致：只输出定位，不写 background-image。"""
        layer = self._text_layer(id="layer-2")
        layer.pop("image_path")
        self.renderer.render(layer, indent=2)
        css = self.ctx.css_rules[0]
        assert "background-image" not in css
        assert "position: absolute;" in css


# =====================================================================
# LayerRenderer — type dispatch
# =====================================================================

class TestLayerRendererDispatch:
    """LayerRenderer dispatches to correct strategy by type."""

    def setup_method(self):
        self.ctx = CodegenContext(
            psd_width=375, psd_height=812,
            output_dir=Path("/tmp/test_output"),
            psd_name="test",
        )
        self.renderer = LayerRenderer(self.ctx)

    def _layer(self, ltype="image", **kw):
        base = {
            "id": "layer-1", "name": "test", "type": ltype,
            "left": 0, "top": 0, "width": 100, "height": 50,
            "opacity": 1, "blend_mode": "normal", "z_index": 1,
        }
        base.update(kw)
        return base

    def test_unknown_type_falls_back_to_image(self):
        layer = self._layer(ltype="shape_custom")
        html = self.renderer.render(layer)
        assert 'data-type="image"' in html  # ImageRenderer writes data-type="image"

    def test_class_name_set_on_layer(self):
        """render() sets layer['class_name'] for downstream consumption."""
        layer = self._layer()
        self.renderer.render(layer)
        assert "class_name" in layer
        assert isinstance(layer["class_name"], str)
