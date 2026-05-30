# -*- coding: utf-8 -*-
"""Tests for targets/html/codegen — HTMLGenerator, HtmlBuilder, CodegenContext."""

import json
from pathlib import Path

import pytest

from targets.html.codegen.context import CodegenContext
from targets.html.codegen.html_builder import HtmlBuilder, HtmlBuilderMixin
from targets.html.codegen.html_generator import HTMLGenerator
from targets.html.codegen.version import __version__


# =====================================================================
# CodegenContext
# =====================================================================

class TestCodegenContext:
    """CodegenContext: shared state container."""

    def test_default_namer(self):
        ctx = CodegenContext(
            psd_width=375, psd_height=812,
            output_dir=Path("/tmp/out"), psd_name="demo",
        )
        assert ctx.namer is not None
        assert ctx.css_rules == []

    def test_reset(self):
        ctx = CodegenContext(
            psd_width=375, psd_height=812,
            output_dir=Path("/tmp/out"), psd_name="demo",
        )
        ctx.css_rules.append(".foo { color: red; }")
        ctx.reset()
        assert ctx.css_rules == []

    def test_fields(self):
        ctx = CodegenContext(
            psd_width=1920, psd_height=1080,
            output_dir=Path("/var/out"), psd_name="banner",
        )
        assert ctx.psd_width == 1920
        assert ctx.psd_height == 1080
        assert ctx.psd_name == "banner"
        assert ctx.output_dir == Path("/var/out")


# =====================================================================
# HtmlBuilder
# =====================================================================

class TestHtmlBuilder:
    """HtmlBuilder: CSS / HTML / JS text assembly."""

    def setup_method(self):
        self.ctx = CodegenContext(
            psd_width=750, psd_height=1334,
            output_dir=Path("/tmp/out"), psd_name="landing",
        )
        self.builder = HtmlBuilder(self.ctx)

    def test_build_css_header(self):
        css = self.builder.build_css()
        assert f"PSD2HTML v{__version__}" in css
        assert "landing" in css
        assert "width: 750px;" in css
        assert "height: 1334px;" in css

    def test_build_css_includes_rules(self):
        self.ctx.css_rules.append(".btn__1 { color: red; }")
        css = self.builder.build_css()
        assert ".btn__1 { color: red; }" in css

    def test_build_css_media_query(self):
        css = self.builder.build_css()
        assert f"max-width: 750px" in css
        assert f"calc(100vw / 750)" in css

    def test_build_html_structure(self):
        html = self.builder.build_html("<div>content</div>\n")
        assert "<!DOCTYPE html>" in html
        assert '<html lang="zh-CN">' in html
        assert "<title>landing</title>" in html
        assert 'href="style.css"' in html
        assert 'id="canvas"' in html
        assert "<div>content</div>" in html
        assert 'src="main.js"' in html

    def test_build_js_content(self):
        js = self.builder.build_js()
        assert f"PSD2HTML v{__version__}" in js
        assert "setLanguage" in js
        assert "data-i18n-key" in js


# =====================================================================
# HTMLGenerator — file generation (integration with tmp_path)
# =====================================================================

class TestHTMLGeneratorGenerate:
    """HTMLGenerator.generate_html / generate_metadata / generate_readme."""

    def setup_method(self):
        pass

    def test_generate_html_creates_files(self, tmp_path):
        gen = HTMLGenerator(
            psd_width=375, psd_height=812,
            output_dir=tmp_path, psd_name="test_psd",
        )
        layers = [
            {
                "id": "layer-1", "name": "bg", "type": "image",
                "left": 0, "top": 0, "width": 375, "height": 812,
                "opacity": 1, "blend_mode": "normal", "z_index": 1,
                "image_path": "images/bg.png",
            },
        ]
        html_path = gen.generate_html(layers)

        # Files should exist
        assert (tmp_path / "index.html").exists()
        assert (tmp_path / "style.css").exists()
        assert (tmp_path / "main.js").exists()

        # HTML contains expected content
        html = (tmp_path / "index.html").read_text()
        assert "test_psd" in html
        assert 'id="canvas"' in html

        # CSS contains layer rule
        css = (tmp_path / "style.css").read_text()
        assert "position: absolute;" in css
        assert 'background-image: url("images/bg.png")' in css

    def test_generate_metadata(self, tmp_path):
        gen = HTMLGenerator(
            psd_width=375, psd_height=812,
            output_dir=tmp_path, psd_name="test",
        )
        layers = [{"id": "1", "name": "layer", "type": "image"}]
        gen.generate_metadata(layers, exported=5, skipped=2)

        meta_path = tmp_path / "metadata.json"
        assert meta_path.exists()
        data = json.loads(meta_path.read_text())
        assert data["version"] == __version__
        assert data["psd_name"] == "test"
        assert data["canvas"]["width"] == 375
        assert data["canvas"]["height"] == 812
        assert data["stats"]["exported"] == 5
        assert data["stats"]["skipped"] == 2
        assert data["stats"]["total"] == 7

    def test_generate_readme(self, tmp_path):
        gen = HTMLGenerator(
            psd_width=375, psd_height=812,
            output_dir=tmp_path, psd_name="demo",
        )
        gen.generate_readme(exported=10, skipped=3)

        readme = (tmp_path / "README.md").read_text()
        assert "demo" in readme
        assert "10" in readme
        assert "3" in readme
        assert "375 x 812" in readme
        assert __version__ in readme

    def test_generate_html_resets_context(self, tmp_path):
        """Each generate_html call resets css_rules and namer."""
        gen = HTMLGenerator(
            psd_width=375, psd_height=812,
            output_dir=tmp_path, psd_name="test",
        )
        layers = [
            {
                "id": "layer-1", "name": "a", "type": "image",
                "left": 0, "top": 0, "width": 10, "height": 10,
                "opacity": 1, "blend_mode": "normal", "z_index": 1,
            },
        ]
        gen.generate_html(layers)
        first_rules = len(gen.ctx.css_rules)

        # Second call should reset
        gen.generate_html(layers)
        assert len(gen.ctx.css_rules) == first_rules  # same count, not doubled


