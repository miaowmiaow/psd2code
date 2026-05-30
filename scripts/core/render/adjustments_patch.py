#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
psd-tools 调整层补丁模块

psd-tools 原生不支持某些调整层类型（如 BlackAndWhite）。
此模块实现缺失的调整层算法并注册到 psd-tools 的 ADJUSTMENT_FUNC 字典中，
使 composite() 调用时能正确渲染这些效果。

用法：在 composite() 调用前 import 此模块即可（模块级自动注册）。
"""

from __future__ import annotations

import functools
import logging
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from psd_tools.constants import ColorMode
from psd_tools.api.adjustments import BlackAndWhite

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────────────

_0 = np.float32(0.0)
_1 = np.float32(1.0)


def _preserve_alpha(func):
    """与 psd_tools.composite.adjustments._preserve_alpha 相同逻辑的装饰器。"""

    @functools.wraps(func)
    def wrapper(img: np.ndarray, colormode: ColorMode, *args, **kwargs):
        color_channels = ColorMode.channels(colormode)
        n_channels = img.shape[2]

        if not (n_channels == color_channels or n_channels == color_channels + 1):
            raise ValueError("Channel count does not match colormode.")

        if n_channels > color_channels:
            color = img[..., :color_channels]
            alpha = img[..., color_channels:]

            out = func(color, colormode, *args, **kwargs)
            if out.shape[2] != color_channels:
                raise ValueError(
                    f"Adjustment function returned {out.shape[2]} channels, "
                    f"expected {color_channels}."
                )
            return np.concatenate([out, alpha], axis=2)

        return func(img, colormode, *args, **kwargs)

    return wrapper


# ──────────────────────────────────────────────────────────────────────
# BlackAndWhite 调整层实现
# ──────────────────────────────────────────────────────────────────────
#
# Photoshop Black & White 调整层工作原理：
# 1. 将每个像素的 RGB 转换为灰度，使用基于色相的加权混合
# 2. 各通道权重：Reds, Yellows, Greens, Cyans, Blues, Magentas
# 3. 权重范围 [-200, 300]，默认值各不相同
# 4. 最终灰度值 = 对每个色相区间的贡献加权求和
#
# 色相到权重的映射使用 60° 分段线性插值：
#   H ∈ [0°,  60°)  → Reds ↔ Yellows
#   H ∈ [60°, 120°) → Yellows ↔ Greens
#   H ∈ [120°,180°) → Greens ↔ Cyans
#   H ∈ [180°,240°) → Cyans ↔ Blues
#   H ∈ [240°,300°) → Blues ↔ Magentas
#   H ∈ [300°,360°) → Magentas ↔ Reds
#
# 最终灰度 = luminance * (interpolated_weight / 100)
# 其中 luminance = max(R, G, B)（Photoshop 用 max 而非标准 luma）

@_preserve_alpha
def apply_blackandwhite(
    img: np.ndarray,
    colormode: Literal[ColorMode.CMYK, ColorMode.GRAYSCALE, ColorMode.RGB],
    layer: BlackAndWhite,
) -> np.ndarray:
    """应用 BlackAndWhite 调整层效果。

    Args:
        img: (H, W, 3) float32 RGB [0, 1]
        colormode: 色彩模式
        layer: BlackAndWhite 调整层对象

    Returns:
        (H, W, 3) float32 灰度图（3通道相同值）
    """
    if colormode == ColorMode.GRAYSCALE:
        # 灰度图不需要处理
        return img.copy()

    if colormode == ColorMode.CMYK:
        # CMYK 暂不完整支持，直接返回
        logger.debug("BlackAndWhite adjustment on CMYK not fully supported")
        return img.copy()

    # RGB 模式
    # 获取权重参数（范围 -200 ~ 300，默认 40,60,40,60,20,80）
    reds = np.float32(layer.red) / np.float32(100.0)
    yellows = np.float32(layer.yellow) / np.float32(100.0)
    greens = np.float32(layer.green) / np.float32(100.0)
    cyans = np.float32(layer.cyan) / np.float32(100.0)
    blues = np.float32(layer.blue) / np.float32(100.0)
    magentas = np.float32(layer.magenta) / np.float32(100.0)

    # weights 数组：按色相 0°→60°→120°→180°→240°→300°→360° 的顺序
    # 对应 Reds, Yellows, Greens, Cyans, Blues, Magentas, (wrap to Reds)
    weights = np.array(
        [reds, yellows, greens, cyans, blues, magentas, reds],
        dtype=np.float32,
    )

    h, w, _ = img.shape
    r = img[:, :, 0]
    g = img[:, :, 1]
    b = img[:, :, 2]

    # 计算色相（简化版，0-6 范围映射到 6 个 60° 段）
    cmax = np.maximum(np.maximum(r, g), b)
    cmin = np.minimum(np.minimum(r, g), b)
    delta = cmax - cmin

    # 避免除零
    safe_delta = np.where(delta > 1e-7, delta, np.float32(1.0))

    # 色相计算 (0-6 范围)
    hue = np.zeros((h, w), dtype=np.float32)

    mask_r = (cmax == r) & (delta > 1e-7)
    mask_g = (cmax == g) & (delta > 1e-7) & ~mask_r
    mask_b = (cmax == b) & (delta > 1e-7) & ~mask_r & ~mask_g

    hue[mask_r] = ((g[mask_r] - b[mask_r]) / safe_delta[mask_r]) % np.float32(6.0)
    hue[mask_g] = (b[mask_g] - r[mask_g]) / safe_delta[mask_g] + np.float32(2.0)
    hue[mask_b] = (r[mask_b] - g[mask_b]) / safe_delta[mask_b] + np.float32(4.0)

    # 确保 hue 在 [0, 6) 范围内
    hue = hue % np.float32(6.0)

    # 分段线性插值权重
    # hue 在 [i, i+1) 时，weight = weights[i] * (1-frac) + weights[i+1] * frac
    hue_idx = np.floor(hue).astype(np.int32)
    hue_idx = np.clip(hue_idx, 0, 5)
    hue_frac = hue - hue_idx.astype(np.float32)

    # 获取两端权重
    w0 = weights[hue_idx]  # (H, W)
    w1 = weights[hue_idx + 1]  # (H, W)

    # 插值得到最终权重
    interp_weight = w0 * (np.float32(1.0) - hue_frac) + w1 * hue_frac

    # Photoshop Black & White 使用 max(R,G,B) 作为基础亮度
    # 然后乘以权重因子
    luminance = cmax

    # 对于无彩度（delta=0）的像素，权重取 reds（色相=0时的值）
    # 实际上 Photoshop 对纯灰色像素直接保留原亮度
    gray = luminance * interp_weight

    # 对于 delta=0 的像素（纯灰色），保持原始亮度
    gray = np.where(delta > 1e-7, gray, luminance)

    # clamp 到 [0, 1]
    gray = np.clip(gray, _0, _1)

    # 检查是否使用色调（tint）
    use_tint = getattr(layer, 'use_tint', False)
    if use_tint:
        tint_color = getattr(layer, 'tint_color', None)
        if tint_color:
            # tint_color 是 {b'Rd  ': r, b'Grn ': g, b'Bl  ': b} 格式，值范围 0-255
            tr = np.float32(tint_color.get(b'Rd  ', 225.0)) / np.float32(255.0)
            tg = np.float32(tint_color.get(b'Grn ', 211.0)) / np.float32(255.0)
            tb = np.float32(tint_color.get(b'Bl  ', 179.0)) / np.float32(255.0)

            # 色调混合：灰度值作为亮度乘以色调颜色
            result = np.stack([gray * tr, gray * tg, gray * tb], axis=2)
            return result.astype(np.float32)

    # 无色调：3 通道相同灰度值
    result = np.stack([gray, gray, gray], axis=2)
    return result.astype(np.float32)


# ──────────────────────────────────────────────────────────────────────
# draw_stroke_effect 修复
# ──────────────────────────────────────────────────────────────────────
#
# psd-tools 的 draw_stroke_effect 对于完全填满的形状（shape 全为 1）
# 存在 bug：scharr 边缘检测返回全 0 → 归一化 divide(0,0) 变成全 1
# → 描边的 mask 覆盖了整个区域，导致描边颜色替代了填充颜色。
#
# 修复方案：在 draw_stroke_effect 中，当 edges 全为 0（没有实际边缘变化）时，
# 返回空 mask（全 0），表示不需要描边效果。


def _patched_draw_stroke_effect(viewport, shape, desc, psd):
    """修复版本的 draw_stroke_effect，处理 shape 全填满时的边缘情况。"""
    from skimage import filters  # noqa: PLC0415
    from skimage.morphology import disk  # noqa: PLC0415
    from psd_tools.composite import paint, utils
    from psd_tools.terminology import Enum, Key

    height, width = viewport[3] - viewport[1], viewport[2] - viewport[0]
    if not isinstance(shape, np.ndarray):
        shape = np.full((height, width, 1), shape, dtype=np.float32)

    paint_type = desc.get(Key.PaintType).enum
    if paint_type == Enum.SolidColor:
        color, _ = paint.draw_solid_color_fill(viewport, psd.color_mode, desc)
        if color is None:
            color = np.ones((height, width, 1))
    elif paint_type == Enum.Pattern:
        color, _ = paint.draw_pattern_fill(viewport, psd, desc)
        if color is None:
            color = np.ones((height, width, 1))
    elif paint_type == Enum.GradientFill:
        color, _ = paint.draw_gradient_fill(viewport, psd.color_mode, desc)
        if color is None:
            color = np.ones((height, width, 1))
    else:
        logger.warning("No fill specification found in stroke effect.")
        color = np.ones((height, width, 1))

    style = desc.get(Key.Style).enum
    size = float(desc.get(Key.SizeKey, 1.0))
    if style in (Enum.OutsetFrame, Enum.InsetFrame):
        size *= 2

    edges = filters.scharr(shape[:, :, 0])

    # ── 修复：当没有检测到边缘时，返回空 mask ──
    edge_max = np.max(edges)
    if edge_max < 1e-7:
        # shape 完全均匀（全 0 或全 1），没有边缘可以描边
        mask = np.zeros((height, width, 1), dtype=np.float32)
        return color, mask
    # ── 修复结束 ──

    pen = disk(int(size / 2.0 - 1))
    mask = (
        filters.rank.maximum((255 * edges).astype(np.uint8), pen).astype(np.float32)
        / 255.0
    )
    mask = utils.divide(mask - np.min(mask), np.max(mask) - np.min(mask))
    mask = np.expand_dims(mask, 2)

    if style == Enum.OutsetFrame:
        mask = np.maximum(0, mask - shape)
    elif style == Enum.InsetFrame:
        mask = np.maximum(0, mask * shape)

    return color, mask


# ──────────────────────────────────────────────────────────────────────
# 注册到 psd-tools
# ──────────────────────────────────────────────────────────────────────

def register_adjustment_patches():
    """将补丁注册到 psd-tools 的 ADJUSTMENT_FUNC 字典。"""
    try:
        from psd_tools.composite.composite import ADJUSTMENT_FUNC
        if 'blackandwhite' not in ADJUSTMENT_FUNC:
            ADJUSTMENT_FUNC['blackandwhite'] = apply_blackandwhite
            logger.debug("Registered blackandwhite adjustment patch")
    except ImportError:
        logger.warning("Failed to import psd_tools.composite.composite.ADJUSTMENT_FUNC")


def register_stroke_effect_patch():
    """修复 psd-tools 的 draw_stroke_effect 全覆盖 bug。"""
    try:
        import psd_tools.composite.effects as eff_mod
        eff_mod.draw_stroke_effect = _patched_draw_stroke_effect

        # 关键：Compositor._apply_stroke_effect 通过 __globals__ 引用 draw_stroke_effect
        # 需要直接修改方法的 __globals__ 字典
        from psd_tools.composite.composite import Compositor
        Compositor._apply_stroke_effect.__globals__[
            'draw_stroke_effect'
        ] = _patched_draw_stroke_effect
        logger.debug("Registered stroke effect patch")
    except (ImportError, AttributeError) as e:
        logger.warning(f"Failed to patch draw_stroke_effect: {e}")


# 模块级自动注册
register_adjustment_patches()
register_stroke_effect_patch()
