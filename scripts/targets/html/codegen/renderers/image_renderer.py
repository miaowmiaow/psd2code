# -*- coding: utf-8 -*-
"""图片图层渲染策略。"""

from __future__ import annotations

from typing import Any, Optional

from ..escape import _esc
from .base import NodeRenderer, register_renderer
from .css_helpers import position_css_lines, read_png_size, semantic_css_class


@register_renderer("image")
class ImageRenderer(NodeRenderer):
    """普通图片图层。也作为 layer type 缺省回退。"""

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

        # 收集 CSS：class_name 可能是多类字符串（如 "bg__3 layer"），
        # CSS 选择器只取语义类（首个 token），role 类不参与选择。
        css_class = semantic_css_class(class_name)
        css = f'.{css_class} {{\n'
        css += position_css_lines(layer)

        if layer.get('image_path'):
            css += f'    background-image: url("{layer["image_path"]}");\n'
            # 仅在 PNG 实际像素 != CSS 容器尺寸时写 `100% 100%` 强制拉伸；
            # 像素恰好相等时省略 `background-size`，让浏览器走默认 `auto`，
            # 1:1 像素映射，最大限度保留 PSD 渲染时已有的边缘抗锯齿质量
            # （避免 `100% 100%` 触发的二次缩放管道破坏边缘像素）。
            if not self._png_matches_css(layer):
                css += f'    background-size: 100% 100%;\n'
            css += f'    background-repeat: no-repeat;\n'
            css += f'    background-position: left top;\n'

        css += f'}}'
        self.ctx.css_rules.append(css)

        html = f'{pad}<div id="{layer_id}" class="{class_name}" data-name="{_esc(layer["name"])}" data-type="image">\n'
        html += f'{pad}</div>\n'
        return html

    def _png_matches_css(self, layer: dict[str, Any]) -> bool:
        """PNG 实际像素是否恰好等于 CSS 容器（width/height）尺寸。

        相等时调用方应省略 `background-size`，让浏览器走默认 1:1 渲染。
        任一条件不满足（PNG 解析失败 / 路径不存在 / 尺寸不等）返回 False，
        调用方继续保留 `100% 100%` 作为兜底，不影响视觉。
        """
        rel = layer.get('image_path')
        if not rel:
            return False
        png_path = self.ctx.output_dir / rel
        size = read_png_size(png_path)
        if size is None:
            return False
        try:
            cw = int(layer['width'])
            ch = int(layer['height'])
        except (KeyError, TypeError, ValueError):
            return False
        return size == (cw, ch)
