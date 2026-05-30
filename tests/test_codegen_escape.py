# -*- coding: utf-8 -*-
"""Tests for targets/html/codegen/escape.py — HTML attribute escaping."""

from targets.html.codegen.escape import _esc


class TestEsc:
    """_esc: HTML attribute-safe escaping."""

    def test_ampersand(self):
        assert _esc("A&B") == "A&amp;B"

    def test_double_quote(self):
        assert _esc('say "hi"') == 'say &quot;hi&quot;'

    def test_less_than(self):
        assert _esc("a<b") == "a&lt;b"

    def test_greater_than(self):
        assert _esc("a>b") == "a&gt;b"

    def test_all_entities(self):
        assert _esc('&<>"') == "&amp;&lt;&gt;&quot;"

    def test_no_escape_needed(self):
        assert _esc("hello world 123") == "hello world 123"

    def test_empty_string(self):
        assert _esc("") == ""

    def test_chinese_text(self):
        assert _esc("你好<世界>") == "你好&lt;世界&gt;"

    def test_multiple_same_char(self):
        assert _esc("&&&") == "&amp;&amp;&amp;"

    def test_entity_in_attribute_context(self):
        """Typical usage: layer name in data-name attribute."""
        assert _esc('图层 "top" & <bg>') == '图层 &quot;top&quot; &amp; &lt;bg&gt;'
