#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图层效果渲染模块（主协调器）
- 调用专门的渲染器处理各种效果
- 管理渲染顺序和画布合成
- 外描边 / 外发光 / 投影会自动扩展画布，返回新的 bbox
"""

from typing import Any

import numpy as np
from PIL import Image

from common.image_utils import ImageArrayUtils

from .stroke_renderer import StrokeRenderer
from .shadow_renderer import DropShadowRenderer, InnerShadowRenderer
from .glow_renderer import OuterGlowRenderer, InnerGlowRenderer
from .overlay_renderer import ColorOverlayRenderer, GradientOverlayRenderer


# ======================================================================
# 公开 API
# ======================================================================

def render_layer_with_effects(layer: Any) -> tuple[Image.Image, tuple[int, int, int, int]] | None:
    """
    渲染图层并叠加所有启用的效果（描边、阴影、发光）。

    如果图层没有任何效果，直接返回原始图片 + 原始 bbox。
    如果效果需要扩展画布（外描边/外发光/投影），会自动扩展并
    返回扩展后的 bbox。

    Args:
        layer: psd-tools 图层对象

    Returns:
        (PIL.Image RGBA, expanded_bbox) 或 None（图层为空时）
    """
    try:
        # 1. 获取基础图层图片
        base_img = layer.topil()

        # 【修复】对于 shape 图层，topil() 可能返回 None
        # 优先尝试用"SoCo 填充色 + origination 几何"自己合成（避免 composite() 把
        # 图层样式描边的颜色错误地铺满整个 shape，典型 bug：领奖.psd 矩形 13）
        if base_img is None:
            base_img = _render_shape_base_from_fill(layer)

        # 兜底：用 composite() 获取基础图像（无 SoCo 或非规则形状时）
        if base_img is None and hasattr(layer, 'composite'):
            try:
                base_img = layer.composite()
            except Exception:
                pass

        if base_img is None:
            return None

        if base_img.mode != 'RGBA':
            base_img = base_img.convert('RGBA')

        base_arr = ImageArrayUtils.pil_to_float_array(base_img)
        h, w = base_arr.shape[:2]

        base_rgb = base_arr[:, :, :3]
        base_alpha = base_arr[:, :, 3:4]

        # 2. 如果没有效果，直接返回
        if not _has_effects(layer):
            return base_img, layer.bbox

        # 3. 计算需要扩展的像素数（外描边 / 外发光 / 投影）
        expand = _calc_expand(layer)

        # 4. 在扩展画布上放置基础图层
        new_h = h + expand * 2
        new_w = w + expand * 2

        ext_rgb = np.zeros((new_h, new_w, 3), dtype=np.float32)
        ext_alpha = np.zeros((new_h, new_w, 1), dtype=np.float32)
        ext_rgb[expand:expand + h, expand:expand + w, :] = base_rgb
        ext_alpha[expand:expand + h, expand:expand + w, :] = base_alpha

        # 5. 逐个渲染效果，收集到 compositing 队列
        #    排序：投影 → 外发光 → 外描边(底) → 原图(顶) → 内描边 → 内发光 → 内阴影
        #
        #    PS 效果列表中，先出现的效果在"上层"（更靠近图层本身）。
        #    对于外描边：先出现的（通常更窄）应该在合成时更靠近 base（后画），
        #    后出现的（通常更宽）应该先画（在底层）。
        #    因此外描边需要反转顺序后加入 below_layers。
        below_layers: list[np.ndarray] = []   # 叠在原图下方
        above_layers: list[np.ndarray] = []   # 叠在原图上方
        outer_strokes: list[np.ndarray] = []  # 外描边/居中描边（按效果列表顺序收集，最后反转）

        for effect in layer.effects:
            if not is_effect_active(effect, layer):
                continue

            name = str(effect)

            # 使用专门的渲染器
            if DropShadowRenderer.can_render(effect):
                arr = DropShadowRenderer.render(effect, ext_alpha, new_h, new_w, expand)
                if arr is not None:
                    below_layers.insert(0, arr)  # 投影最底层

            elif OuterGlowRenderer.can_render(effect):
                arr = OuterGlowRenderer.render(effect, ext_alpha, new_h, new_w)
                if arr is not None:
                    below_layers.append(arr)

            elif StrokeRenderer.can_render(effect):
                arr = StrokeRenderer.render(effect, layer, ext_alpha, new_h, new_w)
                if arr is not None:
                    # 内描边叠在原图上方，外描边/居中描边收集后反转
                    style = StrokeRenderer.get_stroke_style(effect)
                    if style == 'InsF':
                        above_layers.append(arr)
                    else:
                        outer_strokes.append(arr)

            elif InnerGlowRenderer.can_render(effect):
                arr = InnerGlowRenderer.render(effect, ext_alpha, new_h, new_w)
                if arr is not None:
                    above_layers.append(arr)

            elif InnerShadowRenderer.can_render(effect):
                arr = InnerShadowRenderer.render(effect, ext_alpha, new_h, new_w)
                if arr is not None:
                    above_layers.append(arr)

            elif ColorOverlayRenderer.can_render(effect):
                ColorOverlayRenderer.apply(effect, ext_rgb, ext_alpha, new_h, new_w)

            elif GradientOverlayRenderer.can_render(effect):
                GradientOverlayRenderer.apply(effect, ext_rgb, ext_alpha, new_h, new_w)

        # 外描边反转顺序后加入 below_layers：
        # 效果列表中后出现的（更宽）先画（底层），先出现的（更窄）后画（靠近 base）
        for stroke_arr in reversed(outer_strokes):
            below_layers.append(stroke_arr)

        # 6. 合成：below → base → above
        canvas = np.zeros((new_h, new_w, 4), dtype=np.float32)

        for layer_arr in below_layers:
            canvas = _alpha_composite(canvas, layer_arr)

        base_full = np.zeros((new_h, new_w, 4), dtype=np.float32)
        base_full[:, :, :3] = ext_rgb
        base_full[:, :, 3:4] = ext_alpha
        canvas = _alpha_composite(canvas, base_full)

        for layer_arr in above_layers:
            canvas = _alpha_composite(canvas, layer_arr)

        # 7. 转回 PIL
        result_img = ImageArrayUtils.float_array_to_pil(canvas)

        # 8. 计算扩展后的 bbox
        bbox = layer.bbox
        new_bbox = (
            bbox[0] - expand,
            bbox[1] - expand,
            bbox[2] + expand,
            bbox[3] + expand,
        )

        return result_img, new_bbox

    except Exception as e:
        print(f"      ⚠️  效果渲染失败: {e}")
        return None


def render_layer_with_effects_on_image(
    layer: Any,
    base_img: Image.Image,
    base_bbox: tuple[int, int, int, int],
) -> tuple[Image.Image, tuple[int, int, int, int]] | None:
    """
    在给定图像上应用指定图层的效果。
    用于剪切蒙版场景：先合成 base+clips，再应用 base 的效果。

    Args:
        layer: psd-tools 图层对象（提供效果列表）
        base_img: 已合成的 RGBA 图像
        base_bbox: 图像对应的画布 bbox

    Returns:
        (PIL.Image RGBA, expanded_bbox) 或 None
    """
    try:
        if base_img.mode != 'RGBA':
            base_img = base_img.convert('RGBA')

        base_arr = ImageArrayUtils.pil_to_float_array(base_img)
        h, w = base_arr.shape[:2]

        base_rgb = base_arr[:, :, :3]
        base_alpha = base_arr[:, :, 3:4]

        if not _has_effects(layer):
            return base_img, base_bbox

        expand = _calc_expand(layer)

        new_h = h + expand * 2
        new_w = w + expand * 2

        ext_rgb = np.zeros((new_h, new_w, 3), dtype=np.float32)
        ext_alpha = np.zeros((new_h, new_w, 1), dtype=np.float32)
        ext_rgb[expand:expand + h, expand:expand + w, :] = base_rgb
        ext_alpha[expand:expand + h, expand:expand + w, :] = base_alpha

        below_layers: list[np.ndarray] = []
        above_layers: list[np.ndarray] = []
        outer_strokes: list[np.ndarray] = []

        for effect in layer.effects:
            if not is_effect_active(effect, layer):
                continue

            if DropShadowRenderer.can_render(effect):
                arr = DropShadowRenderer.render(effect, ext_alpha, new_h, new_w, expand)
                if arr is not None:
                    below_layers.insert(0, arr)

            elif OuterGlowRenderer.can_render(effect):
                arr = OuterGlowRenderer.render(effect, ext_alpha, new_h, new_w)
                if arr is not None:
                    below_layers.append(arr)

            elif StrokeRenderer.can_render(effect):
                arr = StrokeRenderer.render(effect, layer, ext_alpha, new_h, new_w)
                if arr is not None:
                    style = StrokeRenderer.get_stroke_style(effect)
                    if style == 'InsF':
                        above_layers.append(arr)
                    else:
                        outer_strokes.append(arr)

            elif InnerGlowRenderer.can_render(effect):
                arr = InnerGlowRenderer.render(effect, ext_alpha, new_h, new_w)
                if arr is not None:
                    above_layers.append(arr)

            elif InnerShadowRenderer.can_render(effect):
                arr = InnerShadowRenderer.render(effect, ext_alpha, new_h, new_w)
                if arr is not None:
                    above_layers.append(arr)

            elif ColorOverlayRenderer.can_render(effect):
                ColorOverlayRenderer.apply(effect, ext_rgb, ext_alpha, new_h, new_w)

            elif GradientOverlayRenderer.can_render(effect):
                GradientOverlayRenderer.apply(effect, ext_rgb, ext_alpha, new_h, new_w)

        # 外描边反转顺序后加入 below_layers
        for stroke_arr in reversed(outer_strokes):
            below_layers.append(stroke_arr)

        canvas = np.zeros((new_h, new_w, 4), dtype=np.float32)

        for layer_arr in below_layers:
            canvas = _alpha_composite(canvas, layer_arr)

        base_full = np.zeros((new_h, new_w, 4), dtype=np.float32)
        base_full[:, :, :3] = ext_rgb
        base_full[:, :, 3:4] = ext_alpha
        canvas = _alpha_composite(canvas, base_full)

        for layer_arr in above_layers:
            canvas = _alpha_composite(canvas, layer_arr)

        result_img = ImageArrayUtils.float_array_to_pil(canvas)

        new_bbox = (
            base_bbox[0] - expand,
            base_bbox[1] - expand,
            base_bbox[2] + expand,
            base_bbox[3] + expand,
        )

        return result_img, new_bbox

    except Exception as e:
        print(f"      ⚠️  效果渲染(on_image)失败: {e}")
        return None


# ======================================================================
# 内部工具
# ======================================================================

def _layer_fx_master_enabled(layer: Any) -> bool:
    """
    图层样式整体开关是否启用（PS 图层面板里 fx 行最左侧的总开关）。
    PSD 中 layer.effects.enabled=False 时，PS 实际不渲染任何效果，
    即使子项 effect.enabled=True。
    """
    if not hasattr(layer, 'effects') or not layer.effects:
        return False
    return bool(getattr(layer.effects, 'enabled', True))


def is_effect_active(effect: Any, layer: Any) -> bool:
    """单个效果是否真正生效：整体开关 AND 子项开关 都为 True。"""
    if not _layer_fx_master_enabled(layer):
        return False
    return bool(getattr(effect, 'enabled', False))


def _has_effects(layer: Any) -> bool:
    """图层是否有任何真正生效的效果（同时考虑整体开关与子项开关）"""
    if not _layer_fx_master_enabled(layer):
        return False
    return any(e.enabled for e in layer.effects)


def _calc_expand(layer: Any) -> int:
    """
    根据外描边 / 外发光 / 投影的参数计算需要扩展的像素。
    使用各个渲染器的 calc_expand 方法。
    """
    expand = 0
    for effect in layer.effects:
        if not is_effect_active(effect, layer):
            continue

        if StrokeRenderer.can_render(effect):
            expand = max(expand, StrokeRenderer.calc_expand(effect))
        elif OuterGlowRenderer.can_render(effect):
            expand = max(expand, OuterGlowRenderer.calc_expand(effect))
        elif DropShadowRenderer.can_render(effect):
            expand = max(expand, DropShadowRenderer.calc_expand(effect))

    return expand


def _alpha_composite(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    """
    标准 Porter-Duff src-over 合成。
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


