# -*- coding: utf-8 -*-
"""图层渲染器（Strategy + Registry）。

每种图层类型（group/image/text）对应一个 NodeRenderer 实现。
通过 @register_renderer 自注册到 RendererRegistry。
"""

from .base import NodeRenderer, RendererRegistry, register_renderer

# 触发自注册
from . import group_renderer   # noqa: F401
from . import image_renderer   # noqa: F401
from . import text_renderer    # noqa: F401

__all__ = ["NodeRenderer", "RendererRegistry", "register_renderer"]
