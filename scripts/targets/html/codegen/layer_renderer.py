# -*- coding: utf-8 -*-
"""图层渲染器（Composition）。

职责：
- 持有 CodegenContext 共享状态
- 按图层 type 分派到对应 NodeRenderer（Strategy 模式，从 RendererRegistry 解析）
"""

from __future__ import annotations

from typing import Any, Optional

from .context import CodegenContext
from .renderers import RendererRegistry


class LayerRenderer:
    """图层渲染协调者。

    对外提供：
    - render(layer, indent, parent, siblings) —— 统一入口（== 原 _render_layer）
    """

    def __init__(self, ctx: CodegenContext) -> None:
        self.ctx = ctx
        # 实例化每种类型的策略（持有对 ctx 和 self 的引用）
        self._strategies = {
            lt: RendererRegistry.get(lt)(ctx, self)
            for lt in RendererRegistry.types()
        }

    # ------------------------------------------------------------------
    # 主入口（Strategy 分派）
    # ------------------------------------------------------------------

    def render(
        self,
        layer: dict[str, Any],
        indent: int = 2,
        parent: Optional[dict[str, Any]] = None,
        siblings: Optional[list[dict[str, Any]]] = None,
    ) -> str:
        siblings = siblings or []
        ltype = layer.get('type', 'image')

        # 类名生成：统一在此处完成，避免每个策略重复
        class_name = self.ctx.namer.generate_class_name(layer, parent, siblings)
        layer['class_name'] = class_name  # 缓存供父层消费

        strategy = self._strategies.get(ltype) or self._strategies['image']
        return strategy.render(layer, indent, parent, siblings, class_name)