# =====================================================================
# HTMLGenerator — backward compat properties
# =====================================================================

class TestHTMLGeneratorCompat:
    """Backward-compatible properties and delegation methods."""

    def test_namer_property(self, tmp_path):
        gen = HTMLGenerator(375, 812, tmp_path, "test")
        from targets.html.codegen.naming import SimpleNamer
        assert isinstance(gen.namer, SimpleNamer)

    def test_psd_dimensions(self, tmp_path):
        gen = HTMLGenerator(1920, 1080, tmp_path, "banner")
        assert gen.psd_width == 1920
        assert gen.psd_height == 1080

    def test_output_dir_property(self, tmp_path):
        gen = HTMLGenerator(375, 812, tmp_path, "test")
        assert gen.output_dir == tmp_path

    def test_psd_name_property(self, tmp_path):
        gen = HTMLGenerator(375, 812, tmp_path, "demo")
        assert gen.psd_name == "demo"

    def test_css_rules_property(self, tmp_path):
        gen = HTMLGenerator(375, 812, tmp_path, "test")
        assert gen._css_rules == []
        gen.ctx.css_rules.append("test")
        assert gen._css_rules == ["test"]

    def test_render_layer_delegation(self, tmp_path):
        gen = HTMLGenerator(375, 812, tmp_path, "test")
        layer = {
            "id": "layer-1", "name": "test", "type": "image",
            "left": 0, "top": 0, "width": 50, "height": 50,
            "opacity": 1, "blend_mode": "normal", "z_index": 1,
        }
        html = gen._render_layer(layer)
        assert "layer-1" in html

    def test_build_css_delegation(self, tmp_path):
        gen = HTMLGenerator(375, 812, tmp_path, "test")
        css = gen._build_css()
        assert "PSD2HTML" in css

    def test_build_html_delegation(self, tmp_path):
        gen = HTMLGenerator(375, 812, tmp_path, "test")
        html = gen._build_html("<p>hi</p>")
        assert "<!DOCTYPE html>" in html

    def test_build_js_delegation(self, tmp_path):
        gen = HTMLGenerator(375, 812, tmp_path, "test")
        js = gen._build_js()
        assert "setLanguage" in js


# =====================================================================
# HTMLGenerator — multiple layers
# =====================================================================

class TestHTMLGeneratorMultipleLayers:
    """Integration test with multiple layers of different types."""

    def test_mixed_layers(self, tmp_path):
        gen = HTMLGenerator(375, 812, tmp_path, "mixed")
        layers = [
            {
                "id": "group-1", "name": "header", "type": "group",
                "left": 0, "top": 0, "width": 375, "height": 200,
                "opacity": 1, "blend_mode": "normal", "z_index": 1,
                "children": [
                    {
                        "id": "layer-1", "name": "logo", "type": "image",
                        "left": 10, "top": 10, "width": 100, "height": 50,
                        "opacity": 1, "blend_mode": "normal", "z_index": 2,
                        "image_path": "images/logo.png",
                    },
                    {
                        "id": "layer-2", "name": "标题", "type": "text",
                        "left": 120, "top": 20, "width": 200, "height": 30,
                        "opacity": 1, "blend_mode": "normal", "z_index": 3,
                        "text": "Welcome",
                        "text_style": {"font_size": 22, "color": "#fff"},
                    },
                ],
            },
        ]
        html_path = gen.generate_html(layers)
        html = Path(html_path).read_text()
        css = (tmp_path / "style.css").read_text()

        # Group
        assert 'data-type="group"' in html
        # Image
        assert 'data-type="image"' in html
        assert "logo.png" in css
        # Text
        assert 'data-type="text"' in html
        assert "Welcome" in html
        # Multiple CSS rules
        assert css.count("position: absolute;") >= 3

    def test_empty_layers_tree(self, tmp_path):
        gen = HTMLGenerator(375, 812, tmp_path, "empty")
        html_path = gen.generate_html([])
        html = Path(html_path).read_text()
        assert 'id="canvas"' in html
        css = (tmp_path / "style.css").read_text()
        # Only global styles, no layer rules
        assert "#canvas" in css
