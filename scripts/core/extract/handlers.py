# -*- coding: utf-8 -*-
"""图层导出决策链 —— Chain of Responsibility（纯解析版）。

每个 PSD 图层一一映射到一个 layer_info（叶图层）或 group_info（组）。
解析阶段不做任何"装饰性合图"——一图层 = 一 div = 一 PNG。

唯一的例外是 PSD 原生剪贴蒙版语义：
- base 是普通图层 + clipping=1 的 layer 列表 → 必须烧成单图（CSS 无法等价还原）
- base 是组 + clipping=1 的 layer → clipped 用 base group alpha/矩形 剪裁后独立导出

这两种处理由 `_merge_clipping_group` / `_export_clipped_layer_against_group_base`
完成，是 PSD 像素语义的还原，不是"为了减少 DOM 节点而合图"。
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


class ClippingGroupHandler(LayerHandler):
    """剪切蒙版组：base_layer + 多个 clipped_layers（PSD 原生语义）。"""

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

        # 过滤隐藏 / 透明的 clipped 层
        clipped_layers = [
            cl for cl in clipped_layers
            if cl.visible and cl.opacity > 0
        ]

        # 隐藏/透明 base → 跳过整组
        if not base_layer.visible:
            exp.skipped_count += 1 + len(clipped_layers)
            print(f"{'  ' * depth}🚫 {base_name} (隐藏剪切蒙版组，已跳过)")
            return HandlerResult(handled=True)
        if base_layer.opacity == 0:
            exp.skipped_count += 1 + len(clipped_layers)
            print(f"{'  ' * depth}🚫 {base_name} (opacity=0剪切蒙版组，已跳过)")
            return HandlerResult(handled=True)

        # 所有 clipped 都被隐藏 → 仅处理 base
        if not clipped_layers:
            if base_layer.is_group():
                inner_ctx = HandlerContext(
                    exporter=exp, item=base_layer, depth=depth,
                    parent_name=parent_name, parent_left=parent_left,
                    parent_top=parent_top,
                    parent_clip_bbox=parent_clip_bbox,
                )
                return GroupHandler().handle(inner_ctx)
            li = exp._export_single_layer(
                base_layer, base_name,
                f'{parent_name}/{base_name}' if parent_name else base_name,
                depth, parent_left, parent_top, clip_bbox=parent_clip_bbox,
            )
            return HandlerResult(produced=[li] if li else [], handled=True)

        produced: list[dict[str, Any]] = []
        full_name = f'{parent_name}/{base_name}' if parent_name else base_name

        # base 是组：组本身递归导出，clipped 层用 base group alpha 剪裁后独立导出
        if base_layer.is_group():
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
                'blend_mode': BLEND_MODES.get(base_layer.blend_mode, 'normal'),
                'z_index': exp._z_counter,
                'children': children,
            }
            produced.append(group_info)
            for cl in clipped_layers:
                cl_name = cl.name or 'clipped'
                cl_full = f'{parent_name}/{cl_name}' if parent_name else cl_name
                # 用 base group composite alpha/矩形 剪裁 clipped layer，
                # 避免输出 PSD 视觉中"看不见"的整张大色块
                li = exp._export_clipped_layer_against_group_base(
                    cl, base_layer, cl_name, cl_full, depth,
                    parent_left, parent_top,
                )
                if li:
                    produced.append(li)
            return HandlerResult(produced=produced, handled=True)

        # base 是普通图层：合并剪切（PSD 原生语义，CSS 无法等价还原）
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
    """普通组（非剪切蒙版）：用 PSD 原生合成簇决策合图策略。

    决策由 `compose_cluster.decide_group_merge()` 给出，基于 PSD 自身的硬性
    合成语义（剪贴蒙版 / 非 NORMAL 混合 / PASS_THROUGH 子组 / 调整层）：

    - merge_full           → 整组合成单图
    - merge_with_text_kept → 非文本合成为背景，文本独立保留可访问性
    - merge_partial        → 仅"glued cluster"合成（每个 cluster 一张），
                              singleton independent cluster 保留独立递归
    - no_merge             → 完全递归处理
    """

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

        from .compose_cluster import decide_group_merge, describe_decision

        decision = decide_group_merge(layer)
        print(f"{'  ' * depth}📁 {layer_name} → {describe_decision(decision)}")

        # ── 路径 1: merge_full —— 整组合成单图 ────────────────────────────
        if decision.action == "merge_full":
            merged = exp._merge_group_as_single_image(
                layer, layer_name, full_name, depth, parent_left, parent_top,
            )
            if merged:
                return HandlerResult(produced=[merged], handled=True)
            print(f"{'  ' * depth}  ⚠️  merge_full 失败，回退逐层导出")
            # 失败 fallthrough 到默认递归路径

        # ── 路径 2: merge_with_text_kept —— 非文本合成 + 文本独立 ─────────
        merged_bg_info: dict[str, Any] | None = None
        if decision.action == "merge_with_text_kept":
            merged_bg_info = exp._merge_group_non_text_as_image(
                layer, layer_name, full_name, depth,
                parent_left=layer.left, parent_top=layer.top,
            )
            if merged_bg_info is None:
                print(f"{'  ' * depth}  ⚠️  merge_with_text_kept 失败，回退逐层导出")

        # ── 路径 3: merge_partial —— 按 PSD sibling 顺序交错处理 ───────────
        # 关键设计：cluster 的合成图必须分配到它在 PSD 中"最高 idx 成员"
        # 对应的 z 序位置（在 indep sibling 之间），不能集中前置。否则会把
        # 真实位于 cluster 之上的 indep sibling 错置到 cluster 之下，
        # 造成纯色 placeholder 反盖海滩主图等灾难性视觉错乱。
        partial_merged_ids: set[int] = set()
        partial_did_succeed = False
        if decision.action == "merge_partial":
            for cluster_members in decision.merged_clusters:
                for m in cluster_members:
                    partial_merged_ids.add(id(m))

        # ── 检查组是否有 layer mask（需要传播到子层导出）──────────────────
        # 当组不走 merge_full 时（子层独立导出），组自身的 mask 不会通过
        # composite() 自动应用，需要显式传播到子层。
        _has_group_mask = (
            hasattr(layer, 'mask') and layer.mask is not None
            and not getattr(layer.mask, 'disabled', False)
            and layer.mask.bbox != (0, 0, 0, 0)
        )
        if _has_group_mask:
            exp._ancestor_group_masks.append((layer, layer.mask.bbox))

        # ── 路径 4: 默认递归（含上面任何路径回退到这里的情况） ────────────
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

        children: list[dict[str, Any]] = []

        if decision.action == "merge_partial" and decision.merged_clusters:
            # ── 按 PSD sibling 顺序交错处理 ─────────────────────────────
            # 遍历组的直接子（PSD 自底向上顺序），对每个 sibling：
            #   - 若 sibling 不属于任何 glued cluster → 走标准 handler 链
            #     （单图层 / 子组 / 剪贴蒙版组等）
            #   - 若 sibling 属于某 glued cluster：
            #       * 不是该 cluster 最后一个成员 → 跳过（合成时一并处理）
            #       * 是最后一个成员 → 触发该 cluster 合成（z 序在此分配）
            # 这样合成图的 z 序与"该 cluster 最顶成员在 PSD 中的位置"一致，
            # 浏览器堆叠语义与 PSD 合成语义对齐。
            cluster_by_last_id: dict[int, list[Any]] = {}
            for cluster_members in decision.merged_clusters:
                if not cluster_members:
                    continue
                # 取 cluster 中"最后一个被加入的成员"作为占位锚点
                # （compose_cluster 严格按 PSD 自底向上顺序构建 cluster.members）
                last = cluster_members[-1]
                cluster_by_last_id[id(last)] = cluster_members

            cluster_idx_counter = 0
            total_glued_clusters = len(decision.merged_clusters)

            sibling_list = list(layer)
            for sib in sibling_list:
                sid = id(sib)
                if sid in partial_merged_ids:
                    # 属于 cluster 的成员
                    if sid in cluster_by_last_id:
                        # 该 cluster 的"锚点"成员 → 触发合成
                        cluster_members = cluster_by_last_id[sid]
                        suffix = (
                            f"-cluster{cluster_idx_counter}"
                            if total_glued_clusters > 1 else ""
                        )
                        cluster_idx_counter += 1
                        bg = exp._merge_cluster_layers_as_image(
                            layer, cluster_members, layer_name, full_name, depth,
                            parent_left=grp_abs_left_orig,
                            parent_top=grp_abs_top_orig,
                            suffix=suffix,
                        )
                        if bg is not None:
                            children.append(bg)
                            partial_did_succeed = True
                    # else: 不是锚点成员，等到锚点出现时一并合成；
                    # 标准 handler 链对它也跳过（在下面"独立处理 sibling"里被屏蔽）
                else:
                    # 独立 sibling（singleton independent cluster 成员）→ 标准 handler 链
                    # 临时隐藏所有 cluster 成员，避免它们参与单 sibling 处理
                    saved: list[tuple[Any, bool]] = []
                    for c in sibling_list:
                        if id(c) != sid and c.visible:
                            saved.append((c, c.visible))
                            try:
                                c.visible = False
                            except Exception:
                                pass
                    try:
                        sub_children = exp.export_layers(
                            [sib], full_name, depth + 1,
                            parent_left=grp_abs_left_orig,
                            parent_top=grp_abs_top_orig,
                            parent_clip_bbox=group_clip_bbox,
                        )
                        children.extend(sub_children)
                    finally:
                        for c, vis in saved:
                            try:
                                c.visible = vis
                            except Exception:
                                pass

            if not partial_did_succeed:
                print(f"{'  ' * depth}  ⚠️  merge_partial 全部失败，回退逐层导出")
                children = exp.export_layers(
                    layer, full_name, depth + 1,
                    parent_left=grp_abs_left_orig,
                    parent_top=grp_abs_top_orig,
                    parent_clip_bbox=group_clip_bbox,
                )
        else:
            # merge_with_text_kept / merge_full 失败回退 / no_merge：
            # 用 export_layers 一次性递归（必要时临时隐藏）
            hidden_saved: list[tuple[Any, bool]] = []
            if merged_bg_info is not None:
                non_text = [t for t in self._collect_non_text_recursive(layer)]
                for nt in non_text:
                    hidden_saved.append((nt, nt.visible))
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
                for nt, vis in hidden_saved:
                    try:
                        nt.visible = vis
                    except Exception:
                        pass

            # merge_with_text_kept：合成图作为组的"最底层"子元素
            if merged_bg_info is not None:
                children = [merged_bg_info] + children

        if grp_overflow:
            offset_x = grp_abs_left - grp_abs_left_orig
            offset_y = grp_abs_top - grp_abs_top_orig
            if offset_x != 0 or offset_y != 0:
                print(f"{'  ' * depth}  🔧 调整子图层坐标偏移: ({-offset_x}, {-offset_y})")
                exp._adjust_children_offset(children, -offset_x, -offset_y)

        # ── pop 组 mask ──
        if _has_group_mask:
            exp._ancestor_group_masks.pop()

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
            'blend_mode': BLEND_MODES.get(layer.blend_mode, 'normal'),
            'z_index': exp._z_counter,
            'children': children,
        }
        return HandlerResult(produced=[group_info], handled=True)

    @staticmethod
    def _collect_non_text_recursive(group_layer: Any) -> list[Any]:
        """递归收集组内所有可见非文本叶图层（含子组里的）。

        merge_with_text_kept 路径：合成图已包含所有非文本视觉，
        递归导出时需把这些非文本图层临时隐藏，避免重复导出。
        """
        out: list[Any] = []

        def walk(node: Any) -> None:
            if not getattr(node, 'visible', True) or getattr(node, 'opacity', 255) == 0:
                return
            if node.is_group():
                for c in node:
                    walk(c)
                return
            kind = str(getattr(node, 'kind', '') or '').lower()
            if 'type' not in kind:
                out.append(node)

        for c in group_layer:
            walk(c)
        return out


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


#: 决策顺序：剪切蒙版组 → 跳过隐藏 → 组 → 叶图层
DEFAULT_HANDLERS: list[LayerHandler] = [
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
