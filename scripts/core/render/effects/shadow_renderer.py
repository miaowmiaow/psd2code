#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阴影效果渲染器
支持：投影 (DropShadow)、内阴影 (InnerShadow)
"""

from typing import Any
import math
import numpy as np
from PIL import Image, ImageFilter

from common.image_utils import ImageArrayUtils


class ShadowRenderer:
    """阴影效果渲染器基类"""
    
    @staticmethod
    def _extract_color(desc: Any) -> tuple[float, float, float]:
        """提取颜色，返回 (r, g, b) 范围 [0, 1]"""
        try:
            clr = desc.get(b'Clr ')
            if clr is not None:
                r = float(clr.get(b'Rd  ', 0)) / 255.0
                g = float(clr.get(b'Grn ', 0)) / 255.0
                b = float(clr.get(b'Bl  ', 0)) / 255.0
                return (r, g, b)
        except Exception:
            pass
        return (0.0, 0.0, 0.0)


class DropShadowRenderer(ShadowRenderer):
    """投影效果渲染器"""
    
    @staticmethod
    def can_render(effect: Any) -> bool:
        """判断是否为投影效果"""
        return str(effect) == 'DropShadow'
    
    @staticmethod
    def render(
        effect: Any, 
        alpha: np.ndarray, 
        h: int, 
        w: int, 
        expand: int
    ) -> np.ndarray | None:
        """
        渲染投影效果
        
        Args:
            effect: PSD 投影效果对象
            alpha: 基础图层的 alpha 通道，形状 (H, W, 1)
            h: 画布高度
            w: 画布宽度
            expand: 画布扩展量
        
        Returns:
            渲染后的投影图层数组 (H, W, 4)，如果失败则返回 None
        """
        try:
            desc = effect.descriptor
            color = DropShadowRenderer._extract_color(desc)
            opacity = effect.opacity / 100.0
            blur = int(float(desc.get(b'blur', 0)))
            dist = int(float(desc.get(b'Dstn', 0)))
            angle_deg = float(desc.get(b'lagl', 120))
            
            # Photoshop 角度 → 像素偏移
            # PS 角度表示光源方向（逆时针，0°=右，90°=上），投影在光源反方向
            # 屏幕坐标系：X右为正，Y下为正
            # dx = -cos(angle) * dist, dy = sin(angle) * dist
            angle_rad = math.radians(angle_deg)
            dx = int(round(-dist * math.cos(angle_rad)))
            dy = int(round(dist * math.sin(angle_rad)))
            
            # 图层挖空投影（layerConceals）
            layer_conceals = bool(desc.get(b'layerConceals', True))
            
            # 以 alpha 为形状绘制阴影
            shadow_alpha = alpha[:, :, 0].copy()
            
            # 高斯模糊
            if blur > 0:
                pil_alpha = Image.fromarray((shadow_alpha * 255).astype(np.uint8), 'L')
                pil_alpha = pil_alpha.filter(ImageFilter.GaussianBlur(radius=blur))
                shadow_alpha = ImageArrayUtils.pil_l_to_float_array(pil_alpha)
            
            # 应用偏移
            shifted = np.zeros_like(shadow_alpha)
            src_y0 = max(0, -dy)
            src_y1 = min(h, h - dy)
            src_x0 = max(0, -dx)
            src_x1 = min(w, w - dx)
            dst_y0 = max(0, dy)
            dst_y1 = min(h, h + dy)
            dst_x0 = max(0, dx)
            dst_x1 = min(w, w + dx)
            copy_h = min(src_y1 - src_y0, dst_y1 - dst_y0)
            copy_w = min(src_x1 - src_x0, dst_x1 - dst_x0)
            if copy_h > 0 and copy_w > 0:
                shifted[dst_y0:dst_y0 + copy_h, dst_x0:dst_x0 + copy_w] = \
                    shadow_alpha[src_y0:src_y0 + copy_h, src_x0:src_x0 + copy_w]
            
            # 图层挖空投影：在原图不透明区域下方的投影被挖掉
            if layer_conceals:
                orig_alpha = alpha[:, :, 0]
                shifted = shifted * (1.0 - orig_alpha)
            
            out = np.zeros((h, w, 4), dtype=np.float32)
            out[:, :, 0] = color[0]
            out[:, :, 1] = color[1]
            out[:, :, 2] = color[2]
            out[:, :, 3] = shifted * opacity
            return out
            
        except Exception as e:
            print(f"      ⚠️  投影渲染失败: {e}")
            return None
    
    @staticmethod
    def calc_expand(effect: Any) -> int:
        """计算投影需要扩展的像素数"""
        desc = effect.descriptor
        blur = int(float(desc.get(b'blur', 0)))
        dist = int(float(desc.get(b'Dstn', 0)))
        return blur + dist + 4


class InnerShadowRenderer(ShadowRenderer):
    """内阴影效果渲染器"""
    
    @staticmethod
    def can_render(effect: Any) -> bool:
        """判断是否为内阴影效果"""
        return str(effect) == 'InnerShadow'
    
    @staticmethod
    def render(
        effect: Any, 
        alpha: np.ndarray, 
        h: int, 
        w: int
    ) -> np.ndarray | None:
        """
        渲染内阴影效果
        
        Args:
            effect: PSD 内阴影效果对象
            alpha: 基础图层的 alpha 通道，形状 (H, W, 1)
            h: 画布高度
            w: 画布宽度
        
        Returns:
            渲染后的内阴影图层数组 (H, W, 4)，如果失败则返回 None
        """
        try:
            desc = effect.descriptor
            color = InnerShadowRenderer._extract_color(desc)
            opacity = effect.opacity / 100.0
            blur = int(float(desc.get(b'blur', 0)))
            dist = int(float(desc.get(b'Dstn', 0)))
            angle_deg = float(desc.get(b'lagl', 120))
            
            # Photoshop 角度 → 像素偏移（与 DropShadow 相同）
            angle_rad = math.radians(angle_deg)
            dx = int(round(-dist * math.cos(angle_rad)))
            dy = int(round(dist * math.sin(angle_rad)))
            
            # 反转 alpha
            inv_alpha = 1.0 - alpha[:, :, 0]
            
            # 偏移
            shifted = np.zeros_like(inv_alpha)
            src_y0 = max(0, -dy)
            src_y1 = min(h, h - dy)
            src_x0 = max(0, -dx)
            src_x1 = min(w, w - dx)
            dst_y0 = max(0, dy)
            dst_y1 = min(h, h + dy)
            dst_x0 = max(0, dx)
            dst_x1 = min(w, w + dx)
            copy_h = min(src_y1 - src_y0, dst_y1 - dst_y0)
            copy_w = min(src_x1 - src_x0, dst_x1 - dst_x0)
            if copy_h > 0 and copy_w > 0:
                shifted[dst_y0:dst_y0 + copy_h, dst_x0:dst_x0 + copy_w] = \
                    inv_alpha[src_y0:src_y0 + copy_h, src_x0:src_x0 + copy_w]
            
            # 模糊
            if blur > 0:
                pil_sh = Image.fromarray((shifted * 255).astype(np.uint8), 'L')
                pil_sh = pil_sh.filter(ImageFilter.GaussianBlur(radius=blur))
                shifted = ImageArrayUtils.pil_l_to_float_array(pil_sh)
            
            # 内阴影只在原图 alpha 范围内
            shadow_mask = shifted * alpha[:, :, 0]
            
            out = np.zeros((h, w, 4), dtype=np.float32)
            out[:, :, 0] = color[0]
            out[:, :, 1] = color[1]
            out[:, :, 2] = color[2]
            out[:, :, 3] = shadow_mask * opacity
            return out
            
        except Exception as e:
            print(f"      ⚠️  内阴影渲染失败: {e}")
            return None
