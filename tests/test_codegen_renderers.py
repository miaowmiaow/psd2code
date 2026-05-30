# -*- coding: utf-8 -*-
"""Tests for targets/html/codegen/renderers/ — Registry, css_helpers, and Renderers."""

import struct
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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

from targets.html.codegen.renderers.group_renderer import _calculate_actual_bounds


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
        """Children within parent, but max_right == width → triggers +2 safety margin.

        _calculate_actual_bounds initialises max_right = original_width, so the
        condition `max_right >= original_width - 1` is always true for non-empty
        groups with width >= 1.  This is intentional: provide a small safety buffer.
        """
        layer = {
            "type": "group", "width": 200, "height": 200,
            "children": [
                {"type": "image", "left": 10, "top": 10, "width": 50, "height": 50},
            ],
        }
        # max_right=200 >= 199 → expand to 202
        assert _calculate_actual_bounds(layer) == (202, 202)

    def test_group_children_near_edge(self):
        """Child right edge >= parent_width - 1 → expand with +2 safety."""
        layer = {
            "type": "group", "width": 100, "height": 100,
            "children": [
                {"type": "image", "left": 0, "top": 0, "width": 100, "height": 50},
            ],
        }
        w, h = _calculate_actual_bounds(layer)
        # child_right=100 >= width-1=99 → expand
        assert w == 102
        assert h == 102

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
        # Empty child ignored; but max_right=200 >= 199 → +2 safety
        assert w == 202
        assert h == 202

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
        # Inner group: child_right=80 >= 79 → expand to 82
        # Outer: max_right = max(100, 0+82) = 100 >= 99 → expand to 102
        assert w == 102
        assert h == 102


# =====================================================================
# TextRenderer helpers
# =====================================================================

from targets.html.codegen.renderers.text_renderer import _fmt_num, _text_style_css


class TestFmtNum:
    """_fmt_num: CSS-safe number formatting."""

    def test_integer(self):
        assert _fmt_num(16.0) == "16"

    def test_actual_integer(self):
        assert _fmt_num(16) == "16"

    def test_two_decimal_places(self):
        assert _fmt_num(22.099999999999998) == "22.1"

    def test_trailing_zero_stripped(self):
        assert _fmt_num(10.50) == "10.5"

    def test_small_decimal(self):
        assert _fmt_num(0.85) == "0.85"

    def test_non_numeric(self):
        assert _fmt_num("abc") == "abc"

    def test_zero(self):
        assert _fmt_num(0) == "0"


class TestTextStyleCss:
    """_text_style_css: generate text-specific CSS properties."""

    def _text_layer(self, **overrides):
        base = {
            "width": 200, "height": 30,
            "text": "Hello",
            "text_style": {
                "font_size": 16,
                "color": "#333",
                "text_align": "left",
            },
        }
        base.update(overrides)
        return base

    def test_basic_output(self):
        css = _text_style_css(self._text_layer())
        assert "font-size:" in css
        assert "color: #333;" in css
        assert "text-align: left;" in css

    def test_font_size_capped(self):
        """Font size >= single_line_h * 0.85 → capped to 0.85 * h."""
        layer = self._text_layer(height=20)
        layer["text_style"]["font_size"] = 30
        css = _text_style_css(layer)
        # 30 >= 20*0.85=17 → capped to 17
        assert "font-size: 17px;" in css

    def test_multiline_line_height(self):
        """Multi-line text → line-height = single_line_h."""
        layer = self._text_layer(height=60, text="line1\rline2\rline3")
        layer["text_style"]["font_size"] = 12
        css = _text_style_css(layer)
        assert "line-height:" in css
        # 3 lines, h=60, single_line_h=20
        assert "20px" in css

    def test_multiline_with_leading(self):
        """Multi-line with leading < single_line_h → use leading."""
        layer = self._text_layer(height=60, text="A\rB")
        layer["text_style"]["font_size"] = 12
        layer["text_style"]["leading"] = 25
        css = _text_style_css(layer)
        assert "line-height: 25px;" in css

    def test_multiline_leading_exceeds(self):
        """Multi-line with leading > single_line_h → clamp to single_line_h."""
        layer = self._text_layer(height=60, text="A\rB")
        layer["text_style"]["font_size"] = 12
        layer["text_style"]["leading"] = 50
        css = _text_style_css(layer)
        # single_line_h=30, leading=50 > 30 → clamped to 30
        assert "line-height: 30px;" in css

    def test_no_color(self):
        layer = self._text_layer()
        layer["text_style"].pop("color")
        css = _text_style_css(layer)
        assert "color:" not in css

    def test_no_text_align(self):
        layer = self._text_layer()
        layer["text_style"].pop("text_align")
        css = _text_style_css(layer)
        assert "text-align:" not in css

    def test_newline_variants(self):
        """All newline variants (\\r\\n, \\r, \\n) are counted as line breaks."""
        layer = self._text_layer(height=100, text="A\r\nB\nC\rD")
        layer["text_style"]["font_size"] = 12
        css = _text_style_css(layer)
        # After normalisation: A\nB\nC\nD → 4 lines
        assert "line-height:" in css


