# -*- coding: utf-8 -*-
"""文本图层渲染策略。

文字一律栅格化为图片：HTML 里不输出可编辑文本节点（``<span>``），
渲染方式与 :class:`ImageRenderer` 完全一致（``background-image`` 指向 PNG），
只把 ``data-type`` 标记为 ``"text"``，让 LayoutOptimizer / 开发者仍能识别
"这张图原本是文字图层"（布局优化按 data-type 区分内容与装饰）。

CSS / HTML 细节全部复用 :class:`ImageRenderer`，不在这里重复实现。
"""

from __future__ import annotations

from .base import register_renderer
from .image_renderer import ImageRenderer


@register_renderer("text")
class TextRenderer(ImageRenderer):
    """文本图层（栅格化为图片，保留 ``data-type="text"`` 标记）。

    渲染逻辑（定位 CSS / background-image / background-size 兜底）完全继承
    :class:`ImageRenderer`，唯一差异是本类的 ``data_type``。
    """

    data_type: str = "text"
