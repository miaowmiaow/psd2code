# -*- coding: utf-8 -*-
"""Tests for scripts/tools/ai_relayout.py — 候选筛选、合成与落盘（不含浏览器校验）。"""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from PIL import Image

from common.css_utils import parse_css_to_dict
from tools.ai_relayout import (
    apply_changes,
    canvas_size,
    collect_candidates,
    synthesize,
)

CSS_BASE = """
* { margin: 0; padding: 0; }
#canvas { width: 750px; height: 1334px; }
.grp { position: relative; width: 40px; height: 40px; }
.a { position: absolute; left: 0px; top: 0px; z-index: 1; width: 40px; height: 40px; background: url("images/a.png") no-repeat; }
.b { position: absolute; left: 5px; top: 5px; z-index: 2; width: 10px; height: 10px; background: url("images/b.png") no-repeat; }
"""

HTML_BASE = (
    '<div class="grp layer-group">'
    '<div class="a layer"></div>'
    '<div class="b layer"></div>'
    "</div>"
)


@pytest.fixture()
def assets(tmp_path: Path):
    """构造最小产物：两张图片 + 对应 CSS 规则。"""
    images = tmp_path / "images"
    images.mkdir()
    Image.new("RGBA", (40, 40), (255, 0, 0, 255)).save(images / "a.png")
    Image.new("RGBA", (10, 10), (0, 0, 255, 255)).save(images / "b.png")
    return tmp_path, images, parse_css_to_dict(CSS_BASE)


# ---------------------------------------------------------------------------
# canvas_size
# ---------------------------------------------------------------------------

class TestCanvasSize:
    def test_parses_canvas_block(self):
        assert canvas_size(CSS_BASE) == (750, 1334)

    def test_falls_back_when_missing(self):
        assert canvas_size(".x { color: red; }", default=(320, 480)) == (320, 480)


# ---------------------------------------------------------------------------
# collect_candidates
# ---------------------------------------------------------------------------

class TestCollectCandidates:
    def test_accepts_pure_image_group(self, assets):
        _, images, rules = assets
        cands = collect_candidates(BeautifulSoup(HTML_BASE, "html.parser"), rules, images)

        assert len(cands) == 1
        assert cands[0].ok, cands[0].skip_reason
        assert [layer.css_class for layer in cands[0].layers] == ["a", "b"]

    def test_skips_group_with_text(self, assets):
        _, images, rules = assets
        html = HTML_BASE.replace(
            "</div>", '<div class="t layer"><span>x</span></div></div>'
        )
        cand = collect_candidates(BeautifulSoup(html, "html.parser"), rules, images)[0]

        assert not cand.ok
        assert "文本" in (cand.skip_reason or "")

    def test_skips_group_with_own_background(self, assets):
        _, images, _ = assets
        css = CSS_BASE.replace(
            ".grp {", '.grp { background: url("images/a.png") no-repeat;'
        )
        rules = parse_css_to_dict(css)
        cand = collect_candidates(BeautifulSoup(HTML_BASE, "html.parser"), rules, images)[0]

        assert not cand.ok
        assert "背景" in (cand.skip_reason or "")

    def test_skips_non_unique_class(self, assets):
        _, images, rules = assets
        html = HTML_BASE + '<div class="a layer"></div>'
        cand = collect_candidates(BeautifulSoup(html, "html.parser"), rules, images)[0]

        assert not cand.ok
        assert "唯一" in (cand.skip_reason or "")

    def test_skips_missing_image_file(self, assets):
        tmp_path, _, rules = assets
        empty = tmp_path / "no_images"
        empty.mkdir()
        cand = collect_candidates(BeautifulSoup(HTML_BASE, "html.parser"), rules, empty)[0]

        assert not cand.ok
        assert "缺失" in (cand.skip_reason or "")


# ---------------------------------------------------------------------------
# synthesize / apply_changes
# ---------------------------------------------------------------------------

class TestSynthesizeAndApply:
    def test_synthesize_uses_union_bbox(self, assets):
        _, images, rules = assets
        cand = collect_candidates(BeautifulSoup(HTML_BASE, "html.parser"), rules, images)[0]

        synthesize(cand, images, "merged.png")

        assert (cand.merged_width, cand.merged_height) == (40, 40)
        merged = Image.open(images / "merged.png")
        assert merged.size == (40, 40)
        # .b 只覆盖左上 10×10，右上角应仍是 .a 的红色
        assert merged.getpixel((35, 35))[:3] == (255, 0, 0)
        # .b 位于 (5,5)~(15,15)，中心应为蓝色
        assert merged.getpixel((7, 7))[:3] == (0, 0, 255)

    def test_apply_changes_rewrites_css_and_removes_nodes(self, assets):
        _, images, rules = assets
        soup = BeautifulSoup(HTML_BASE, "html.parser")
        cand = collect_candidates(soup, rules, images)[0]
        synthesize(cand, images, "merged.png")
        cand.status = "merged"

        new_css, new_html, removed = apply_changes(soup, CSS_BASE, [cand])

        assert removed == 1
        assert ".b {" not in new_css
        assert "images/merged.png" in new_css
        assert 'class="b layer"' not in new_html
        assert 'class="a layer"' in new_html

    def test_apply_changes_ignores_non_merged(self, assets):
        _, images, rules = assets
        soup = BeautifulSoup(HTML_BASE, "html.parser")
        cand = collect_candidates(soup, rules, images)[0]
        cand.status = "rejected"

        new_css, new_html, removed = apply_changes(soup, CSS_BASE, [cand])

        assert removed == 0
        assert new_css == CSS_BASE
        assert 'class="b layer"' in new_html