def _render_shape_base_from_fill(layer: Any) -> Image.Image | None:
    """
    用"SoCo 纯色填充 + origination 几何"自己合成 shape 图层的基础图。

    适用场景：
        shape 图层 + 有 SoCo 填充色 + origination 是 Rectangle/RoundedRectangle/Ellipse
        且 layer.topil() 返回 None。

    为什么需要：
        psd-tools 的 layer.composite() 在 shape 图层带"图层样式描边"（lfx2 的 FrFX）时
        有 bug：会把整个 shape 区域涂成描边颜色，而不是"内部填充色 + 描边带描边色"。
        典型案例：领奖.psd "矩形 13"（SoCo=#fff36c, OutF stroke=#5f8618）composite 后
        整张 630×112 图全部变成 #5f8618。

    解决思路：绕开 composite 的 effect 合成，自己根据 SoCo + 几何信息构造
    "内部全填充色"的基础图（alpha 为 shape 形状），让 effects_renderer 后续的
    StrokeRenderer 自己叠加描边。

    Args:
        layer: psd-tools 图层对象

    Returns:
        PIL.Image (RGBA) 或 None（不满足条件时）
    """
    try:
        # 必须是 shape 类型
        if not hasattr(layer, 'kind') or layer.kind != 'shape':
            return None

        # 必须有 SoCo 填充色
        soco = None
        if hasattr(layer, 'tagged_blocks'):
            try:
                soco = layer.tagged_blocks.get_data(b'SoCo')
            except Exception:
                soco = None
        if not soco:
            return None

        clr = soco.get(b'Clr ')
        if not clr:
            return None
        try:
            r = int(round(float(clr.get(b'Rd  ', 0))))
            g = int(round(float(clr.get(b'Grn ', 0))))
            b = int(round(float(clr.get(b'Bl  ', 0))))
        except Exception:
            return None
        r = max(0, min(255, r))
        g = max(0, min(255, g))
        b = max(0, min(255, b))

        bbox = layer.bbox
        if bbox is None:
            return None
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if w <= 0 or h <= 0:
            return None

        # 根据 origination 几何形状生成 alpha mask
        from PIL import ImageDraw
        mask = Image.new('L', (w, h), 0)
        draw = ImageDraw.Draw(mask)

        orig_list = getattr(layer, 'origination', None) or []
        orig = orig_list[0] if orig_list else None
        orig_type = type(orig).__name__ if orig else ''

        if orig_type == 'Rectangle':
            # 直角矩形：整片填充
            draw.rectangle((0, 0, w, h), fill=255)
        elif orig_type == 'RoundedRectangle':
            # 圆角矩形：用 PIL 的 rounded_rectangle
            radii = getattr(orig, 'radii', None) or {}
            try:
                tl = float(radii.get(b'topLeft', 0)) if radii else 0
                tr = float(radii.get(b'topRight', 0)) if radii else 0
                bl = float(radii.get(b'bottomLeft', 0)) if radii else 0
                br = float(radii.get(b'bottomRight', 0)) if radii else 0
            except Exception:
                tl = tr = bl = br = 0
            # PIL 不支持四角不同半径，取均值（绝大多数 PSD 圆角矩形四角相同）
            radius = int(round((tl + tr + bl + br) / 4))
            radius = max(0, min(radius, min(w, h) // 2))
            if radius > 0:
                if hasattr(draw, 'rounded_rectangle'):
                    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
                else:
                    draw.rectangle((0, 0, w, h), fill=255)
            else:
                draw.rectangle((0, 0, w, h), fill=255)
        elif orig_type == 'Ellipse':
            draw.ellipse((0, 0, w - 1, h - 1), fill=255)
        else:
            # Invalidated / 其他自定义几何：放弃，让上层走 composite() 兜底
            # （Invalidated 通常意味着用户把矩形拉变形过，几何信息丢失）
            return None

        # 合成 RGBA
        rgba = Image.new('RGBA', (w, h), (r, g, b, 0))
        rgba.putalpha(mask)
        return rgba

    except Exception as e:
        print(f"      ⚠️  shape 基础图自渲染失败: {e}")
        return None
