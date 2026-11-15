# -*- coding: utf-8 -*-
"""Tests for .codebuddy/skills/psd2code-relayout/scripts/templatize.py

只覆盖纯分析逻辑（不需要浏览器）；模板化的像素验收由 verify_page.py 负责。
"""

import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

_SKILL_SCRIPTS = Path(__file__).resolve().parents[1] / ".codebuddy/skills/psd2code-relayout/scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

from templatize import analyze, parse_css, verdict  # noqa: E402

CSS = """
.tag { position: relative; z-index: 10; width: 99px; height: 44px; margin-left: 4px; }
.tag-bg { position: absolute; left: 0px; top: 0px; width: 97px; height: 42px; background: url("images/a.png") no-repeat; }
.tag-text { position: absolute; left: 13px; top: 12px; z-index: 11; width: 74px; height: 19px; }
.deep { position: absolute; left: 0px; top: 0px; z-index: 20; width: 90px; height: 40px; background: url("images/b.png") no-repeat; }
"""

HTML_SAFE = """
<div class="tag"><div class="tag-bg"></div><span>+10</span></div>
<div class="tag"><div class="tag-bg"></div><span>+10</span></div>
"""

# 文本嵌在两层内
HTML_DEEP = """
<div class="tag"><div class="tag-bg"><div class="tag-text"><span>+10</span></div></div></div>
<div class="tag"><div class="tag-bg"><div class="tag-text"><span>+10</span></div></div></div>
"""

# 子层 z(20) 高于文本（静态，视为 0）
HTML_Z_RISK = """
<div class="tag"><div class="deep"></div><span>+10</span></div>
<div class="tag"><div class="deep"></div><span>+10</span></div>
"""


def _analyze(html: str):
    return analyze(BeautifulSoup(html, "html.parser"), parse_css(CSS), class_filter="tag")


class TestParseCss:
    def test_splits_declarations(self):
        rules = parse_css(CSS)
        assert rules["tag"]["width"] == "99px"
        assert rules["tag-bg"]["left"] == "0px"


class TestAnalyze:
    def test_collects_layers_and_text(self):
        nodes = _analyze(HTML_SAFE)
        assert len(nodes) == 2
        first = nodes[0]
        assert [l.cls for l in first.layers] == ["tag-bg"]
        assert first.text == "+10"
        assert first.text_depth == 0

    def test_detects_deep_text(self):
        nodes = _analyze(HTML_DEEP)
        assert nodes[0].text_depth == 2
        assert nodes[0].text_container_cls == "tag-text"

    def test_ignores_leaf_layers(self):
        # 没有子 div 的元素不参与模板化
        html = '<div class="tag"><span>x</span></div>'
        assert _analyze(html) == []


class TestVerdict:
    def test_safe_when_single_layer_and_lower_z(self):
        safe, reason = verdict(_analyze(HTML_SAFE))
        assert safe, reason

    def test_safe_but_warns_on_deep_text(self):
        safe, reason = verdict(_analyze(HTML_DEEP))
        assert safe
        assert "extract" in reason

    def test_rejects_when_layer_z_above_text(self):
        # .deep 的 z=20，文本是静态节点（视为 0）→ 背景条绘制在文字之上，不能合并
        safe, reason = verdict(_analyze(HTML_Z_RISK))
        assert not safe
        assert "z=" in reason

    def test_rejects_when_layer_wraps_images(self):
        """子层内部还有图时压平会丢图（实测：领奖 .rect-7 内含 13 个 div）。"""
        css = CSS + '\n.inner-img { position:absolute; left:0px; top:0px; width:10px; height:10px; background: url("images/c.png") no-repeat; }'
        html = (
            '<div class="tag"><div class="tag-bg">'
            '<div class="inner-img"></div><span>+10</span>'
            '</div></div>'
        )
        nodes = analyze(BeautifulSoup(html, "html.parser"), parse_css(css), class_filter="tag")
        safe, reason = verdict(nodes)
        assert not safe
        assert "图片" in reason

    def test_rejects_when_layer_wraps_multiple_texts(self):
        html = '<div class="tag"><div class="tag-bg"><span>a</span><span>b</span></div></div>'
        nodes = analyze(BeautifulSoup(html, "html.parser"), parse_css(CSS), class_filter="tag")
        safe, reason = verdict(nodes)
        assert not safe
        assert "文本" in reason

    def test_rejects_multi_layer(self):
        html = '<div class="tag"><div class="tag-bg"></div><div class="tag-bg"></div></div>'
        safe, reason = verdict(_analyze(html))
        assert not safe
        assert "子层数" in reason

    def test_rejects_inconsistent_geometry(self):
        css = CSS + "\n.tag-2 { position: relative; width: 120px; height: 44px; }"
        html = HTML_SAFE + '<div class="tag-2"><div class="tag-bg"></div><span>+10</span></div>'
        nodes = analyze(BeautifulSoup(html, "html.parser"), parse_css(css), class_filter=None)
        picked = [n for n in nodes if n.cls in ("tag", "tag-2")]
        safe, reason = verdict(picked)
        assert not safe
        assert "几何" in reason
