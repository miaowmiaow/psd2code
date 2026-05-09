# -*- coding: utf-8 -*-
"""NodeRenderer 抽象 + RendererRegistry。

设计模式：
- Strategy：每种图层类型是一个独立的渲染策略
- Registry / Factory：通过 @register_renderer("group") 自注册
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from ..context import CodegenContext
    from ..layer_renderer import LayerRenderer


class NodeRenderer(ABC):
    """单一图层类型的渲染策略。"""

    #: 本策略处理的图层 type 字段值
    layer_type: str = ""

    def __init__(self, ctx: "CodegenContext", host: "LayerRenderer") -> None:
        self.ctx = ctx
        # host 提供递归入口 host.render(child, ...)，避免策略之间直接依赖
        self.host = host

    @abstractmethod
    def render(
        self,
        layer: dict[str, Any],
        indent: int,
        parent: Optional[dict[str, Any]],
        siblings: list[dict[str, Any]],
        class_name: str,
    ) -> str:
        ...


class RendererRegistry:
    """图层类型 → Renderer 类 的注册表。"""

    _registry: dict[str, type[NodeRenderer]] = {}

    @classmethod
    def register(cls, layer_type: str, renderer_cls: type[NodeRenderer]) -> None:
        cls._registry[layer_type] = renderer_cls

    @classmethod
    def get(cls, layer_type: str) -> type[NodeRenderer]:
        # 缺省回退到 image（与历史行为一致：layer.get('type', 'image')）
        return cls._registry.get(layer_type) or cls._registry["image"]

    @classmethod
    def types(cls) -> list[str]:
        return list(cls._registry.keys())


def register_renderer(layer_type: str) -> Callable[[type[NodeRenderer]], type[NodeRenderer]]:
    """类装饰器：把 NodeRenderer 子类自注册到 RendererRegistry。"""

    def _wrap(renderer_cls: type[NodeRenderer]) -> type[NodeRenderer]:
        renderer_cls.layer_type = layer_type
        RendererRegistry.register(layer_type, renderer_cls)
        return renderer_cls

    return _wrap
