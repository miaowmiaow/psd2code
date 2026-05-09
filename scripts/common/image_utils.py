#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图像处理公共工具模块
提取常用的 bbox 处理、数组转换、图像混合等方法
"""

from typing import cast
import numpy as np
from PIL import Image


# ======================================================================
# BBox 工具类
# ======================================================================

class BBoxUtils:
    """BBox（边界框）处理工具"""
    
    @staticmethod
    def constrain_to_canvas(
        bbox: tuple[int, int, int, int],
        canvas_w: int,
        canvas_h: int,
    ) -> tuple[int, int, int, int]:
        """
        将 bbox 约束到画布范围内
        
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
    
    @staticmethod
    def expand_bbox(
        bbox: tuple[int, int, int, int],
        expand: int
    ) -> tuple[int, int, int, int]:
        """
        扩展 bbox（用于外描边、投影等效果）
        
        Args:
            bbox: (left, top, right, bottom)
            expand: 扩展像素数
        
        Returns:
            扩展后的 bbox
        """
        left, top, right, bottom = bbox
        return (left - expand, top - expand, right + expand, bottom + expand)
    
    @staticmethod
    def get_dimensions(bbox: tuple[int, int, int, int]) -> tuple[int, int]:
        """
        获取 bbox 的宽度和高度
        
        Args:
            bbox: (left, top, right, bottom)
        
        Returns:
            (width, height)
        """
        left, top, right, bottom = bbox
        return (right - left, bottom - top)
    
    @staticmethod
    def is_valid(bbox: tuple[int, int, int, int]) -> bool:
        """
        检查 bbox 是否有效（宽高都大于0）
        
        Args:
            bbox: (left, top, right, bottom)
        
        Returns:
            是否有效
        """
        left, top, right, bottom = bbox
        return right > left and bottom > top
    
    @staticmethod
    def intersect(
        bbox1: tuple[int, int, int, int],
        bbox2: tuple[int, int, int, int]
    ) -> tuple[int, int, int, int] | None:
        """
        计算两个 bbox 的交集
        
        Args:
            bbox1: 第一个 bbox
            bbox2: 第二个 bbox
        
        Returns:
            交集 bbox，如果不相交则返回 None
        """
        left = max(bbox1[0], bbox2[0])
        top = max(bbox1[1], bbox2[1])
        right = min(bbox1[2], bbox2[2])
        bottom = min(bbox1[3], bbox2[3])
        
        if right <= left or bottom <= top:
            return None
        return (left, top, right, bottom)


# ======================================================================
# 图像数组转换工具类
# ======================================================================

