# -*- coding: utf-8 -*-
"""文本图层渲染策略。"""

from __future__ import annotations

from typing import Any, Optional

from ..escape import _esc
from .base import NodeRenderer, register_renderer
from .css_helpers import position_css_lines, semantic_css_class


def _fmt_num(v: float) -> str:
    """格式化 CSS 数字字面量：整数走整数；小数最多保留 2 位且去尾随 0。

    专治 ``font_size = h * 0.85`` 这类浮点运算产生的 ``22.099999999999998``
    超长尾数，避免 CSS 出现 15 位小数噪声。
    """
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip('0').rstrip('.')


def _text_style_css(layer: dict[str, Any]) -> str:
    """生成文本图层的 CSS 属性片段。"""
    s = layer['text_style']
    css = ''

    font_size = s.get('font_size', 16)
    h = layer['height']
    # 视觉兜底（重要！不要随意放宽）：
    # 浏览器字体（PingFang/Arial 等）比 PSD 设计字体宽，且 PSD 允许文字溢出 bbox
    # 渲染（PS 的 bbox 是紧贴文字的小框），CSS 里 height 是硬约束，必须保证
    # font-size × line-height × 行数 不超过 height，否则多段文本会上下重叠。
    # 多行文本：用硬换行符（PSD 主要用 \r）估算行数。
    text = layer.get('text', '') or ''
    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    line_count = max(1, normalized.count('\n') + 1)
    single_line_h = h / line_count if line_count > 0 else h

    # 字号兜底：≥ 单行高度 * 0.85 时压到 0.85*单行高度
    # （与 text_extractor.extract_text_info 保持一致）
    if font_size >= single_line_h * 0.85:
        font_size = single_line_h * 0.85

    css += f'    font-size: {_fmt_num(font_size)}px;\n'

    has_multiline = '\r' in text or '\n' in text
    leading = s.get('leading')
    if has_multiline:
        # 多行强制写 line-height = single_line_h，避免 PSD 的 leading > single_line_h
        # 时多行总高溢出 bbox（典型：领奖.psd layer-88 h=53, leading=32, 2 行 = 64 > 53）
        effective_leading = single_line_h
        if leading and leading > 0:
            effective_leading = min(leading, single_line_h)
        css += f'    line-height: {_fmt_num(effective_leading)}px;\n'
    elif leading and leading <= h:
        # 单行场景：保留 PSD leading（如有）
        pass

    if s.get('color'):
        css += f'    color: {s["color"]};\n'
    if s.get('text_align'):
        css += f'    text-align: {s["text_align"]};\n'

    return css


@register_renderer("text")
class TextRenderer(NodeRenderer):
    """文本图层（保留为可编辑文本）。"""

    def render(
        self,
        layer: dict[str, Any],
        indent: int,
        parent: Optional[dict[str, Any]],
        siblings: list[dict[str, Any]],
        class_name: str,
    ) -> str:
        pad = '    ' * indent
        layer_id = layer['id']

        # 文本图层宽度 +2（历史行为，避免末尾字符被裁）
        w = layer["width"] + 2

        # class_name 是多类字符串（如 "title__5 layer"），CSS 选择器只取首个语义类
        css_class = semantic_css_class(class_name)
        css = f'.{css_class} {{\n'
        css += position_css_lines(layer, width=w)

        if layer.get('text_style'):
            css += _text_style_css(layer)

        css += f'}}'
        self.ctx.css_rules.append(css)

        html = f'{pad}<div id="{layer_id}" class="{class_name}" data-name="{_esc(layer["name"])}" data-type="text">\n'

        if layer.get('text'):
            escaped = layer['text'].replace('<', '&lt;').replace('>', '&gt;')
            escaped = escaped.replace('\r\n', '<br>').replace('\r', '<br>').replace('\n', '<br>')
            i18n_key = _esc(layer['name'])
            html += f'{pad}    <span data-i18n-key="{i18n_key}">{escaped}</span>\n'

        html += f'{pad}</div>\n'
        return html
