#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PSD 剪贴蒙版 (clipping mask) 处理模块

负责识别和合并 PSD 中的剪贴蒙版图层。
剪贴蒙版是 PSD 原生语义，CSS 无法等价，因此必须作为像素级合成还原。

参考: https://en.wikipedia.org/wiki/Clipping_(computer_graphics)
"""

from __future__ import annotations

from typing import Any
from PIL import Image
import numpy as np

from common.image_utils import ImageArrayUtils
from core.render.effects.effects_renderer import (
    render_layer_with_effects,
    render_layer_with_effects_on_image,
    is_effect_active,
)
from .image_ops import _apply_layer_mask


class ClippingHandler:
    """PSD 剪贴蒙版处理器
    
    负责：
    1. 识别 clipping 图层
    2. 分组 base + clipped 图层
    3. 合并渲染为单张图片
    """

    # Photoshop 混合模式映射表（从 layer_exporter 复用）
    # 这里简化版本，只保留剪贴蒙版处理中需要的模式
    BLEND_MODES_CLIPPING = {
        'OVERLAY': 'overlay',
        'MULTIPLY': 'multiply',
        'SCREEN': 'screen',
        'SOFT_LIGHT': 'soft-light',
        'HARD_LIGHT': 'hard-light',
        'NORMAL': 'normal',
    }

    @staticmethod
    def is_clipping_layer(layer: Any) -> bool:
        """判断图层是否为剪切蒙版。
        
        Args:
            layer: PSD 图层对象
            
        Returns:
            True 如果是剪贴蒙版图层
        """
        return (
            hasattr(layer, '_record')
            and hasattr(layer._record, 'clipping')
            and layer._record.clipping == 1
        )

    @staticmethod
    def group_clipping_layers(layers_list: list[Any]) -> list[Any]:
        """把连续的 clipping 图层分组到它们的 base 图层上。

        返回一个列表，元素为：
        - 普通图层 / 组 → 原样保留
        - (base_layer, [clipped_layer, ...]) → 需要合并渲染的剪切蒙版组
        
        Args:
            layers_list: 图层列表
            
        Returns:
            重新组织的层列表
        """
        grouped: list[Any] = []
        i = 0
        while i < len(layers_list):
            layer = layers_list[i]
            # 如果该图层不是 clipping，检查后面是否有 clipping 图层挂在它身上
            if not ClippingHandler.is_clipping_layer(layer):
                clipped: list[Any] = []
                j = i + 1
                while j < len(layers_list) and ClippingHandler.is_clipping_layer(layers_list[j]):
                    clipped.append(layers_list[j])
                    j += 1
                if clipped:
                    grouped.append((layer, clipped))
                else:
                    grouped.append(layer)
                i = j
            else:
                # 孤立的 clipping 图层（没有 base），作为普通图层处理
                grouped.append(layer)
                i += 1
        return grouped

    @staticmethod
    def adjust_children_offset(
        children: list[dict],
        offset_x: int,
        offset_y: int,
    ) -> None:
        """递归调整子图层的相对坐标偏移。

        当父组的 bbox 被约束时（比如从 -904 变成 0），子图层的相对坐标需要相应调整。

        关键原则：
        - 直接子图层（image/text）：调整其相对坐标
        - 子组（group）：只调整组自己的坐标，**不递归调整其子图层**
          因为子组的子图层已经是相对于子组的坐标，不受父组约束影响

        Args:
            children: 子图层列表
            offset_x: X 轴偏移量
            offset_y: Y 轴偏移量
        """
        for child in children:
            # 调整当前图层的坐标
            child['left'] += offset_x
            child['top'] += offset_y

    @staticmethod
    def merge_clipping_group(
        base_layer: Any,
        clipped_layers: list[Any],
        parent_name: str,
        depth: int,
        parent_left: int,
        parent_top: int,
        z_counter_ref: list[int],  # 使用 list 绕过 Python 作用域限制
        image_saver,  # 图片保存回调
        blend_modes_map: dict,  # 混合模式映射
    ) -> dict[str, Any] | None:
        """将 base + clipped 图层合并渲染为单张图片并导出。
        
        Photoshop 渲染顺序：先在 base 原始内容上合成 clip 层，再应用 base 效果。

        ⚠️ 这是 PSD 像素语义的还原（CSS 无法等价），不是装饰性合图。
        
        Args:
            base_layer: 基础图层
            clipped_layers: 剪贴蒙版图层列表
            parent_name: 父图层名称
            depth: 递归深度
            parent_left: 父容器左坐标
            parent_top: 父容器顶坐标
            z_counter_ref: z-index 计数器引用（list[int]）
            image_saver: 图片保存函数，签名为 (img, name, depth) → rel_path
            blend_modes_map: 混合模式映射表
            
        Returns:
            合并后的图层信息字典，或 None 如果合并失败
        """
        try:
            base_name = base_layer.name or 'merged'
            clipped_names = [c.name for c in clipped_layers]
            full_name = f'{parent_name}/{base_name}' if parent_name else base_name
            print(f"{'  ' * depth}🔗 合并剪切蒙版: {base_name} ← {clipped_names}")

            # --- 步骤1: 获取 base 原始图层（不含效果）---
            base_raw_img = base_layer.topil()
            if base_raw_img is None:
                return None
            if base_raw_img.mode != 'RGBA':
                base_raw_img = base_raw_img.convert('RGBA')

            raw_bbox = base_layer.bbox  # 原始 bbox（无效果扩展）
            raw_w = raw_bbox[2] - raw_bbox[0]
            raw_h = raw_bbox[3] - raw_bbox[1]
            if raw_w <= 0 or raw_h <= 0:
                return None

            # --- 步骤2: 在 base 原始内容上合成 clip 层 ---
            canvas_raw = np.array(
                base_raw_img.resize((raw_w, raw_h)) if base_raw_img.size != (raw_w, raw_h) else base_raw_img,
                dtype=np.float32
            ) / 255.0

            # base 原始 alpha 用于 clip
            base_alpha = canvas_raw[:, :, 3:4].copy()

            for cl in clipped_layers:
                if not cl.visible or cl.opacity == 0:
                    continue
                try:
                    # 对 clip 层使用效果渲染（处理 ColorOverlay 等）
                    cl_effect_result = render_layer_with_effects(cl)
                    if cl_effect_result is not None:
                        cl_img, cl_bbox = cl_effect_result
                    else:
                        cl_img = cl.topil()
                        cl_bbox = cl.bbox
                    if cl_img is None:
                        continue
                    if cl_img.mode != 'RGBA':
                        cl_img = cl_img.convert('RGBA')

                    # 应用图层蒙版（user mask）
                    cl_img = _apply_layer_mask(cl, cl_img, cl_bbox)

                    cl_arr = ImageArrayUtils.pil_to_float_array(cl_img)

                    ox = cl_bbox[0] - raw_bbox[0]
                    oy = cl_bbox[1] - raw_bbox[1]
                    cl_h, cl_w = cl_arr.shape[:2]

                    src_x0 = max(0, -ox)
                    src_y0 = max(0, -oy)
                    dst_x0 = max(0, ox)
                    dst_y0 = max(0, oy)
                    copy_w = min(cl_w - src_x0, raw_w - dst_x0)
                    copy_h = min(cl_h - src_y0, raw_h - dst_y0)

                    if copy_w <= 0 or copy_h <= 0:
                        continue

                    src_region = cl_arr[src_y0:src_y0+copy_h, src_x0:src_x0+copy_w]
                    dst_region = canvas_raw[dst_y0:dst_y0+copy_h, dst_x0:dst_x0+copy_w]
                    clip_region = base_alpha[dst_y0:dst_y0+copy_h, dst_x0:dst_x0+copy_w]

                    layer_opacity = cl.opacity / 255.0
                    src_alpha = src_region[:, :, 3:4] * layer_opacity
                    # Clip to base alpha: 使用连续 alpha 值（而非 binary）
                    # Photoshop 剪切蒙版将 clip 层的 alpha 乘以 base alpha，
                    # 使 clip 层在 base 半透明边缘处也能平滑过渡
                    src_alpha = src_alpha * clip_region

                    src_rgb = src_region[:, :, :3]
                    dst_rgb = dst_region[:, :, :3]
                    dst_alpha_ch = dst_region[:, :, 3:4]

                    # 混合模式处理
                    blend_mode = str(cl.blend_mode)
                    if 'OVERLAY' in blend_mode.upper():
                        blended = np.where(
                            dst_rgb < 0.5,
                            2.0 * src_rgb * dst_rgb,
                            1.0 - 2.0 * (1.0 - src_rgb) * (1.0 - dst_rgb)
                        )
                    elif 'MULTIPLY' in blend_mode.upper():
                        blended = src_rgb * dst_rgb
                    elif 'SCREEN' in blend_mode.upper():
                        blended = 1.0 - (1.0 - src_rgb) * (1.0 - dst_rgb)
                    elif 'SOFT' in blend_mode.upper():
                        blended = np.where(
                            src_rgb < 0.5,
                            dst_rgb - (1.0 - 2.0 * src_rgb) * dst_rgb * (1.0 - dst_rgb),
                            dst_rgb + (2.0 * src_rgb - 1.0) * (np.sqrt(dst_rgb) - dst_rgb)
                        )
                    elif 'HARD' in blend_mode.upper():
                        blended = np.where(
                            src_rgb < 0.5,
                            2.0 * src_rgb * dst_rgb,
                            1.0 - 2.0 * (1.0 - src_rgb) * (1.0 - dst_rgb)
                        )
                    else:
                        blended = src_rgb

                    # Porter-Duff OVER 合成（标准 alpha 合成公式）
                    out_alpha = src_alpha + dst_alpha_ch * (1.0 - src_alpha)
                    safe_out_alpha = np.where(out_alpha > 0, out_alpha, 1.0)
                    out_rgb = (blended * src_alpha + dst_rgb * dst_alpha_ch * (1.0 - src_alpha)) / safe_out_alpha

                    canvas_raw[dst_y0:dst_y0+copy_h, dst_x0:dst_x0+copy_w, :3] = np.clip(out_rgb, 0, 1)
                    canvas_raw[dst_y0:dst_y0+copy_h, dst_x0:dst_x0+copy_w, 3:4] = np.clip(out_alpha, 0, 1)

                except Exception as e:
                    print(f"{'  ' * depth}  ⚠️  合成 {cl.name} 失败: {e}")

            # --- 步骤2.5: 应用 base 图层的 user mask（图层蒙版）---
            merged_raw = ImageArrayUtils.float_array_to_pil(canvas_raw)
            merged_raw = _apply_layer_mask(base_layer, merged_raw, raw_bbox)

            # --- 步骤3: 应用 base 的效果 ---
            has_effects = False
            if hasattr(base_layer, 'effects') and base_layer.effects:
                for e in base_layer.effects:
                    if is_effect_active(e, base_layer):
                        has_effects = True
                        break

            if has_effects:
                final_result = render_layer_with_effects_on_image(
                    base_layer, merged_raw, raw_bbox
                )
                if final_result is not None:
                    final_img, final_bbox = final_result
                else:
                    final_img = merged_raw
                    final_bbox = raw_bbox
            else:
                final_img = merged_raw
                final_bbox = raw_bbox

            if final_img.mode != 'RGBA':
                final_img = final_img.convert('RGBA')

            canvas_left = final_bbox[0]
            canvas_top = final_bbox[1]
            canvas_w = final_bbox[2] - final_bbox[0]
            canvas_h = final_bbox[3] - final_bbox[1]

            z_counter_ref[0] += 1
            abs_left = canvas_left
            abs_top = canvas_top
            rel_left = abs_left - parent_left
            rel_top = abs_top - parent_top

            layer_info: dict[str, Any] = {
                'id': f'layer-{z_counter_ref[0]}',
                'name': base_name,
                'full_name': full_name,
                'left': rel_left,
                'top': rel_top,
                'width': canvas_w,
                'height': canvas_h,
                'opacity': base_layer.opacity / 255.0,
                'blend_mode': blend_modes_map.get(base_layer.blend_mode, 'normal'),
                'z_index': z_counter_ref[0],
                'type': 'image',
            }

            # 保存图片（去重）
            rel_path = image_saver(final_img, base_name, depth)
            layer_info['image_path'] = rel_path

            print(f"{'  ' * depth}🖼️  {base_name} [合并{len(clipped_layers)+1}层 {canvas_w}x{canvas_h}] → {rel_path}")
            return layer_info

        except Exception as e:
            print(f"{'  ' * depth}❌ 合并剪切蒙版失败 ({base_layer.name}): {e}")
            import traceback
            traceback.print_exc()
            return None
