#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图层渲染器模块
负责将 PSD 图层渲染为图像，包括组的手动渲染、子图层合成等
"""

from typing import Any, Tuple, Optional
import numpy as np
from PIL import Image

from common.image_utils import ImageArrayUtils, ImageBlendUtils, BBoxUtils
from core.render.effects.effects_renderer import render_layer_with_effects, is_effect_active


class GroupRenderer:
    """组图层渲染器"""
    
    def __init__(self, canvas_width: int, canvas_height: int):
        """
        初始化渲染器
        
        Args:
            canvas_width: 画布宽度
            canvas_height: 画布高度
        """
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
    
    def render_group_expanded(
        self,
        group_layer: Any,
        grp_bbox: Tuple[int, int, int, int],
        expand: int,
        depth: int = 0
    ) -> Image.Image:
        """
        在扩展画布上手动渲染组的所有子图层
        
        用于处理效果溢出场景（外描边、投影等超出组 bbox 的情况）
        
        Args:
            group_layer: 组图层对象
            grp_bbox: 组的原始 bbox
            expand: 扩展像素数
            depth: 递归深度（用于打印调试信息）
        
        Returns:
            渲染后的 PIL Image (RGBA)
        """
        grp_w, grp_h = BBoxUtils.get_dimensions(grp_bbox)
        
        # 创建扩展画布
        ext_h = grp_h + expand * 2
        ext_w = grp_w + expand * 2
        canvas = ImageArrayUtils.create_blank_canvas(ext_h, ext_w, channels=4)
        
        # 嵌套函数：渲染子组
        def render_subgroup(sub_grp: Any, depth_offset: int = 0):
            """渲染子组（优先使用 composite，失败则递归）"""
            nonlocal canvas
            
            if not sub_grp.visible or sub_grp.opacity == 0:
                return
            
            sub_bbox = sub_grp.bbox
            
            # 优先使用 composite() 渲染子组（正确处理组级效果）
            try:
                sub_img = sub_grp.composite(viewport=sub_bbox)
                if sub_img and sub_img.mode == 'RGBA':
                    sub_arr = ImageArrayUtils.pil_to_float_array(sub_img)
                    
                    # 应用子组不透明度
                    sub_arr = ImageBlendUtils.multiply_alpha(
                        sub_arr,
                        sub_grp.opacity / 255.0
                    )
                    
                    # 计算子组在扩展画布上的位置
                    sub_x = sub_bbox[0] - grp_bbox[0] + expand
                    sub_y = sub_bbox[1] - grp_bbox[1] + expand
                    
                    ImageBlendUtils.paste_on_canvas(
                        canvas, sub_arr, sub_x, sub_y, blend=True
                    )
                    return
            except Exception as e:
                print(f"{'  ' * (depth + depth_offset)}  ⚠️  子组 composite 失败: {e}")
            
            # 降级：递归渲染子图层
            for child in sub_grp:
                render_layer(child, depth_offset + 1)
        
        # 嵌套函数：渲染普通图层
        def render_layer(layer: Any, depth_offset: int = 0):
            """递归渲染图层"""
            nonlocal canvas
            
            if not layer.visible or layer.opacity == 0:
                return
            
            if layer.is_group():
                render_subgroup(layer, depth_offset)
                return
            
            # 普通图层：渲染效果
            result = render_layer_with_effects(layer)
            if result is None:
                return
            
            img, eff_bbox = result
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            
            # 计算图层在扩展画布上的位置
            layer_x = eff_bbox[0] - grp_bbox[0] + expand
            layer_y = eff_bbox[1] - grp_bbox[1] + expand
            
            layer_arr = ImageArrayUtils.pil_to_float_array(img)
            
            # 应用图层不透明度
            layer_arr = ImageBlendUtils.multiply_alpha(
                layer_arr,
                layer.opacity / 255.0
            )
            
            # 合成到 canvas
            ImageBlendUtils.paste_on_canvas(
                canvas, layer_arr, layer_x, layer_y, blend=True
            )
        
        # 渲染所有子图层
        for child in group_layer:
            render_layer(child)
        
        # 转回 PIL Image
        return ImageArrayUtils.float_array_to_pil(canvas)
    
    def calc_group_expand(self, group_layer: Any) -> int:
        """
        计算组需要的扩展像素数（递归检查所有子图层的效果）
        
        Args:
            group_layer: 组图层对象
        
        Returns:
            需要扩展的像素数
        """
        max_expand = 0
        
        for child in group_layer:
            if not child.visible:
                continue
            
            if child.is_group():
                # 递归检查子组
                child_expand = self.calc_group_expand(child)
                max_expand = max(max_expand, child_expand)
            else:
                # 检查图层效果
                if hasattr(child, 'effects') and child.effects:
                    for effect in child.effects:
                        if not is_effect_active(effect, child):
                            continue
                        
                        effect_name = str(effect)
                        if effect_name == 'DropShadow':
                            # 投影：实际衰减半径 ≈ blur * (1+spread/100) * 1.5
                            import math
                            desc = effect.descriptor
                            distance = float(desc.get(b'Dstn', 0)) if hasattr(desc, 'get') else 0.0
                            size = float(desc.get(b'blur', 0)) if hasattr(desc, 'get') else 0.0
                            spread = float(desc.get(b'Ckmt', 0)) if hasattr(desc, 'get') else 0.0
                            radius = size * (1.0 + spread / 100.0) * 1.5
                            max_expand = max(max_expand, int(math.ceil(distance + radius)) + 2)
                        
                        elif effect_name == 'OuterGlow':
                            # 外发光：高斯软衰减，可见半径 ≈ blur * (1+spread/100) * 1.5
                            import math
                            desc = effect.descriptor
                            size = float(desc.get(b'blur', 0)) if hasattr(desc, 'get') else 0.0
                            spread = float(desc.get(b'Ckmt', 0)) if hasattr(desc, 'get') else 0.0
                            radius = size * (1.0 + spread / 100.0) * 1.5
                            max_expand = max(max_expand, int(math.ceil(radius)) + 2)
                        
                        elif effect_name == 'Stroke':
                            # 外描边 / 居中描边
                            desc = effect.descriptor
                            style = desc.get(b'Styl')
                            style_val = style.enum if hasattr(style, 'enum') else b''
                            
                            if style_val in (b'OutF', b'CtrF'):
                                size_obj = desc.get(b'Sz  ')
                                size = int(size_obj) if size_obj else 0
                                
                                if style_val == b'OutF':
                                    max_expand = max(max_expand, size)
                                else:  # 居中描边
                                    max_expand = max(max_expand, size // 2 + 1)
        
        return max_expand


class LayerCompositor:
    """图层合成器 - 处理图层合成、蒙版等"""
    
    @staticmethod
    def apply_layer_mask(
        layer: Any,
        img: Image.Image,
        img_bbox: Tuple[int, int, int, int]
    ) -> Image.Image:
        """
        应用图层蒙版到图像的 alpha 通道
        
        Args:
            layer: 图层对象
            img: 图层图像
            img_bbox: 图像的 bbox
        
        Returns:
            应用蒙版后的图像
        """
        if not hasattr(layer, 'mask') or layer.mask is None:
            return img
        
        try:
            mask = layer.mask
            mask_img = mask.topil()
            if mask_img is None:
                return img
            
            mask_arr = ImageArrayUtils.pil_l_to_float_array(mask_img)
            mask_bbox = mask.bbox
            
            img_arr = ImageArrayUtils.pil_to_float_array(img)
            h, w = img_arr.shape[:2]
            
            # 计算 mask 与 img 的重叠区域
            intersect = BBoxUtils.intersect(mask_bbox, img_bbox)
            if intersect is None:
                # 无重叠：全透明
                img_arr[:, :, 3] = 0
            else:
                # 有重叠：应用 mask
                inter_l, inter_t, inter_r, inter_b = intersect
                
                # mask 中的位置
                mask_x1 = inter_l - mask_bbox[0]
                mask_y1 = inter_t - mask_bbox[1]
                mask_x2 = inter_r - mask_bbox[0]
                mask_y2 = inter_b - mask_bbox[1]
                
                # img 中的位置
                img_x1 = inter_l - img_bbox[0]
                img_y1 = inter_t - img_bbox[1]
                img_x2 = inter_r - img_bbox[0]
                img_y2 = inter_b - img_bbox[1]
                
                # 裁剪到有效范围
                img_x1 = max(0, min(img_x1, w))
                img_y1 = max(0, min(img_y1, h))
                img_x2 = max(img_x1, min(img_x2, w))
                img_y2 = max(img_y1, min(img_y2, h))
                
                mask_h, mask_w = mask_arr.shape
                mask_x1 = max(0, min(mask_x1, mask_w))
                mask_y1 = max(0, min(mask_y1, mask_h))
                mask_x2 = max(mask_x1, min(mask_x2, mask_w))
                mask_y2 = max(mask_y1, min(mask_y2, mask_h))
                
                # 应用 mask（重叠区域外全透明）
                full_mask = np.zeros((h, w), dtype=np.float32)
                copy_h = min(img_y2 - img_y1, mask_y2 - mask_y1)
                copy_w = min(img_x2 - img_x1, mask_x2 - mask_x1)
                
                if copy_h > 0 and copy_w > 0:
                    full_mask[img_y1:img_y1 + copy_h, img_x1:img_x1 + copy_w] = \
                        mask_arr[mask_y1:mask_y1 + copy_h, mask_x1:mask_x1 + copy_w]
                
                img_arr[:, :, 3] *= full_mask
            
            return ImageArrayUtils.float_array_to_pil(img_arr)
        
        except Exception as e:
            print(f"  ⚠️  应用蒙版失败: {e}")
            return img
    
    @staticmethod
    def apply_clipping_mask(
        base_img: Image.Image,
        base_bbox: Tuple[int, int, int, int],
        clip_layers: list
    ) -> Image.Image:
        """
        应用剪切蒙版（将上层图层剪切到基础层的形状）
        
        Args:
            base_img: 基础层图像
            base_bbox: 基础层 bbox
            clip_layers: 剪切层列表
        
        Returns:
            合成后的图像
        """
        base_arr = ImageArrayUtils.pil_to_float_array(base_img)
        base_alpha = base_arr[:, :, 3:4]
        
        # 创建输出画布
        result = base_arr.copy()
        
        for clip_layer in clip_layers:
            if not clip_layer.visible or clip_layer.opacity == 0:
                continue
            
            clip_result = render_layer_with_effects(clip_layer)
            if clip_result is None:
                continue
            
            clip_img, clip_bbox = clip_result
            clip_arr = ImageArrayUtils.pil_to_float_array(clip_img)
            
            # 计算重叠区域
            inter_l = max(base_bbox[0], clip_bbox[0])
            inter_t = max(base_bbox[1], clip_bbox[1])
            inter_r = min(base_bbox[2], clip_bbox[2])
            inter_b = min(base_bbox[3], clip_bbox[3])
            
            if inter_r <= inter_l or inter_b <= inter_t:
                continue
            
            # 计算在各自图像中的位置
            base_x1 = inter_l - base_bbox[0]
            base_y1 = inter_t - base_bbox[1]
            base_x2 = inter_r - base_bbox[0]
            base_y2 = inter_b - base_bbox[1]
            
            clip_x1 = inter_l - clip_bbox[0]
            clip_y1 = inter_t - clip_bbox[1]
            clip_x2 = inter_r - clip_bbox[0]
            clip_y2 = inter_b - clip_bbox[1]
            
            # 提取重叠区域
            base_region = base_alpha[base_y1:base_y2, base_x1:base_x2, :]
            clip_region = clip_arr[clip_y1:clip_y2, clip_x1:clip_x2, :]
            
            # 剪切：clip 的 alpha 与 base 的 alpha 相乘
            clipped_alpha = clip_region[:, :, 3:4] * base_region
            clipped_region = ImageArrayUtils.combine_rgba(
                clip_region[:, :, :3], clipped_alpha
            )
            
            # 应用不透明度
            clipped_region = ImageBlendUtils.multiply_alpha(
                clipped_region,
                clip_layer.opacity / 255.0
            )
            
            # 合成到结果
            result[base_y1:base_y2, base_x1:base_x2] = ImageBlendUtils.alpha_composite(
                result[base_y1:base_y2, base_x1:base_x2],
                clipped_region
            )
        
        return ImageArrayUtils.float_array_to_pil(result)
