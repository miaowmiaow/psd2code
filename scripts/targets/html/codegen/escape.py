# -*- coding: utf-8 -*-
"""HTML 属性/文本转义工具。"""


def _esc(text: str) -> str:
    """简单的 HTML 属性转义"""
    return text.replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