class ImageArrayUtils:
    """图像与 NumPy 数组转换工具"""
    
    @staticmethod
    def pil_to_float_array(img: Image.Image) -> np.ndarray:
        """
        PIL Image 转为浮点数组 [0, 1]
        
        Args:
            img: PIL Image（RGBA）
        
        Returns:
            float32 数组，形状 (H, W, 4)，值域 [0, 1]
        """
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        return np.array(img, dtype=np.float32) / 255.0

    @staticmethod
    def pil_l_to_float_array(img: Image.Image) -> np.ndarray:
        """
        PIL 灰度图（'L' mode，单通道）转为浮点数组 [0, 1]

        用于 alpha/mask/阴影高斯模糊等场景，语义上只关心单通道 0~255 数据。

        Args:
            img: PIL Image，期望 mode='L'；若非 L 会强转为 L

        Returns:
            float32 数组，形状 (H, W)，值域 [0, 1]
        """
        if img.mode != 'L':
            img = img.convert('L')
        return np.array(img, dtype=np.float32) / 255.0

    @staticmethod
    def float_to_uint8_rgba(arr: np.ndarray) -> np.ndarray:
        """
        浮点数组 [0, 1] 夹取后转为 uint8（不包 Image.fromarray）。

        适用于调用方还需要做后续像素操作、或传给其它 PIL 方法的场景。
        """
        return (np.clip(arr, 0, 1) * 255).astype(np.uint8)

    @staticmethod
    def float_array_to_pil(arr: np.ndarray) -> Image.Image:
        """
        浮点数组 [0, 1] 转为 PIL Image
        
        Args:
            arr: float32 数组，形状 (H, W, 3/4)，值域 [0, 1]
        
        Returns:
            PIL Image (RGBA)
        """
        out_uint8 = (np.clip(arr, 0, 1) * 255).astype(np.uint8)
        return Image.fromarray(out_uint8, 'RGBA')
    
    @staticmethod
    def split_rgba(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        分离 RGBA 数组为 RGB 和 Alpha
        
        Args:
            arr: float32 数组，形状 (H, W, 4)
        
        Returns:
            (rgb_array, alpha_array)
            - rgb_array: (H, W, 3)
            - alpha_array: (H, W, 1)
        """
        return arr[:, :, :3], arr[:, :, 3:4]
    
    @staticmethod
    def combine_rgba(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
        """
        合并 RGB 和 Alpha 为 RGBA 数组
        
        Args:
            rgb: RGB 数组，形状 (H, W, 3)
            alpha: Alpha 数组，形状 (H, W, 1) 或 (H, W)
        
        Returns:
            RGBA 数组，形状 (H, W, 4)
        """
        if alpha.ndim == 2:
            alpha = alpha[:, :, np.newaxis]
        return np.concatenate([rgb, alpha], axis=2)
    
    @staticmethod
    def create_blank_canvas(
        height: int,
        width: int,
        channels: int = 4
    ) -> np.ndarray:
        """
        创建空白画布（全透明）
        
        Args:
            height: 画布高度
            width: 画布宽度
            channels: 通道数（3=RGB, 4=RGBA）
        
        Returns:
            全0的 float32 数组
        """
        return np.zeros((height, width, channels), dtype=np.float32)


# ======================================================================
# 图像混合工具类
# ======================================================================

class ImageBlendUtils:
    """图像混合（Alpha Compositing）工具"""
    
    @staticmethod
    def alpha_composite(
        bottom: np.ndarray,
        top: np.ndarray,
        top_alpha_multiplier: float = 1.0
    ) -> np.ndarray:
        """
        Alpha 混合：将 top 图层叠加到 bottom 图层上
        
        使用标准的 Porter-Duff "over" 算法：
        - out_alpha = top_alpha + bottom_alpha * (1 - top_alpha)
        - out_rgb = (top_rgb * top_alpha + bottom_rgb * bottom_alpha * (1 - top_alpha)) / out_alpha
        
        Args:
            bottom: 底层图像数组，形状 (H, W, 4)，float32，值域 [0, 1]
            top: 顶层图像数组，形状 (H, W, 4)，float32，值域 [0, 1]
            top_alpha_multiplier: 顶层透明度乘数（用于调整整体不透明度）
        
        Returns:
            混合后的图像数组，形状 (H, W, 4)
        """
        top_rgb, top_alpha = ImageArrayUtils.split_rgba(top)
        bottom_rgb, bottom_alpha = ImageArrayUtils.split_rgba(bottom)
        
        # 调整顶层透明度
        top_alpha = top_alpha * top_alpha_multiplier
        
        # 计算输出 alpha
        out_alpha = top_alpha + bottom_alpha * (1 - top_alpha)
        
        # 计算输出 RGB（处理除零情况）
        epsilon = 1e-8
        out_rgb = (
            top_rgb * top_alpha + bottom_rgb * bottom_alpha * (1 - top_alpha)
        ) / (out_alpha + epsilon)
        
        return ImageArrayUtils.combine_rgba(out_rgb, out_alpha)
    
    @staticmethod
    def paste_on_canvas(
        canvas: np.ndarray,
        image: np.ndarray,
        offset_x: int,
        offset_y: int,
        blend: bool = True
    ) -> None:
        """
        将图像粘贴到画布上（原地修改）
        
        Args:
            canvas: 画布数组，形状 (H, W, 4)
            image: 要粘贴的图像，形状 (h, w, 4)
            offset_x: X 偏移量
            offset_y: Y 偏移量
            blend: 是否使用 alpha 混合（False 则直接覆盖）
        """
        h: int = cast(int, image.shape[0])
        w: int = cast(int, image.shape[1])
        canvas_h: int = cast(int, canvas.shape[0])
        canvas_w: int = cast(int, canvas.shape[1])
        
        # 计算有效的粘贴区域
        paste_x1 = max(0, offset_x)
        paste_y1 = max(0, offset_y)
        paste_x2 = min(canvas_w, offset_x + w)
        paste_y2 = min(canvas_h, offset_y + h)
        
        if paste_x2 <= paste_x1 or paste_y2 <= paste_y1:
            return  # 完全在画布外
        
        # 计算源图像对应的区域
        src_x1 = paste_x1 - offset_x
        src_y1 = paste_y1 - offset_y
        src_x2 = paste_x2 - offset_x
        src_y2 = paste_y2 - offset_y
        
        if blend:
            canvas[paste_y1:paste_y2, paste_x1:paste_x2] = ImageBlendUtils.alpha_composite(
                canvas[paste_y1:paste_y2, paste_x1:paste_x2],
                image[src_y1:src_y2, src_x1:src_x2]
            )
        else:
            canvas[paste_y1:paste_y2, paste_x1:paste_x2] = image[src_y1:src_y2, src_x1:src_x2]
    
    @staticmethod
    def multiply_alpha(image: np.ndarray, alpha_multiplier: float) -> np.ndarray:
        """
        调整图像整体不透明度
        
        Args:
            image: 图像数组，形状 (H, W, 4)
            alpha_multiplier: 透明度乘数
        
        Returns:
            调整后的图像数组
        """
        result = image.copy()
        result[:, :, 3] *= alpha_multiplier
        return result


# ======================================================================
# 颜色工具类
# ======================================================================

class ColorUtils:
    """颜色处理工具"""
    
    @staticmethod
    def rgb_to_tuple(rgb_value: int) -> tuple[int, int, int]:
        """
        将整数 RGB 值转为 (R, G, B) 元组
        
        Args:
            rgb_value: 24位整数，格式 0xRRGGBB
        
        Returns:
            (R, G, B) 元组，每个值 0-255
        """
        r = (rgb_value >> 16) & 0xFF
        g = (rgb_value >> 8) & 0xFF
        b = rgb_value & 0xFF
        return (r, g, b)
    
    @staticmethod
    def rgba_to_normalized(r: int, g: int, b: int, a: float) -> tuple[float, float, float, float]:
        """
        将 RGBA 值归一化到 [0, 1]
        
        Args:
            r, g, b: 0-255
            a: 0.0-1.0
        
        Returns:
            (r, g, b, a) 归一化后的值
        """
        return (r / 255.0, g / 255.0, b / 255.0, a)
