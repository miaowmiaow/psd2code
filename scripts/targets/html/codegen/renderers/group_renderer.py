# -*- coding: utf-8 -*-
"""组图层渲染策略。

组的渲染逻辑包含：
1. 递归计算子图层实际边界（考虑效果溢出）
2. 决定是否设置 overflow:hidden
3. 递归渲染子层（通过 host.render）
"""

from __future__ import annotations

from typing import Any, Optional

from ..escape import _esc
from .base import NodeRenderer, register_renderer
from .css_helpers import position_css_lines, semantic_css_class

# 组容器「贴边保护边距」：
# 当子层几乎填满组 bbox 时，组容器会在右下各多出这么多像素，避免子图边缘的
# 抗锯齿像素被 `overflow: hidden` 裁掉。
#
# 注意：
# - 它**不是**效果扩展（描边/投影扩展已在 LayerExporter 阶段计入子图 bbox，
#   见 core/extract/layer_exporter.py::_calc_group_expand）；
# - 因此组容器的 CSS 尺寸可能比 PSD bbox 大 2px，`_mapping_report.md` 里会看到
#   这一差异（属预期，不是 bug）；
# - 改动它属于像素级行为变更，需要真实设计稿的 baseline diff 验证。
_GROUP_BLEED_PADDING = 2


def _calculate_actual_bounds(layer_data: dict[str, Any]) -> tuple[int, int]:
    """递归计算图层的实际边界（考虑效果溢出）。

    普通图层返回自身 width/height；组图层递归合并子层边界，
    紧贴边界时扩展 +2px 安全边距。
    空残留 group（width=0/height=0 且无 children）直接返回 (0, 0)，
    避免被当作有效占位带入父组的边界计算。
    """
    if layer_data.get('type') != 'group':
        return layer_data.get('width', 0), layer_data.get('height', 0)

    original_width = layer_data.get('width', 0)
    original_height = layer_data.get('height', 0)
    children = layer_data.get('children', [])

    # 空残留组：PSD 设计师隐藏/清空过的子组，无视觉内容，
    # 不应贡献边界（也不要返回 +2 安全边距，否则在父组遇到极端坐标
    # 时会拉偏 min_top）。
    if not children and original_width == 0 and original_height == 0:
        return 0, 0

    # 以「子层实际边界」为基准（0 起步），而不是以 bbox 起步：
    # 若从 bbox 起步，`max_right >= original_width - 1` 恒成立 → 每个有子层的组
    # 都会被无条件 +2px，容器尺寸永远比 PSD bbox 大 2px（历史行为）。
    max_right = 0
    max_bottom = 0

    for child in children:
        # 跳过空残留子组（同上）
        if (
            child.get('type') == 'group'
            and not child.get('children')
            and child.get('width', 0) == 0
            and child.get('height', 0) == 0
        ):
            continue
        child_left = child.get('left', 0)
        child_top = child.get('top', 0)
        child_width, child_height = _calculate_actual_bounds(child)

        child_right = child_left + child_width
        child_bottom = child_top + child_height

        max_right = max(max_right, child_right)
        max_bottom = max(max_bottom, child_bottom)

    # 分轴判断：只有真正触及/超出该方向边界的轴才加保护边距。
    # 旧实现用 `or` 合并两轴，且直接返回 max_* + 2 —— 当只有宽度溢出时高度会被
    # 压成 `子层高度 + 2`（可能远小于 bbox），把容器砍短。
    width = original_width
    height = original_height
    if max_right >= original_width - 1:
        width = max(original_width, max_right + _GROUP_BLEED_PADDING)
    if max_bottom >= original_height - 1:
        height = max(original_height, max_bottom + _GROUP_BLEED_PADDING)
    return width, height


@register_renderer("group")
class GroupRenderer(NodeRenderer):
    """组图层。"""

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

        left = layer["left"]
        top = layer["top"]
        width = layer["width"]
        height = layer["height"]

        # ── 1. 溢出检测 ──
        has_overflow = False
        children = layer.get('children', [])
        if children:
            min_left = 0
            min_top = 0
            # 同 _calculate_actual_bounds：从 0 起步才能区分"内缩"与"贴边/溢出"
            max_right = 0
            max_bottom = 0

            for child in children:
                # 跳过"空残留 group"：PSD 里被设计师隐藏/清空的子组，
                # bbox 收缩到 (0,0)，但 layer.left/top 仍带极端坐标
                # （如 top=-1459）。若纳入边界计算，min_top 会被拉到
                # -1459 → 父组 height 被错算成 1500+。这种空 group 既
                # 无视觉内容也无 children，对父组边界毫无贡献，必须排除。
                if (
                    child.get('type') == 'group'
                    and not child.get('children')
                    and child.get('width', 0) == 0
                    and child.get('height', 0) == 0
                ):
                    continue

                child_left = child.get('left', 0)
                child_top = child.get('top', 0)
                child_width, child_height = _calculate_actual_bounds(child)

                child_right = child_left + child_width
                child_bottom = child_top + child_height

                if child_left < min_left:
                    min_left = child_left
                if child_top < min_top:
                    min_top = child_top
                if child_right > max_right:
                    max_right = child_right
                if child_bottom > max_bottom:
                    max_bottom = child_bottom

            actual_width = max_right - min_left
            actual_height = max_bottom - min_top

            if actual_width >= width - 1 or actual_height >= height - 1:
                has_overflow = True
                # 分轴扩展，且绝不小于 bbox（详见 _calculate_actual_bounds 注释）
                if actual_width >= width - 1:
                    width = max(width, actual_width + _GROUP_BLEED_PADDING)
                if actual_height >= height - 1:
                    height = max(height, actual_height + _GROUP_BLEED_PADDING)

        # ── 2. 组 CSS ──
        # class_name 是多类字符串（如 "btn__27 layer-group"），CSS 选择器只取语义类
        css_class = semantic_css_class(class_name)
        css = f'.{css_class} {{\n'
        # GroupRenderer 历史上不写 mix-blend-mode；width/height 可能因溢出检测扩展
        css += position_css_lines(
            layer, width=width, height=height, left=left, top=top,
            include_blend=False,
        )
        if not has_overflow:
            css += '    overflow: hidden;  /* 裁剪超出组边界的子图层 */\n'
        css += '}'
        self.ctx.css_rules.append(css)

        # ── 3. 递归渲染所有子层 ──
        html = f'{pad}<div id="{layer_id}" class="{class_name}" data-name="{_esc(layer["name"])}" data-type="group">\n'
        for child in children:
            html += self.host.render(child, indent + 1, parent=layer, siblings=children)
        html += f'{pad}</div>\n'
        return html
