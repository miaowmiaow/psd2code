# -*- coding: utf-8 -*-
"""图层导出决策链 —— Chain of Responsibility。

原 `LayerExporter.export_layers` 内部有一段 ~150 行的 if/elif 分支，
负责判断一个 item 是"剪切蒙版组 / 按钮组 / 可合并组 / 普通组 / 叶图层"
中的哪一种，并决定合并策略。

把这段分支提炼成一系列 Handler，每个 handler 负责单一决策：
- 先调用 can_handle(ctx) 判断是否适用
- 若适用，调用 handle(ctx) 返回 0..N 个 layer_info，并告知是否已"吃掉"输入

这样：
- 每条决策分支独立可测、可替换
- 新增一种合并策略只需加一个 Handler 并 register
- LayerExporter.export_layers 退化为"run handlers in order"

handler **不持有状态**，所有副作用（_z_counter、exported_count、打印）
仍走 LayerExporter.* 方法本身。不破坏既有混合渲染与子组 composite 约束。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .layer_exporter import LayerExporter


@dataclass
class HandlerContext:
    """单个 item 的导出决策上下文。"""

    exporter: "LayerExporter"
    item: Any  # 单图层 或 (base_layer, [clipped...]) 元组
    depth: int
    parent_name: str
    parent_left: int
    parent_top: int
    parent_clip_bbox: Optional[tuple[int, int, int, int]]
    bg_layer_ids: set[int] = field(default_factory=set)


@dataclass
class HandlerResult:
    """handler 执行结果。"""

    #: 本次决策产出的 layer_info 列表，追加到 export_layers 的 result
    produced: list[dict[str, Any]] = field(default_factory=list)
    #: 是否终止链（已处理此 item），True 则后续 handler 跳过
    handled: bool = False


class LayerHandler(ABC):
    """图层导出决策节点。"""

    @abstractmethod
    def can_handle(self, ctx: HandlerContext) -> bool:
        ...

    @abstractmethod
    def handle(self, ctx: HandlerContext) -> HandlerResult:
        ...


# ------------------------------------------------------------------
# 具体 Handler：每条只负责一条决策分支
# ------------------------------------------------------------------


class BackgroundSkipHandler(LayerHandler):
    """顶层已合并过的背景图层直接跳过。"""

    def can_handle(self, ctx: HandlerContext) -> bool:
        if not ctx.bg_layer_ids:
            return False
        if isinstance(ctx.item, tuple):
            return id(ctx.item[0]) in ctx.bg_layer_ids
        return id(ctx.item) in ctx.bg_layer_ids

    def handle(self, ctx: HandlerContext) -> HandlerResult:
        # 不产出，也不再继续
        return HandlerResult(produced=[], handled=True)


class ClippingGroupHandler(LayerHandler):
    """剪切蒙版组：base_layer + 多个 clipped_layers。"""

    def can_handle(self, ctx: HandlerContext) -> bool:
        return isinstance(ctx.item, tuple)

    def handle(self, ctx: HandlerContext) -> HandlerResult:
        exp = ctx.exporter
        base_layer, clipped_layers = ctx.item
        depth = ctx.depth
        parent_name = ctx.parent_name
        parent_left = ctx.parent_left
        parent_top = ctx.parent_top
        parent_clip_bbox = ctx.parent_clip_bbox

        base_name = base_layer.name or 'merged'

        # 隐藏/透明 base → 跳过整组
        if not base_layer.visible:
            exp.skipped_count += 1 + len(clipped_layers)
            print(f"{'  ' * depth}🚫 {base_name} (隐藏剪切蒙版组，已跳过)")
            return HandlerResult(handled=True)
        if base_layer.opacity == 0:
            exp.skipped_count += 1 + len(clipped_layers)
            print(f"{'  ' * depth}🚫 {base_name} (opacity=0剪切蒙版组，已跳过)")
            return HandlerResult(handled=True)

        produced: list[dict[str, Any]] = []
        full_name = f'{parent_name}/{base_name}' if parent_name else base_name

        # base 是组：先试合并，否则递归
        if base_layer.is_group():
            can_merge = exp._can_merge_group(base_layer)

            if can_merge:
                merged = exp._merge_group_as_single_image(
                    base_layer, base_name, full_name,
                    depth, parent_left, parent_top,
                    clip_bbox=parent_clip_bbox,
                )
                if merged:
                    produced.append(merged)
                    for cl in clipped_layers:
                        cl_name = cl.name or 'clipped'
                        cl_full = f'{parent_name}/{cl_name}' if parent_name else cl_name
                        li = exp._export_single_layer(
                            cl, cl_name, cl_full, depth,
                            parent_left, parent_top, clip_bbox=parent_clip_bbox,
                        )
                        if li:
                            produced.append(li)
                    return HandlerResult(produced=produced, handled=True)
                print(f"{'  ' * depth}  ⚠️  合并失败，回退到逐层导出")

            # 回退：递归处理组
            print(f"{'  ' * depth}📁 {base_name} (组，有剪切蒙版附着)")

            grp_abs_left = base_layer.left
            grp_abs_top = base_layer.top

            children = exp.export_layers(
                base_layer, full_name, depth + 1,
                parent_left=grp_abs_left, parent_top=grp_abs_top,
            )

            exp._z_counter += 1
            from .layer_exporter import BLEND_MODES
            group_info: dict[str, Any] = {
                'id': f'group-{exp._z_counter}',
                'name': base_name,
                'full_name': full_name,
                'type': 'group',
                'left': grp_abs_left - parent_left,
                'top': grp_abs_top - parent_top,
                'width': max(base_layer.width, 0),
                'height': max(base_layer.height, 0),
                'opacity': base_layer.opacity / 255.0,
                'blend_mode': BLEND_MODES.get(str(base_layer.blend_mode), 'normal'),
                'z_index': exp._z_counter,
                'children': children,
            }
            produced.append(group_info)
            for cl in clipped_layers:
                cl_name = cl.name or 'clipped'
                cl_full = f'{parent_name}/{cl_name}' if parent_name else cl_name
                li = exp._export_single_layer(
                    cl, cl_name, cl_full, depth,
                    parent_left, parent_top, clip_bbox=parent_clip_bbox,
                )
                if li:
                    produced.append(li)
            return HandlerResult(produced=produced, handled=True)

        # base 是普通图层：合并剪切
        merged = exp._merge_clipping_group(
            base_layer, clipped_layers,
            parent_name, depth,
            parent_left, parent_top,
        )
        if merged:
            produced.append(merged)
        else:
            # 回退：单独导出每个图层
            li = exp._export_single_layer(
                base_layer, base_name, full_name, depth,
                parent_left, parent_top, clip_bbox=parent_clip_bbox,
            )
            if li:
                produced.append(li)
            for cl in clipped_layers:
                cl_name = cl.name or 'clipped'
                cl_full = f'{parent_name}/{cl_name}' if parent_name else cl_name
                li = exp._export_single_layer(
                    cl, cl_name, cl_full, depth,
                    parent_left, parent_top, clip_bbox=parent_clip_bbox,
                )
                if li:
                    produced.append(li)
        return HandlerResult(produced=produced, handled=True)


class InvisibleLayerHandler(LayerHandler):
    """跳过隐藏 / opacity=0 的普通图层。"""

    def can_handle(self, ctx: HandlerContext) -> bool:
        if isinstance(ctx.item, tuple):
            return False
        layer = ctx.item
        return (not layer.visible) or (layer.opacity == 0)

    def handle(self, ctx: HandlerContext) -> HandlerResult:
        exp = ctx.exporter
        layer = ctx.item
        layer_name = layer.name or 'Layer'
        exp.skipped_count += 1
        pad = '  ' * ctx.depth
        if not layer.visible:
            print(f"{pad}🚫 {layer_name} (隐藏，已跳过)")
        else:
            print(f"{pad}🚫 {layer_name} (opacity=0，已跳过)")
        return HandlerResult(handled=True)


class GroupHandler(LayerHandler):
    """普通组（非剪切蒙版）：先试 composite 合并，否则递归。"""

    def can_handle(self, ctx: HandlerContext) -> bool:
        if isinstance(ctx.item, tuple):
            return False
        return ctx.item.is_group()

    def handle(self, ctx: HandlerContext) -> HandlerResult:
        exp = ctx.exporter
        layer = ctx.item
        layer_name = layer.name or 'Layer'
        full_name = f'{ctx.parent_name}/{layer_name}' if ctx.parent_name else layer_name
        depth = ctx.depth
        parent_left = ctx.parent_left
        parent_top = ctx.parent_top
        parent_clip_bbox = ctx.parent_clip_bbox

        produced: list[dict[str, Any]] = []

        can_merge = exp._can_merge_group(layer)

        if can_merge:
            merged = exp._merge_group_as_single_image(
                layer, layer_name, full_name,
                depth, parent_left, parent_top,
                clip_bbox=parent_clip_bbox,
            )
            if merged:
                produced.append(merged)
                return HandlerResult(produced=produced, handled=True)
            print(f"{'  ' * depth}📁 {layer_name} (合并失败，回退逐层导出)")

        # 非文本合并：组内存在文本 + 非文本时，
        # 将非文本图层合并为单张背景图，文本图层独立保留
        # 注意：传入组自身的绝对坐标作为 parent_left/parent_top，
        # 这样返回的 layer_info 中 left/top 就是**相对组内部**的坐标
        # （与文本子图层共用同一相对坐标基准）
        merged_bg_info: dict[str, Any] | None = None
        if not can_merge and exp._can_merge_group_non_text(layer):
            merged_bg_info = exp._merge_group_non_text_as_image(
                layer, layer_name, full_name,
                depth,
                parent_left=layer.left,
                parent_top=layer.top,
                clip_bbox=parent_clip_bbox,
            )
            if merged_bg_info is None:
                print(
                    f"{'  ' * depth}📁 {layer_name} "
                    f"(非文本合并失败，回退逐层导出)"
                )

        print(f"{'  ' * depth}📁 {layer_name} (组)")

        # 组的画布绝对坐标
        from config import Config
        grp_abs_left_orig = layer.left
        grp_abs_top_orig = layer.top
        grp_width_orig = layer.width
        grp_height_orig = layer.height

        grp_overflow = False
        grp_abs_left = grp_abs_left_orig
        grp_abs_top = grp_abs_top_orig
        grp_width = grp_width_orig
        grp_height = grp_height_orig

        if Config.CONSTRAIN_GROUP_TO_CANVAS:
            if (grp_abs_left_orig < 0 or grp_abs_top_orig < 0 or
                grp_abs_left_orig + grp_width_orig > exp.canvas_width or
                grp_abs_top_orig + grp_height_orig > exp.canvas_height):
                grp_overflow = True
                grp_abs_left = max(0, grp_abs_left_orig)
                grp_abs_top = max(0, grp_abs_top_orig)
                constrained_right = min(exp.canvas_width, grp_abs_left_orig + grp_width_orig)
                constrained_bottom = min(exp.canvas_height, grp_abs_top_orig + grp_height_orig)
                grp_width = constrained_right - grp_abs_left
                grp_height = constrained_bottom - grp_abs_top
                print(f"{'  ' * depth}  ⚠️  组超出画布，约束 bbox: "
                      f"({grp_abs_left_orig},{grp_abs_top_orig},{grp_width_orig}x{grp_height_orig}) → "
                      f"({grp_abs_left},{grp_abs_top},{grp_width}x{grp_height})")

        group_clip_bbox = (grp_abs_left, grp_abs_top,
                           grp_abs_left + grp_width, grp_abs_top + grp_height)

        # 若已生成非文本合并背景图：临时隐藏所有**直接**非文本可见图层，
        # 仅让 export_layers 递归导出文本图层，避免重复导出。
        bg_hidden_saved: list[tuple[Any, bool]] = []
        if merged_bg_info is not None:
            non_text_layers: list[Any] = []
            for c in layer:
                if not c.visible or c.opacity == 0:
                    continue
                if c.is_group():
                    continue
                kind = str(c.kind) if hasattr(c, 'kind') else ''
                if 'type' not in kind.lower():
                    non_text_layers.append(c)
            for nt in non_text_layers:
                bg_hidden_saved.append((nt, nt.visible))
                try:
                    nt.visible = False
                except Exception:
                    pass

        try:
            children = exp.export_layers(
                layer, full_name, depth + 1,
                parent_left=grp_abs_left_orig,
                parent_top=grp_abs_top_orig,
                parent_clip_bbox=group_clip_bbox,
            )
        finally:
            for nt, vis in bg_hidden_saved:
                try:
                    nt.visible = vis
                except Exception:
                    pass

        # 非文本合并背景图作为组的第一个子元素（最底层 z-index）
        if merged_bg_info is not None:
            children = [merged_bg_info] + children


        if grp_overflow:
            offset_x = grp_abs_left - grp_abs_left_orig
            offset_y = grp_abs_top - grp_abs_top_orig
            if offset_x != 0 or offset_y != 0:
                print(f"{'  ' * depth}  🔧 调整子图层坐标偏移: ({-offset_x}, {-offset_y})")
                exp._adjust_children_offset(children, -offset_x, -offset_y)

        exp._z_counter += 1
        from .layer_exporter import BLEND_MODES
        group_info: dict[str, Any] = {
            'id': f'group-{exp._z_counter}',
            'name': layer_name,
            'full_name': full_name,
            'type': 'group',
            'left': grp_abs_left - parent_left,
            'top': grp_abs_top - parent_top,
            'width': max(grp_width, 0),
            'height': max(grp_height, 0),
            'opacity': layer.opacity / 255.0,
            'blend_mode': BLEND_MODES.get(str(layer.blend_mode), 'normal'),
            'z_index': exp._z_counter,
            'children': children,
        }
        produced.append(group_info)
        return HandlerResult(produced=produced, handled=True)


class LeafLayerHandler(LayerHandler):
    """叶图层：普通图片 / 文本。缺省 handler。"""

    def can_handle(self, ctx: HandlerContext) -> bool:
        return not isinstance(ctx.item, tuple)

    def handle(self, ctx: HandlerContext) -> HandlerResult:
        exp = ctx.exporter
        layer = ctx.item
        layer_name = layer.name or 'Layer'
        full_name = f'{ctx.parent_name}/{layer_name}' if ctx.parent_name else layer_name
        li = exp._export_single_layer(
            layer, layer_name, full_name, ctx.depth,
            ctx.parent_left, ctx.parent_top,
            clip_bbox=ctx.parent_clip_bbox,
        )
        produced = [li] if li else []
        return HandlerResult(produced=produced, handled=True)


# ------------------------------------------------------------------
# Pipeline：按顺序执行 Handler
# ------------------------------------------------------------------


#: 决策顺序很重要：跳过已合并背景 → 剪切蒙版组 → 跳过隐藏 → 组 → 叶图层
DEFAULT_HANDLERS: list[LayerHandler] = [
    BackgroundSkipHandler(),
    ClippingGroupHandler(),
    InvisibleLayerHandler(),
    GroupHandler(),
    LeafLayerHandler(),
]


def run_handlers(
    ctx: HandlerContext,
    handlers: list[LayerHandler] = DEFAULT_HANDLERS,
) -> list[dict[str, Any]]:
    """按顺序执行 handlers，首个 handled=True 终止，返回累积 produced。"""
    for h in handlers:
        if h.can_handle(ctx):
            r = h.handle(ctx)
            if r.handled:
                return r.produced
    return []