# =====================================================================
# GroupRenderer.render (integration-level, with minimal context)
# =====================================================================

from targets.html.codegen.context import CodegenContext
from targets.html.codegen.layer_renderer import LayerRenderer


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
        html = self.renderer.render(layer, indent=2)
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
    """TextRenderer.render via LayerRenderer (integration)."""

    def setup_method(self):
        self.ctx = CodegenContext(
            psd_width=375, psd_height=812,
            output_dir=Path("/tmp/test_output"),
            psd_name="test",
        )
        self.renderer = LayerRenderer(self.ctx)

    def test_basic_text(self):
        layer = {
            "id": "layer-1", "name": "title", "type": "text",
            "left": 20, "top": 100, "width": 200, "height": 30,
            "opacity": 1, "blend_mode": "normal", "z_index": 5,
            "text": "Hello World",
            "text_style": {"font_size": 16, "color": "#000"},
        }
        html = self.renderer.render(layer, indent=2)
        assert '<span data-i18n-key=' in html
        assert "Hello World" in html
        assert 'data-type="text"' in html
        css = self.ctx.css_rules[0]
        # Text width is +2
        assert "width: 202px;" in css
        assert "font-size:" in css

    def test_text_html_escape(self):
        """Special chars in text content are escaped."""
        layer = {
            "id": "layer-2", "name": "code", "type": "text",
            "left": 0, "top": 0, "width": 100, "height": 20,
            "opacity": 1, "blend_mode": "normal", "z_index": 1,
            "text": "<script>alert(1)</script>",
            "text_style": {"font_size": 14},
        }
        html = self.renderer.render(layer, indent=2)
        assert "&lt;script&gt;" in html
        assert "<script>" not in html

    def test_text_newlines_to_br(self):
        """\\r → <br>."""
        layer = {
            "id": "layer-3", "name": "desc", "type": "text",
            "left": 0, "top": 0, "width": 100, "height": 40,
            "opacity": 1, "blend_mode": "normal", "z_index": 1,
            "text": "line1\rline2",
            "text_style": {"font_size": 12},
        }
        html = self.renderer.render(layer, indent=2)
        assert "<br>" in html

    def test_no_text(self):
        """Layer with no text → no <span>."""
        layer = {
            "id": "layer-4", "name": "empty", "type": "text",
            "left": 0, "top": 0, "width": 50, "height": 20,
            "opacity": 1, "blend_mode": "normal", "z_index": 1,
        }
        html = self.renderer.render(layer, indent=2)
        assert "<span" not in html

    def test_no_text_style(self):
        """Layer with no text_style → no font CSS."""
        layer = {
            "id": "layer-5", "name": "label", "type": "text",
            "left": 0, "top": 0, "width": 50, "height": 20,
            "opacity": 1, "blend_mode": "normal", "z_index": 1,
            "text": "hello",
        }
        html = self.renderer.render(layer, indent=2)
        css = self.ctx.css_rules[0]
        assert "font-size:" not in css


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
