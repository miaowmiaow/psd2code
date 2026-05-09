#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
描边效果渲染器
支持：外描边 (OutF)、内描边 (InsF)、居中描边 (CtrF)
"""

from typing import Any
import numpy as np


class StrokeRenderer:
    """描边效果渲染器"""
    
    @staticmethod
    def can_render(effect: Any) -> bool:
        """判断是否为描边效果"""
        return str(effect) == 'Stroke'
    
    @staticmethod
    def render(
        effect: Any, 
        layer: Any, 
        alpha: np.ndarray, 
        h: int, 
        w: int
    ) -> np.ndarray | None:
        """
        渲染描边效果
        
        Args:
            effect: PSD 描边效果对象
            layer: 图层对象（用于获取 psd-tools 描边渲染器）
            alpha: 基础图层的 alpha 通道，形状 (H, W, 1)
            h: 画布高度
            w: 画布宽度
        
        Returns:
            渲染后的描边图层数组 (H, W, 4)，如果失败则返回 None
        """
        try:
            from psd_tools.composite.effects import draw_stroke_effect
            
            # 关键 bug 修复（2026-04-29）：
            # psd-tools 的 draw_stroke_effect 用 skimage.filters.scharr 检测形状
            # 边缘，要求输入的 alpha 数组中"形状边缘"前后都有像素（才能算梯度）。
            # 当 alpha 整片紧贴画布边缘（无 padding），scharr 检测不到任何边缘，
            # 导致返回的 mask 整片为 1，描边色被铺满整个 shape（典型 bug：
            # 领奖.psd "矩形 13" SoCo=#fff36c + InsF stroke=#5f8618，渲染出来
            # 整张图变 #5f8618）。
            #
            # 修复策略：在 alpha 周围临时 pad 几个像素的 0（透明），让 scharr 能
            # 检测到完整轮廓；渲染完描边后裁回原尺寸。
            desc = effect.descriptor
            size_obj = desc.get(b'Sz  ')
            stroke_size = int(round(float(size_obj))) if size_obj else 1
            # padding 至少为 stroke_size + 2（保证膨胀核 disk(size) 能完整工作）
            pad = max(stroke_size + 2, 4)

            padded_alpha = np.pad(
                alpha, ((pad, pad), (pad, pad), (0, 0)),
                mode='constant', constant_values=0.0,
            )
            padded_h = h + pad * 2
            padded_w = w + pad * 2
            bbox = (0, 0, padded_w, padded_h)

            stroke_color, stroke_mask = draw_stroke_effect(
                bbox, padded_alpha, effect.descriptor, layer._psd
            )
            
            # 裁回原画布尺寸
            stroke_color_cropped = stroke_color[pad:pad + h, pad:pad + w, :]
            stroke_mask_cropped = stroke_mask[pad:pad + h, pad:pad + w, :]

            opacity = effect.opacity / 100.0
            out = np.zeros((h, w, 4), dtype=np.float32)
            out[:, :, :3] = stroke_color_cropped
            out[:, :, 3:4] = stroke_mask_cropped * opacity
            return out
            
        except Exception as e:
            print(f"      ⚠️  描边渲染失败: {e}")
            return None
    
    @staticmethod
    def calc_expand(effect: Any) -> int:
        """
        计算描边需要扩展的像素数
        
        Args:
            effect: PSD 描边效果对象
        
        Returns:
            扩展像素数
        """
        desc = effect.descriptor
        size = int(float(desc.get(b'Sz  ', 0)))
        style = desc.get(b'Styl')
        style_str = ''
        if hasattr(style, 'enum'):
            style_str = str(style.enum)
        
        # OutF=外描边  CtrF=居中描边  InsF=内描边
        if b'OutF' in str(style_str).encode() or 'OutF' in style_str or 'Outset' in style_str:
            return size + 2
        elif b'CtrF' in str(style_str).encode() or 'CtrF' in style_str or 'Center' in style_str:
            return size // 2 + 2
        else:
            # 内描边不需要扩展
            return 0
    
    @staticmethod
    def get_stroke_style(effect: Any) -> str:
        """
        获取描边样式
        
        Returns:
            'InsF' (内描边) | 'OutF' (外描边) | 'CtrF' (居中描边)
        """
        desc = effect.descriptor
        style = desc.get(b'Styl')
        style_val = style.enum if hasattr(style, 'enum') else b''
        
        if style_val == b'InsF':
            return 'InsF'
        elif style_val == b'CtrF':
            return 'CtrF'
        else:
            return 'OutF'
