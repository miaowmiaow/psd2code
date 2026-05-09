#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图层分类器模块
负责判断图层类型、是否可合并、是否需要特殊处理等
"""

from typing import Any, Optional
import numpy as np


class LayerClassifier:
    """图层分类器 - 判断图层属性和类型"""
    
    def __init__(self, canvas_width: int, canvas_height: int):
        """
        初始化分类器
        
        Args:
            canvas_width: 画布宽度
            canvas_height: 画布高度
        """
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
    
    def is_text_layer(self, layer: Any) -> bool:
        """判断是否为文本图层"""
        return layer.kind == 'type'
    
    def is_group_layer(self, layer: Any) -> bool:
        """判断是否为组图层"""
        return layer.is_group()
    
    def is_pixel_layer(self, layer: Any) -> bool:
        """判断是否为像素图层（非文本、非组）"""
        return not self.is_text_layer(layer) and not self.is_group_layer(layer)
    
    def has_expanding_effects(self, layer: Any) -> bool:
        """
        判断图层是否有需要扩展画布的效果（外描边、外发光、投影）
        
        Args:
            layer: 图层对象
        
        Returns:
            True 表示有扩展效果
        """
        if not hasattr(layer, 'effects') or not layer.effects:
            return False
        
        for effect in layer.effects:
            if not effect.enabled:
                continue
            
            effect_name = str(effect)
            
            # 投影、外发光
            if effect_name in ('DropShadow', 'OuterGlow'):
                return True
            
            # 外描边或居中描边
            if effect_name == 'Stroke':
                desc = effect.descriptor
                style = desc.get(b'Styl')
                style_val = style.enum if hasattr(style, 'enum') else b''
                if style_val in (b'OutF', b'CtrF'):  # 外描边 / 居中描边
                    return True
        
        return False
    
    def is_button_group(self, group_layer: Any) -> bool:
        """
        判断是否为按钮组（文本 + 图片背景）
        
        按钮组特征：
        1. 组名包含"按钮"或"button"
        2. 包含1个文本图层 + N个图片图层
        
        Args:
            group_layer: 组图层对象
        
        Returns:
            True 表示是按钮组
        """
        if not group_layer.is_group():
            return False
        
        name_lower = group_layer.name.lower()
        if '按钮' not in name_lower and 'button' not in name_lower:
            return False
        
        text_count = 0
        image_count = 0
        
        for child in group_layer:
            if not child.visible:
                continue
            
            if child.kind == 'type':
                text_count += 1
            elif not child.is_group():
                image_count += 1
        
        return text_count == 1 and image_count >= 1
    
    def is_pure_image_group(self, group_layer: Any, recursive: bool = True) -> bool:
        """
        判断组是否为纯图片组（组内全是图片图层，无文本）
        
        Args:
            group_layer: 组图层对象
            recursive: 是否递归检查子组
        
        Returns:
            True 表示是纯图片组
        """
        if not group_layer.is_group():
            return False
        
        has_visible_layer = False
        
        for child in group_layer:
            if not child.visible:
                continue
            
            has_visible_layer = True
            
            # 有文本图层 → 不是纯图片组
            if child.kind == 'type':
                return False
            
            # 子组：递归检查
            if child.is_group():
                if recursive and not self.is_pure_image_group(child, recursive=True):
                    return False
        
        return has_visible_layer
    
    def can_merge_group(self, group_layer: Any) -> bool:
        """
        判断组是否可以合并为单张图片
        
        条件（全部满足）：
        1. 组内全是图片图层（子组也递归检查）或者是按钮组
        2. 所有图层都不包含需要扩展画布的效果
        3. 组的 bbox 不能严重超出画布（避免生成超大图片）
        4. 至少有一个可见图层
        
        Args:
            group_layer: 组图层对象
        
        Returns:
            True 表示可以合并
        """
        if not group_layer.is_group():
            return False
        
        # 条件3：检查组 bbox 是否严重超出画布
        grp_bbox = group_layer.bbox
        grp_w = grp_bbox[2] - grp_bbox[0]
        grp_h = grp_bbox[3] - grp_bbox[1]
        
        if grp_w > self.canvas_width * 2 or grp_h > self.canvas_height * 2:
            return False
        
        # 条件1：纯图片组 或 按钮组
        is_pure_image = self.is_pure_image_group(group_layer, recursive=True)
        is_button = self.is_button_group(group_layer)
        
        if not (is_pure_image or is_button):
            return False
        
        # 条件2 & 4：检查所有子图层
        has_visible = False
        
        def check_children(layer: Any) -> bool:
            """递归检查子图层"""
            nonlocal has_visible
            
            for child in layer:
                if not child.visible:
                    continue
                
                has_visible = True
                
                # 有扩展效果 → 不能合并
                if self.has_expanding_effects(child):
                    return False
                
                # 递归检查子组
                if child.is_group():
                    if not check_children(child):
                        return False
            
            return True
        
        if not check_children(group_layer):
            return False
        
        return has_visible
    
    def should_skip_layer(self, layer: Any) -> bool:
        """
        判断图层是否应该跳过（不导出）
        
        跳过条件：
        - 不可见
        - 完全透明（opacity = 0）
        - 空图层（无内容）
        
        Args:
            layer: 图层对象
        
        Returns:
            True 表示应该跳过
        """
        if not layer.visible:
            return True
        
        if hasattr(layer, 'opacity') and layer.opacity == 0:
            return True
        
        # 检查是否为空图层
        if not layer.is_group() and layer.kind != 'type':
            try:
                # 优先使用 composite() 而不是 topil()，因为 shape 图层的 topil() 可能返回 None
                # 但 composite() 可以正常渲染（如进度条、矩形等形状图层）
                img = layer.composite() if hasattr(layer, 'composite') else layer.topil()
                if img is None:
                    return True
                
                # 检查图像是否完全透明
                arr = np.array(img)
                if len(arr.shape) >= 3 and arr.shape[2] == 4:  # 有 alpha 通道
                    alpha = arr[:,:,3]
                    if alpha.max() == 0:  # 完全透明
                        return True
            except Exception:
                return True
        
        return False
    
    def get_blend_mode_css(self, layer: Any) -> Optional[str]:
        """
        获取图层混合模式对应的 CSS mix-blend-mode 值
        
        Args:
            layer: 图层对象
        
        Returns:
            CSS blend mode 字符串，如果是 normal 则返回 None
        """
        if not hasattr(layer, 'blend_mode'):
            return None
        
        # Photoshop 混合模式 → CSS mix-blend-mode
        BLEND_MODES = {
            'BlendMode.NORMAL': 'normal',
            'BlendMode.PASS_THROUGH': 'normal',
            'BlendMode.DISSOLVE': 'normal',
            'BlendMode.MULTIPLY': 'multiply',
            'BlendMode.SCREEN': 'screen',
            'BlendMode.OVERLAY': 'overlay',
            'BlendMode.SOFT_LIGHT': 'soft-light',
            'BlendMode.HARD_LIGHT': 'hard-light',
            'BlendMode.COLOR_DODGE': 'color-dodge',
            'BlendMode.COLOR_BURN': 'color-burn',
            'BlendMode.LINEAR_BURN': 'color-burn',
            'BlendMode.DARKEN': 'darken',
            'BlendMode.DARKER_COLOR': 'darken',
            'BlendMode.LIGHTEN': 'lighten',
            'BlendMode.LIGHTER_COLOR': 'lighten',
            'BlendMode.LINEAR_DODGE': 'lighten',
            'BlendMode.DIFFERENCE': 'difference',
            'BlendMode.EXCLUSION': 'exclusion',
            'BlendMode.VIVID_LIGHT': 'hard-light',
            'BlendMode.LINEAR_LIGHT': 'hard-light',
            'BlendMode.PIN_LIGHT': 'hard-light',
            'BlendMode.HARD_MIX': 'hard-light',
            'BlendMode.SUBTRACT': 'difference',
            'BlendMode.DIVIDE': 'normal',
            'BlendMode.HUE': 'hue',
            'BlendMode.SATURATION': 'saturation',
            'BlendMode.COLOR': 'color',
            'BlendMode.LUMINOSITY': 'luminosity',
        }
        
        blend_mode_str = str(layer.blend_mode)
        css_mode = BLEND_MODES.get(blend_mode_str, 'normal')
        
        return css_mode if css_mode != 'normal' else None
