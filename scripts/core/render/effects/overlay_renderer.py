#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
叠加效果渲染器
支持：颜色叠加 (ColorOverlay)、渐变叠加 (GradientOverlay)
"""

from typing import Any
import math
import numpy as np


class ColorOverlayRenderer:
    """颜色叠加效果渲染器"""
    
    @staticmethod
    def can_render(effect: Any) -> bool:
        """判断是否为颜色叠加效果"""
        return str(effect) == 'ColorOverlay'
    
    @staticmethod
    def apply(
        effect: Any, 
        ext_rgb: np.ndarray, 
        ext_alpha: np.ndarray, 
        h: int, 
        w: int
    ) -> None:
        """
        渲染颜色叠加效果（ColorOverlay）。
        直接修改 ext_rgb，用叠加颜色替换原图 RGB（在 alpha > 0 的区域）。
        
        Photoshop 的 ColorOverlay（Normal 混合模式）行为：
        - opacity=100% 时，完全替换所有有像素区域的 RGB 为叠加颜色
        - opacity<100% 时，按 opacity 混合原始 RGB 和叠加颜色
        - 混合公式只依赖 effect opacity，不依赖像素自身的 alpha 值
        
        Args:
            effect: PSD 颜色叠加效果对象
            ext_rgb: RGB 数组，形状 (H, W, 3)，会被直接修改
            ext_alpha: Alpha 通道，形状 (H, W, 1)
            h: 画布高度
            w: 画布宽度
        """
        try:
            color = ColorOverlayRenderer._extract_color(effect.descriptor)
            opacity = effect.opacity / 100.0
            
            # 在有 alpha 的区域（alpha > 0），按 opacity 混合 RGB
            # 正确公式: new_rgb = orig_rgb * (1 - opacity) + overlay_color * opacity
            # 注意：这里不用 alpha 作为 mask，因为 PS 的 ColorOverlay 只看 opacity
            has_pixel = (ext_alpha[:, :, 0] > 0).astype(np.float32)
            overlay_color = np.array(color)[np.newaxis, np.newaxis, :]
            
            blended = ext_rgb * (1.0 - opacity) + overlay_color * opacity
            has_pixel_3d = has_pixel[:, :, np.newaxis]
            
            # 只在有像素的区域替换 RGB，透明区域保持不变
            ext_rgb[:, :, :] = ext_rgb * (1.0 - has_pixel_3d) + blended * has_pixel_3d
            
        except Exception as e:
            print(f"      ⚠️  颜色叠加渲染失败: {e}")
    
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


class GradientOverlayRenderer:
    """渐变叠加效果渲染器"""
    
    @staticmethod
    def can_render(effect: Any) -> bool:
        """判断是否为渐变叠加效果"""
        return str(effect) == 'GradientOverlay'
    
    @staticmethod
    def apply(
        effect: Any, 
        ext_rgb: np.ndarray, 
        ext_alpha: np.ndarray, 
        h: int, 
        w: int
    ) -> None:
        """
        渲染渐变叠加效果（GradientOverlay）。
        直接修改 ext_rgb，在有像素区域用渐变色替换/混合 RGB。
        
        Photoshop 的 GradientOverlay 行为：
        - 在图层有像素的区域按指定角度、类型绘制渐变色
        - 支持线性（Linear）、径向（Radial）等类型
        - 渐变色带由多个色标和透明度标控制
        - opacity 控制渐变与原图的混合程度
        - reversed 标志翻转渐变方向
        
        Args:
            effect: PSD 渐变叠加效果对象
            ext_rgb: RGB 数组，形状 (H, W, 3)，会被直接修改
            ext_alpha: Alpha 通道，形状 (H, W, 1)
            h: 画布高度
            w: 画布宽度
        """
        try:
            desc = effect.descriptor
            opacity = effect.opacity / 100.0
            
            # 解析渐变角度
            angle_deg = float(desc.get(b'Angl', 90))
            reversed_grad = bool(desc.get(b'Rvrs', False))
            
            # 解析缩放比例（PS 默认 100%）
            scale_pct = float(desc.get(b'Scl ', 100))
            grad_scale = scale_pct / 100.0
            
            # 解析"与图层对齐"标志（Align with Layer）
            align_with_layer = bool(desc.get(b'Algn', True))
            
            # 解析偏移（Offset）- 百分比偏移
            ofst = desc.get(b'Ofst')
            offset_x_pct = 0.0
            offset_y_pct = 0.0
            if ofst is not None:
                offset_x_pct = float(ofst.get(b'Hrzn', 0.0)) / 100.0
                offset_y_pct = float(ofst.get(b'Vrtc', 0.0)) / 100.0
            
            # 解析渐变类型（Linear / Radial / Angle / Reflected / Diamond）
            grad_type = desc.get(b'Type')
            grad_type_str = ''
            if hasattr(grad_type, 'enum'):
                grad_type_str = str(grad_type.enum)
            
            # 解析渐变色标
            grad_data = desc.get(b'Grad')
            color_stops = []
            opacity_stops = []
            
            if grad_data is not None:
                # 色标（Clrs）
                clrs = grad_data.get(b'Clrs', [])
                for cs in clrs:
                    clr = cs.get(b'Clr ')
                    r = float(clr.get(b'Rd  ', 0)) / 255.0 if clr else 0.0
                    g = float(clr.get(b'Grn ', 0)) / 255.0 if clr else 0.0
                    b = float(clr.get(b'Bl  ', 0)) / 255.0 if clr else 0.0
                    location = float(cs.get(b'Lctn', 0)) / 4096.0  # PS 用 4096 为 100%
                    color_stops.append((location, (r, g, b)))
                
                # 透明度标（Trns）
                trns = grad_data.get(b'Trns', [])
                for ts in trns:
                    opct = float(ts.get(b'Opct', 100.0)) / 100.0
                    location = float(ts.get(b'Lctn', 0)) / 4096.0
                    opacity_stops.append((location, opct))
            
            # 默认：如果没有色标，用黑→白
            if not color_stops:
                color_stops = [(0.0, (0.0, 0.0, 0.0)), (1.0, (1.0, 1.0, 1.0))]
            if not opacity_stops:
                opacity_stops = [(0.0, 1.0), (1.0, 1.0)]
            
            # 按 location 排序
            color_stops.sort(key=lambda x: x[0])
            opacity_stops.sort(key=lambda x: x[0])
            
            # 翻转渐变
            if reversed_grad:
                color_stops = [(1.0 - loc, c) for loc, c in reversed(color_stops)]
                opacity_stops = [(1.0 - loc, o) for loc, o in reversed(opacity_stops)]
            
            # 生成 t 值矩阵（0~1）
            angle_rad = math.radians(angle_deg)
            
            if 'Lnr' in grad_type_str or not grad_type_str:
                # 线性渐变：沿角度方向
                # PS 角度：90° = 从下到上，0° = 从左到右
                # 方向向量：dx = cos(angle), dy = -sin(angle)（屏幕 y 轴向下）
                cos_a = math.cos(angle_rad)
                sin_a = math.sin(angle_rad)
                
                # 创建坐标网格
                yy, xx = np.mgrid[0:h, 0:w]
                # 中心点（考虑偏移）
                cy = h / 2.0 + offset_y_pct * h
                cx = w / 2.0 + offset_x_pct * w
                
                if align_with_layer:
                    # 与图层对齐：在物理像素空间中投影，
                    # 渐变的总跨度(SF)等于图层高度 × scale。
                    # 这模拟了 Photoshop 的行为：
                    # - 对正方形图层：渐变铺满整个图层
                    # - 对窄长图层(如18×1864)+近水平渐变：渐变范围远大于宽度，
                    #   使得窄方向上颜色变化极小（几乎均匀）
                    # - 对宽扁图层(如1972×68)+垂直渐变：渐变范围=高度，
                    #   使得整个高度上能看到完整的渐变色带
                    proj = (xx - cx) * cos_a + (yy - cy) * (-sin_a)
                    max_proj = (h / 2.0) * grad_scale
                else:
                    # 不与图层对齐：在物理像素空间中投影
                    proj = (xx - cx) * cos_a + (yy - cy) * (-sin_a)
                    # 使用对角线长度作为渐变的全范围
                    half_diag = math.sqrt(w * w + h * h) / 2.0
                    max_proj = half_diag * grad_scale
                
                if max_proj > 0:
                    t = (proj / max_proj + 1.0) / 2.0
                else:
                    t = np.full((h, w), 0.5, dtype=np.float32)
                t = np.clip(t, 0.0, 1.0).astype(np.float32)
            
            elif 'Rdl' in grad_type_str:
                # 径向渐变：从中心向外
                yy, xx = np.mgrid[0:h, 0:w]
                cy = h / 2.0 + offset_y_pct * h
                cx = w / 2.0 + offset_x_pct * w
                dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
                
                if align_with_layer:
                    # 与图层对齐：使用图层半对角线 * scale
                    max_dist = (math.sqrt(w * w + h * h) / 2.0) * grad_scale
                else:
                    max_dist = math.sqrt(cx ** 2 + cy ** 2) * grad_scale
                
                t = (dist / max_dist).astype(np.float32) if max_dist > 0 else np.zeros((h, w), dtype=np.float32)
                t = np.clip(t, 0.0, 1.0)
            
            else:
                # 其他类型降级为线性
                yy, xx = np.mgrid[0:h, 0:w]
                t = (yy / max(h - 1, 1)).astype(np.float32)
            
            # 根据色标插值生成渐变 RGB 图 (H, W, 3)
            gradient_rgb = np.zeros((h, w, 3), dtype=np.float32)
            for ch in range(3):
                locs = np.array([s[0] for s in color_stops], dtype=np.float32)
                vals = np.array([s[1][ch] for s in color_stops], dtype=np.float32)
                gradient_rgb[:, :, ch] = np.interp(t, locs, vals)
            
            # 根据透明度标插值生成渐变 alpha (H, W)
            grad_opacity = np.ones((h, w), dtype=np.float32)
            if opacity_stops:
                locs = np.array([s[0] for s in opacity_stops], dtype=np.float32)
                vals = np.array([s[1] for s in opacity_stops], dtype=np.float32)
                grad_opacity = np.interp(t, locs, vals).astype(np.float32)
            
            # 最终混合：在有像素区域 blend
            has_pixel = (ext_alpha[:, :, 0] > 0).astype(np.float32)
            has_pixel_3d = has_pixel[:, :, np.newaxis]
            
            effective_opacity = opacity * grad_opacity[:, :, np.newaxis]
            
            blended = ext_rgb * (1.0 - effective_opacity) + gradient_rgb * effective_opacity
            ext_rgb[:, :, :] = ext_rgb * (1.0 - has_pixel_3d) + blended * has_pixel_3d
            
            print(f"      🌈 渐变叠加已渲染 (angle={angle_deg}°, {len(color_stops)} 色标)")
            
        except Exception as e:
            print(f"      ⚠️  渐变叠加渲染失败: {e}")
