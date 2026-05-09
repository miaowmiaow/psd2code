#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""底层图像操作：bbox 裁剪、图层蒙版应用、numpy alpha 合成。

这些函数不依赖 `LayerExporter` 状态，独立为模块以便复用 / 测试。
"""

from typing import Any

import numpy as np
from PIL import Image

from common.image_utils import ImageArrayUtils


def _constrain_bbox_to_canvas(
    bbox: tuple[int, int, int, int],
    canvas_w: int,
    canvas_h: int,
) -> tuple[int, int, int, int]:
    """
    将 bbox 约束到画布范围内。

    Args:
        bbox: (left, top, right, bottom)
        canvas_w: 画布宽度
        canvas_h: 画布高度

    Returns:
        约束后的 bbox (left, top, right, bottom)
    """
    left, top, right, bottom = bbox
    left = max(0, min(left, canvas_w))
    top = max(0, min(top, canvas_h))
    right = max(left, min(right, canvas_w))
    bottom = max(top, min(bottom, canvas_h))
    return (left, top, right, bottom)


def _apply_layer_mask(layer: Any, img: Image.Image, img_bbox: tuple) -> Image.Image:
    """
    如果图层有 user mask，将 mask 应用到图像的 alpha 通道上。
    Photoshop 中，图层蒙版（user mask）会限制图层的可见区域。
    """
    if not hasattr(layer, 'mask') or layer.mask is None:
        return img
    try:
        mask = layer.mask
        mask_img = mask.topil()
        if mask_img is None:
            return img

        mask_arr = ImageArrayUtils.pil_l_to_float_array(mask_img)
        mask_bbox = mask.bbox  # mask 在画布上的绝对坐标

        img_arr = ImageArrayUtils.pil_to_float_array(img)
        h, w = img_arr.shape[:2]

        # 计算 mask 与图像的重叠区域
        mx0 = mask_bbox[0] - img_bbox[0]
        my0 = mask_bbox[1] - img_bbox[1]
        mh, mw = mask_arr.shape[:2]

        # 创建与图像同尺寸的 mask（默认 = mask 的默认颜色，通常白色=255）
        # Photoshop mask 默认背景色根据是否有 background_color 属性决定
        default_val = 0.0  # mask 外部区域默认不可见
        if hasattr(mask, 'background_color') and mask.background_color is not None:
            default_val = mask.background_color / 255.0

        full_mask = np.full((h, w), default_val, dtype=np.float32)

        # 将 mask 内容放到正确位置
        src_x0 = max(0, -mx0)
        src_y0 = max(0, -my0)
        dst_x0 = max(0, mx0)
        dst_y0 = max(0, my0)
        cp_w = min(mw - src_x0, w - dst_x0)
        cp_h = min(mh - src_y0, h - dst_y0)

        if cp_w > 0 and cp_h > 0:
            full_mask[dst_y0:dst_y0+cp_h, dst_x0:dst_x0+cp_w] = \
                mask_arr[src_y0:src_y0+cp_h, src_x0:src_x0+cp_w]

        # 应用 mask 到 alpha 通道
        img_arr[:, :, 3] *= full_mask

        return ImageArrayUtils.float_array_to_pil(img_arr)

    except Exception as e:
        print(f"    ⚠️  应用图层蒙版失败: {e}")
        return img


def _alpha_composite_numpy(dst: 'np.ndarray', src: 'np.ndarray') -> 'np.ndarray':
    """
    标准 Porter-Duff src-over 合成（numpy 版本）。
    dst, src: (H, W, 4) float32, 值域 [0, 1]
    """
    src_rgb = src[:, :, :3]
    src_a = src[:, :, 3:4]
    dst_rgb = dst[:, :, :3]
    dst_a = dst[:, :, 3:4]

    out_a = src_a + dst_a * (1.0 - src_a)
    safe_a = np.maximum(out_a, 1e-10)
    out_rgb = (src_rgb * src_a + dst_rgb * dst_a * (1.0 - src_a)) / safe_a

    result = np.empty_like(dst)
    result[:, :, :3] = out_rgb
    result[:, :, 3:4] = out_a
    return result
