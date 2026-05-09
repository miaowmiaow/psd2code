#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
效果渲染基类模块
定义效果渲染器的统一接口和基础实现
"""

from abc import ABC, abstractmethod
from typing import Any, Optional, Tuple
import numpy as np
from PIL import Image, ImageFilter

from common.image_utils import ImageArrayUtils, ColorUtils


class EffectRenderer(ABC):
    """效果渲染器抽象基类"""
    
    @abstractmethod
    def can_render(self, effect: Any) -> bool:
        """
        判断是否可以渲染此效果
        
        Args:
            effect: PSD 效果对象
        
        Returns:
            True 表示可以渲染
        """
        pass
    
    @abstractmethod
    def render(
        self,
        effect: Any,
        base_alpha: np.ndarray,
        canvas_height: int,
        canvas_width: int,
        **kwargs
    ) -> Optional[np.ndarray]:
        """
        渲染效果
        
        Args:
            effect: PSD 效果对象
            base_alpha: 基础图层的 alpha 通道，形状 (H, W, 1)
            canvas_height: 画布高度
            canvas_width: 画布宽度
            **kwargs: 其他参数（不同效果可能需要不同参数）
        
        Returns:
            渲染后的效果图层数组 (H, W, 4)，如果无法渲染则返回 None
        """
        pass
    
    @staticmethod
    def apply_blur(alpha: np.ndarray, blur_radius: float) -> np.ndarray:
        """
        对 alpha 通道应用高斯模糊
        
        Args:
            alpha: alpha 通道数组，形状 (H, W, 1) 或 (H, W)
            blur_radius: 模糊半径
        
        Returns:
            模糊后的 alpha 数组
        """
        if blur_radius <= 0:
            return alpha
        
        if alpha.ndim == 3:
            alpha_2d = alpha[:, :, 0]
        else:
            alpha_2d = alpha
        
        # 转为 PIL Image 进行模糊
        alpha_img = Image.fromarray((alpha_2d * 255).astype(np.uint8), mode='L')
        blurred_img = alpha_img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        blurred = ImageArrayUtils.pil_l_to_float_array(blurred_img)
        
        if alpha.ndim == 3:
            return blurred[:, :, np.newaxis]
        return blurred
    
    @staticmethod
    def create_solid_color_layer(
        alpha: np.ndarray,
        color_rgb: Tuple[int, int, int],
        opacity: float = 1.0
    ) -> np.ndarray:
        """
        创建纯色效果图层（基于 alpha 形状）
        
        Args:
            alpha: alpha 通道数组，形状 (H, W, 1)
            color_rgb: RGB 颜色 (R, G, B)，0-255
            opacity: 不透明度，0-1
        
        Returns:
            RGBA 数组，形状 (H, W, 4)
        """
        h, w = alpha.shape[:2]
        
        # 归一化颜色
        r, g, b = ColorUtils.rgba_to_normalized(
            color_rgb[0], color_rgb[1], color_rgb[2], 1.0
        )[:3]
        
        # 创建 RGB 层（纯色）
        rgb = np.zeros((h, w, 3), dtype=np.float32)
        rgb[:, :, 0] = r
        rgb[:, :, 1] = g
        rgb[:, :, 2] = b
        
        # 调整 alpha
        adjusted_alpha = alpha * opacity
        
        return ImageArrayUtils.combine_rgba(rgb, adjusted_alpha)


class StrokeRenderer(EffectRenderer):
    """描边效果渲染器"""
    
    def can_render(self, effect: Any) -> bool:
        return str(effect) == 'Stroke'
    
    def render(
        self,
        effect: Any,
        base_alpha: np.ndarray,
        canvas_height: int,
        canvas_width: int,
        **kwargs
    ) -> Optional[np.ndarray]:
        """渲染描边效果"""
        desc = effect.descriptor
        
        # 获取描边参数
        style = desc.get(b'Styl')
        style_val = style.enum if hasattr(style, 'enum') else b'OutF'
        
        size_obj = desc.get(b'Sz  ')
        size = int(size_obj) if size_obj else 1
        
        opacity_obj = desc.get(b'Opct')
        opacity = (int(opacity_obj) / 100.0) if opacity_obj else 1.0
        
        # 获取颜色
        color_obj = desc.get(b'Clr ')
        if not color_obj or not hasattr(color_obj, b'Rd  '):
            return None
        
        r = int(color_obj[b'Rd  '])
        g = int(color_obj[b'Grn '])
        b = int(color_obj[b'Bl  '])
        
        # 根据描边类型渲染
        if style_val == b'InsF':  # 内描边
            return self._render_inner_stroke(base_alpha, (r, g, b), size, opacity)
        elif style_val == b'OutF':  # 外描边
            return self._render_outer_stroke(base_alpha, (r, g, b), size, opacity)
        elif style_val == b'CtrF':  # 居中描边
            return self._render_center_stroke(base_alpha, (r, g, b), size, opacity)
        
        return None
    
    def _render_inner_stroke(
        self,
        alpha: np.ndarray,
        color: Tuple[int, int, int],
        size: int,
        opacity: float
    ) -> np.ndarray:
        """渲染内描边"""
        # 膨胀 alpha
        dilated = self._dilate_alpha(alpha, size)
        
        # 内描边 = 膨胀区域 - 原始区域
        stroke_alpha = np.maximum(dilated - alpha, 0)
        
        return self.create_solid_color_layer(stroke_alpha, color, opacity)
    
    def _render_outer_stroke(
        self,
        alpha: np.ndarray,
        color: Tuple[int, int, int],
        size: int,
        opacity: float
    ) -> np.ndarray:
        """渲染外描边"""
        # 膨胀 alpha
        dilated = self._dilate_alpha(alpha, size)
        
        # 外描边 = 膨胀区域（原始区域会被上层覆盖）
        return self.create_solid_color_layer(dilated, color, opacity)
    
    def _render_center_stroke(
        self,
        alpha: np.ndarray,
        color: Tuple[int, int, int],
        size: int,
        opacity: float
    ) -> np.ndarray:
        """渲染居中描边"""
        # 居中描边 = 外扩一半 + 内缩一半
        outer_size = (size + 1) // 2
        dilated = self._dilate_alpha(alpha, outer_size)
        
        return self.create_solid_color_layer(dilated, color, opacity)
    
    def _dilate_alpha(self, alpha: np.ndarray, size: int) -> np.ndarray:
        """膨胀 alpha 通道（简化版：使用高斯模糊 + 阈值）"""
        if size <= 0:
            return alpha
        
        # 多次膨胀模拟描边扩展
        result = alpha.copy()
        for _ in range(size):
            result = self.apply_blur(result, blur_radius=0.5)
            result = np.minimum(result * 1.5, 1.0)  # 增强并限制
        
        return result


class ShadowRenderer(EffectRenderer):
    """阴影效果渲染器基类"""
    
    def _extract_shadow_params(self, effect: Any) -> dict:
        """提取阴影参数"""
        desc = effect.descriptor
        
        # 颜色
        color_obj = desc.get(b'Clr ')
        if not color_obj or not hasattr(color_obj, b'Rd  '):
            return {}
        
        r = int(color_obj[b'Rd  '])
        g = int(color_obj[b'Grn '])
        b = int(color_obj[b'Bl  '])
        
        # 不透明度
        opacity_obj = desc.get(b'Opct')
        opacity = (int(opacity_obj) / 100.0) if opacity_obj else 0.75
        
        # 角度和距离
        angle_obj = desc.get(b'lagl')
        angle = float(angle_obj) if angle_obj else 120.0
        
        distance_obj = desc.get(b'Dstn')
        distance = int(distance_obj) if distance_obj else 5
        
        # 模糊大小
        blur_obj = desc.get(b'blur')
        blur = int(blur_obj) if blur_obj else 5
        
        return {
            'color': (r, g, b),
            'opacity': opacity,
            'angle': angle,
            'distance': distance,
            'blur': blur
        }


class GlowRenderer(EffectRenderer):
    """发光效果渲染器基类"""
    
    def _extract_glow_params(self, effect: Any) -> dict:
        """提取发光参数"""
        desc = effect.descriptor
        
        # 颜色
        color_obj = desc.get(b'Clr ')
        if not color_obj or not hasattr(color_obj, b'Rd  '):
            return {}
        
        r = int(color_obj[b'Rd  '])
        g = int(color_obj[b'Grn '])
        b = int(color_obj[b'Bl  '])
        
        # 不透明度
        opacity_obj = desc.get(b'Opct')
        opacity = (int(opacity_obj) / 100.0) if opacity_obj else 0.75
        
        # 模糊大小
        blur_obj = desc.get(b'blur')
        blur = int(blur_obj) if blur_obj else 5
        
        return {
            'color': (r, g, b),
            'opacity': opacity,
            'blur': blur
        }
