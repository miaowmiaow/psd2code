#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
发光效果渲染器
支持：外发光 (OuterGlow)、内发光 (InnerGlow)
"""

from typing import Any
import numpy as np
from PIL import Image, ImageFilter

from common.image_utils import ImageArrayUtils


class GlowRenderer:
    """发光效果渲染器基类"""
    
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


class OuterGlowRenderer(GlowRenderer):
    """外发光效果渲染器"""
    
    @staticmethod
    def can_render(effect: Any) -> bool:
        """判断是否为外发光效果"""
        return str(effect) == 'OuterGlow'
    
    @staticmethod
    def render(
        effect: Any, 
        alpha: np.ndarray, 
        h: int, 
        w: int
    ) -> np.ndarray | None:
        """
        渲染外发光效果
        
        Args:
            effect: PSD 外发光效果对象
            alpha: 基础图层的 alpha 通道，形状 (H, W, 1)
            h: 画布高度
            w: 画布宽度
        
        Returns:
            渲染后的外发光图层数组 (H, W, 4)，如果失败则返回 None
        """
        try:
            desc = effect.descriptor
            color = OuterGlowRenderer._extract_color(desc)
            opacity = effect.opacity / 100.0
            blur = int(float(desc.get(b'blur', 0)))
            
            glow_alpha = alpha[:, :, 0].copy()
            
            if blur > 0:
                pil_alpha = Image.fromarray((glow_alpha * 255).astype(np.uint8), 'L')
                pil_alpha = pil_alpha.filter(ImageFilter.GaussianBlur(radius=blur))
                glow_alpha = ImageArrayUtils.pil_l_to_float_array(pil_alpha)
            
            out = np.zeros((h, w, 4), dtype=np.float32)
            out[:, :, 0] = color[0]
            out[:, :, 1] = color[1]
            out[:, :, 2] = color[2]
            out[:, :, 3] = glow_alpha * opacity
            return out
            
        except Exception as e:
            print(f"      ⚠️  外发光渲染失败: {e}")
            return None
    
    @staticmethod
    def calc_expand(effect: Any) -> int:
        """计算外发光需要扩展的像素数"""
        desc = effect.descriptor
        blur = int(float(desc.get(b'blur', 0)))
        return blur + 4


class InnerGlowRenderer(GlowRenderer):
    """内发光效果渲染器"""
    
    @staticmethod
    def can_render(effect: Any) -> bool:
        """判断是否为内发光效果"""
        return str(effect) == 'InnerGlow'
    
    @staticmethod
    def render(
        effect: Any, 
        alpha: np.ndarray, 
        h: int, 
        w: int
    ) -> np.ndarray | None:
        """
        渲染内发光效果（在原图内侧发光）
        
        Args:
            effect: PSD 内发光效果对象
            alpha: 基础图层的 alpha 通道，形状 (H, W, 1)
            h: 画布高度
            w: 画布宽度
        
        Returns:
            渲染后的内发光图层数组 (H, W, 4)，如果失败则返回 None
        """
        try:
            desc = effect.descriptor
            color = InnerGlowRenderer._extract_color(desc)
            opacity = effect.opacity / 100.0
            blur = int(float(desc.get(b'blur', 0)))
            
            # 反转 alpha → 从边缘向内模糊
            inv_alpha = 1.0 - alpha[:, :, 0]
            
            if blur > 0:
                pil_inv = Image.fromarray((inv_alpha * 255).astype(np.uint8), 'L')
                pil_inv = pil_inv.filter(ImageFilter.GaussianBlur(radius=blur))
                inv_alpha = ImageArrayUtils.pil_l_to_float_array(pil_inv)
            
            # 内发光只在原图 alpha 范围内
            glow_mask = inv_alpha * alpha[:, :, 0]
            
            out = np.zeros((h, w, 4), dtype=np.float32)
            out[:, :, 0] = color[0]
            out[:, :, 1] = color[1]
            out[:, :, 2] = color[2]
            out[:, :, 3] = glow_mask * opacity
            return out
            
        except Exception as e:
            print(f"      ⚠️  内发光渲染失败: {e}")
            return None
