#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图层导出模块
- 保留完整 PSD 图层层级结构（组→嵌套 children）
- 使用 layer.left / layer.top 获取画布绝对坐标
- 文本图层保留为 text 类型，有旋转/倾斜的文本降级为图片
- 图片图层自动渲染描边/阴影/发光等效果
- 图片命名 = 原图层名（拼音）+ 唯一数字
"""

from typing import Any
from pathlib import Path
from PIL import Image
import hashlib
import io
import numpy as np

from config import Config
from common.utils import make_image_filename
from common.image_utils import ImageArrayUtils
from core.psd.text_extractor import TextExtractor
from core.render.effects.effects_renderer import (
    render_layer_with_effects,
    render_layer_with_effects_on_image,
    is_effect_active,
)
from .image_ops import (
    _constrain_bbox_to_canvas,
    _apply_layer_mask,
    _alpha_composite_numpy,
)
from .handlers import HandlerContext, run_handlers


# Photoshop 混合模式 → CSS mix-blend-mode
# psd-tools 的 BlendMode 枚举 str() 返回 "BlendMode.XXX" 格式
BLEND_MODES: dict[str, str] = {
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
    # PS 的 LINEAR_BURN 数学是 result = bg + fg - 1（clamp 到 [0,1]），
    # CSS 没有等价模式；视觉最接近的是 multiply（result = bg*fg），
    # 两者都是"乘法压暗"，能正确产生深色压底剪影效果。
    # 不要映射到 color-burn —— color-burn 在浅色底上会被烧成接近底色，剪影会消失。
    'BlendMode.LINEAR_BURN': 'multiply',
    'BlendMode.DARKEN': 'darken',
    'BlendMode.DARKER_COLOR': 'darken',
    'BlendMode.LIGHTEN': 'lighten',
    'BlendMode.LIGHTER_COLOR': 'lighten',
    # PS 的 LINEAR_DODGE (Add) 数学是 result = bg + fg（clamp）≈ CSS plus-lighter；
    # 退而求其次用 screen（result = 1-(1-bg)*(1-fg)）—— 浏览器普适、视觉接近变亮。
    'BlendMode.LINEAR_DODGE': 'screen',
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


class LayerExporter:
    """图层导出器 - 保留完整层级结构"""

    def __init__(self, psd: Any, output_dir: Path, smart_merge: bool = True):
        self.psd = psd
        self.output_dir = output_dir
        self.images_dir = output_dir / 'images'
        self.images_dir.mkdir(exist_ok=True)

        # 画布尺寸（用于 bbox 约束）
        self.canvas_width = psd.width
        self.canvas_height = psd.height

        self.exported_count: int = 0
        self.skipped_count: int = 0
        self._z_counter: int = 0

        # 图片去重：md5 → 已保存的 image_path (如 'images/xxx.png')
        self._image_hash_map: dict[str, str] = {}
        self._dedup_count: int = 0

        # smart_merge=False 关闭「智能合图」：
        #   - _can_merge_group / _can_merge_group_non_text 直接返回 False
        #     → 装饰组不被合成为单张 PNG，保持组结构逐层导出
        #   - _detect_background_layers 返回 []
        #     → 画布底部连续背景图层不被合一张背景图
        # 此开关仅影响 PSD → 中间产物阶段的图片合成，CSS/HTML
        # 布局优化阶段的两项合图（DOMRestructure 内联背景合成、
        # ImageLayerFlatten）由上层管线各自控制（参考
        # targets/html/pipeline.LayoutOptimizeStage 与
        # core/converter.PSDToHTMLConverter）。
        self.smart_merge = smart_merge

    def _save_image_dedup(self, img: Image.Image, name: str, depth: int) -> str:
        """
        保存图片并去重。如果相同内容的图片已保存过，直接复用路径。

        Returns:
            相对路径如 'images/xxx.png'
        """
        # 计算图片内容的 MD5
        buf = io.BytesIO()
        img.save(buf, format=Config.IMAGE_FORMAT.upper())
        img_bytes = buf.getvalue()
        md5 = hashlib.md5(img_bytes).hexdigest()

        if md5 in self._image_hash_map:
            existing_path = self._image_hash_map[md5]
            self._dedup_count += 1
            print(f"{'  ' * depth}♻️  {name} (复用 {existing_path})")
            return existing_path

        # 新图片，保存到磁盘
        # 把完整 md5 传给 make_image_filename，它会取前 6 位作稳定指纹；
        # 同时把 layer ltype 告诉它，作为语义兜底（shape vs image）。
        img_filename = make_image_filename(
            name,
            Config.MAX_FILENAME_LENGTH,
            Config.IMAGE_FORMAT,
            content_hash=md5,
            ltype="image",
        )
        img_path = self.images_dir / img_filename
        img_path.write_bytes(img_bytes)
        rel_path = f'images/{img_filename}'
        self._image_hash_map[md5] = rel_path
        return rel_path

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def _detect_background_layers(self, layers_list: list[Any]) -> list[Any]:
        """
        检测从图层栈底部开始的连续背景图层。

        背景图层需满足以下所有条件：
        1. 从图层栈底部开始连续排列（遇到不满足的即停止）
        2. 非组、非文本图层
        3. 宽度完全覆盖画布 (bbox.left ≤ 0 且 bbox.right ≥ canvas_width)
        4. opacity = 255 且 blend_mode = NORMAL
        5. 不是剪切蒙版
        6. 可见（visible=True）

        Returns:
            符合条件的背景图层列表（按从底到上顺序），
            少于 2 个时返回空列表（单层无需合并）
        """
        # 总开关：smart_merge=False 放弃画布底部连续背景合并
        if not self.smart_merge:
            return []

        bg_layers: list[Any] = []
        canvas_w = self.psd.width

        for layer in layers_list:
            # 跳过隐藏/完全透明图层（不中断扫描）
            if not layer.visible or layer.opacity == 0:
                continue

            # 遇到组 → 停止扫描
            if layer.is_group():
                break

            # 遇到文本 → 停止扫描
            kind = str(layer.kind) if hasattr(layer, 'kind') else ''
            if 'type' in kind.lower():
                break

            # 检查是否全宽覆盖画布
            bbox = layer.bbox
            if bbox[0] > 0 or bbox[2] < canvas_w:
                break

            # 检查不透明度
            if layer.opacity != 255:
                break

            # 检查混合模式
            bm = str(layer.blend_mode)
            if 'NORMAL' not in bm.upper():
                break

            # 检查是否为剪切蒙版
            if self._is_clipping(layer):
                break

            bg_layers.append(layer)

        # 至少 2 个才有合并意义
        return bg_layers if len(bg_layers) >= 2 else []

    def _merge_background_layers(
        self, bg_layers: list[Any], depth: int,
        parent_left: int, parent_top: int,
    ) -> dict[str, Any] | None:
        """
        将底部连续的背景图层合并为一张图片。

        使用手动 alpha 合成（从底到上 Porter-Duff over），
        结果裁切到画布范围 (0, 0, canvas_w, canvas_h)。
        """

        canvas_w, canvas_h = self.psd.width, self.psd.height
        names = [l.name for l in bg_layers]
        print(f"{'  ' * depth}🎨 合并背景图层: {names}")

        try:
            canvas = np.zeros((canvas_h, canvas_w, 4), dtype=np.float32)

            for layer in bg_layers:
                img = layer.topil()
                if img is None:
                    continue
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')

                # 应用图层蒙版
                img = _apply_layer_mask(layer, img, layer.bbox)

                arr = ImageArrayUtils.pil_to_float_array(img)
                bbox = layer.bbox

                # 计算图层与画布的交集区域
                src_x0 = max(0, -bbox[0])
                src_y0 = max(0, -bbox[1])
                dst_x0 = max(0, bbox[0])
                dst_y0 = max(0, bbox[1])
                cp_w = min(arr.shape[1] - src_x0, canvas_w - dst_x0)
                cp_h = min(arr.shape[0] - src_y0, canvas_h - dst_y0)

                if cp_w <= 0 or cp_h <= 0:
                    continue

                src_region = arr[src_y0:src_y0 + cp_h, src_x0:src_x0 + cp_w]
                dst_region = canvas[dst_y0:dst_y0 + cp_h, dst_x0:dst_x0 + cp_w]

                # Porter-Duff over 合成
                src_a = src_region[:, :, 3:4]
                dst_a = dst_region[:, :, 3:4]
                out_a = src_a + dst_a * (1 - src_a)
                safe_a = np.maximum(out_a, 1e-10)
                out_rgb = (src_region[:, :, :3] * src_a +
                           dst_region[:, :, :3] * dst_a * (1 - src_a)) / safe_a

                canvas[dst_y0:dst_y0 + cp_h, dst_x0:dst_x0 + cp_w, :3] = out_rgb
                canvas[dst_y0:dst_y0 + cp_h, dst_x0:dst_x0 + cp_w, 3:4] = out_a

            # 检查是否完全透明
            if canvas[:, :, 3].max() == 0:
                print(f"{'  ' * depth}🚫 背景合并后完全透明")
                return None

            merged_img = ImageArrayUtils.float_array_to_pil(canvas)

            self._z_counter += 1

            # 背景图层固定在画布左上角 (0, 0)
            rel_left = 0 - parent_left
            rel_top = 0 - parent_top

            layer_info: dict[str, Any] = {
                'id': f'layer-{self._z_counter}',
                'name': 'background',
                'full_name': 'background',
                'left': rel_left,
                'top': rel_top,
                'width': canvas_w,
                'height': canvas_h,
                'opacity': 1.0,
                'blend_mode': 'normal',
                'z_index': self._z_counter,
                'type': 'image',
            }

            # 保存图片（去重）
            rel_path = self._save_image_dedup(merged_img, 'background', depth)
            layer_info['image_path'] = rel_path

            total = len(bg_layers)
            print(f"{'  ' * depth}🖼️  background [合并{total}层 {canvas_w}x{canvas_h}] → {rel_path}")
            self.exported_count += total
            return layer_info

        except Exception as e:
            print(f"{'  ' * depth}❌ 合并背景图层失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    @staticmethod
    def _is_clipping(layer: Any) -> bool:
        """判断图层是否为剪切蒙版"""
        return (
            hasattr(layer, '_record')
            and hasattr(layer._record, 'clipping')
            and layer._record.clipping == 1
        )

    @staticmethod
    def _group_clipping_layers(layers_list: list[Any]) -> list[Any]:
        """
        把连续的 clipping 图层分组到它们的 base 图层上。

        返回一个列表，元素为：
        - 普通图层 / 组 → 原样保留
        - (base_layer, [clipped_layer, ...]) → 需要合并渲染的剪切蒙版组
        """
        grouped: list[Any] = []
        i = 0
        while i < len(layers_list):
            layer = layers_list[i]
            # 如果该图层不是 clipping，检查后面是否有 clipping 图层挂在它身上
            if not LayerExporter._is_clipping(layer):
                clipped: list[Any] = []
                j = i + 1
                while j < len(layers_list) and LayerExporter._is_clipping(layers_list[j]):
                    clipped.append(layers_list[j])
                    j += 1
                if clipped:
                    grouped.append((layer, clipped))
                else:
                    grouped.append(layer)
                i = j
            else:
                # 孤立的 clipping 图层（没有 base），作为普通图层处理
                grouped.append(layer)
                i += 1
        return grouped

    @staticmethod
    def _has_canvas_expanding_effects(layer: Any) -> bool:
        """
        判断图层是否包含需要扩展画布的效果（外描边、外发光、投影）。
        内描边、内发光、内阴影、ColorOverlay 不需要扩展画布，不计入。
        """
        if not hasattr(layer, 'effects') or not layer.effects:
            return False
        for effect in layer.effects:
            if not is_effect_active(effect, layer):
                continue
            name = str(effect)
            if name == 'DropShadow':
                return True
            elif name == 'OuterGlow':
                return True
            elif name == 'Stroke':
                desc = effect.descriptor
                style = desc.get(b'Styl')
                style_val = style.enum if hasattr(style, 'enum') else b''
                # InsF = 内描边，不需要扩展；OutF/CtrF = 外描边/居中描边，需要扩展
                if style_val != b'InsF':
                    return True
        return False

    def _is_button_group(self, group_layer: Any) -> bool:
        """
        判断组是否为按钮（按钮需要将文本和背景合并导出为单张图片）。
        
        按钮特征：
        1. 组内子图层数量较少（1-5个）
        2. 包含文本图层
        3. 包含背景图片或形状图层
        4. 尺寸较小（宽度 <= 600px，高度 <= 150px）
        5. **名称包含按钮关键词（强校验）**
        6. **文本不包含数字（排除动态内容）**
        """
        # 获取可见子图层
        visible_children = [
            child for child in group_layer
            if child.visible and child.opacity > 0
        ]
        
        if len(visible_children) == 0:
            return False
        
        # 条件1：子图层数量1-5个
        if len(visible_children) > 5:
            return False
        
        # 条件4：尺寸检查
        grp_bbox = group_layer.bbox
        grp_w = grp_bbox[2] - grp_bbox[0]
        grp_h = grp_bbox[3] - grp_bbox[1]
        
        # 支持横向长条按钮（如"未符合条件" 460x52px）
        # 宽度放宽至 600px，高度放宽至 150px
        if grp_w > 600 or grp_h > 150:
            return False
        
        # 条件2 & 3：检查是否同时包含文本和图片
        # 注意：按钮可能包含子组（如装饰元素），需要递归检查
        has_text = False
        has_image = False
        text_layers = []
        
        def check_layer_recursive(layer, depth=0):
            """递归检查图层，寻找文本和图片"""
            nonlocal has_text, has_image
            
            if layer.is_group():
                # 子组：递归检查其子图层
                for child in layer:
                    if child.visible and child.opacity > 0:
                        check_layer_recursive(child, depth + 1)
            else:
                # 普通图层
                kind = str(layer.kind) if hasattr(layer, 'kind') else ''
                if 'type' in kind.lower():
                    has_text = True
                    text_layers.append(layer)
                else:
                    has_image = True
        
        for child in visible_children:
            check_layer_recursive(child)
        
        if not (has_text and has_image):
            return False
        
        # 条件5（关键）：**名称必须明确包含按钮关键词**
        name_lower = group_layer.name.lower() if group_layer.name else ''
        
        # 强校验：按钮必须包含以下关键词之一
        # 注意：使用完整词而非单字，避免误匹配
        button_keywords = [
            '按钮', 'btn', 'button',
            '立即', '马上',  # 行动类词汇（精确）
            '抽奖', '领取奖', '领奖', '开始游戏',  # 完整动作短语
            '领取', '开始', '确认', '取消', '提交', 
            '登录', '注册', '购买', '支付', '充值',
            '详情', '查看详情', '返回', '关闭',
            '点击', '免费', '立即体验',
            '?', '？',  # 问号按钮（帮助/说明）
            'help', 'info', 'question',  # 英文帮助类关键词
        ]
        
        # 递归收集所有子图层名称（包括子组内的）
        def collect_all_names(layer):
            names = [layer.name.lower() if layer.name else '']
            if layer.is_group():
                for child in layer:
                    names.extend(collect_all_names(child))
            return names
        
        all_child_names = []
        for child in visible_children:
            all_child_names.extend(collect_all_names(child))
        
        child_names_str = ' '.join(all_child_names)
        combined_name = f"{name_lower} {child_names_str}"
        
        has_button_keyword = any(kw in combined_name for kw in button_keywords)
        
        # **如果名称不包含按钮关键词，直接排除**
        if not has_button_keyword:
            return False
        
        # 条件6：**文本不能包含数字（排除动态内容）**
        # 如"已解锁人数：100人"、"累抽集欧气 x3" 都包含数字，应排除
        for text_layer in text_layers:
            text_content = self._extract_text_content(text_layer)
            if text_content and any(char.isdigit() for char in text_content):
                # 文本包含数字，可能是动态内容，不作为按钮
                return False
        
        # 满足所有条件，确认为按钮
        return True
    
    def _extract_text_content(self, text_layer: Any) -> str:
        """
        提取文本图层的文本内容
        """
        try:
            if hasattr(text_layer, 'text'):
                return str(text_layer.text)
            elif hasattr(text_layer, 'engine_dict'):
                # 尝试从引擎字典提取
                engine = text_layer.engine_dict
                if 'Editor' in engine and 'Text' in engine['Editor']:
                    return str(engine['Editor']['Text'])
        except Exception:
            pass
        return ''

    def _calc_group_expand(self, group_layer: Any) -> int:
        """
        计算组内所有图层效果溢出所需的最大扩展像素数。
        
        遍历组内所有可见图层（包括子组），检查其效果（OuterGlow、Stroke等），
        返回所需的最大扩展像素数。
        """
        max_expand = 0
        
        def calc_layer_expand(layer) -> int:
            """计算单个图层的效果扩展像素数"""
            if not hasattr(layer, 'effects') or not layer.effects:
                return 0
            
            expand = 0
            for effect in layer.effects:
                if not is_effect_active(effect, layer):
                    continue
                desc = effect.descriptor
                name = str(effect)
                
                if name == 'Stroke':
                    size = int(float(desc.get(b'Sz  ', 0)))
                    style = desc.get(b'Styl')
                    style_str = ''
                    if hasattr(style, 'enum'):
                        style_str = str(style.enum)
                    # OutF=外描边  CtrF=居中描边
                    if b'OutF' in str(style_str).encode() or 'OutF' in style_str or 'Outset' in style_str:
                        expand = max(expand, size + 2)
                    elif b'CtrF' in str(style_str).encode() or 'CtrF' in style_str or 'Center' in style_str:
                        expand = max(expand, size // 2 + 2)
                
                elif name == 'OuterGlow':
                    # PS 外发光是高斯衰减软边，可见半径远大于 blur 本身：
                    #   实际可见半径 ≈ blur * (1 + spread/100) * 1.5
                    # 仅用 blur+常数 会导致外缘被裁（典型表现：发光边沿出现硬切）
                    import math
                    blur = float(desc.get(b'blur', 0))
                    spread = float(desc.get(b'Ckmt', 0))  # 扩展百分比（0-100）
                    radius = blur * (1.0 + spread / 100.0) * 1.5
                    expand = max(expand, int(math.ceil(radius)) + 2)
                
                elif name == 'DropShadow':
                    # 投影同理：distance 是位移，blur 是高斯模糊半径，
                    # 实际衰减半径 ≈ blur * (1 + spread/100) * 1.5
                    import math
                    blur = float(desc.get(b'blur', 0))
                    dist = float(desc.get(b'Dstn', 0))
                    spread = float(desc.get(b'Ckmt', 0))
                    radius = blur * (1.0 + spread / 100.0) * 1.5
                    expand = max(expand, int(math.ceil(dist + radius)) + 2)
            
            return expand
        
        def check_layer(layer):
            nonlocal max_expand
            if not layer.visible or layer.opacity == 0:
                return
            
            if layer.is_group():
                # 递归检查子组
                for child in layer:
                    check_layer(child)
            else:
                # 普通图层：检查效果
                expand = calc_layer_expand(layer)
                max_expand = max(max_expand, expand)
        
        for child in group_layer:
            check_layer(child)
        
        return max_expand
    
    def _should_use_manual_render(self, group_layer: Any) -> bool:
        """
        判断是否应该使用手动渲染而不是 composite()。
        
        需要手动渲染的情况：
        1. 组内包含特殊混合模式（SCREEN/LINEAR_DODGE等）
           因为 psd-tools 的 composite() 会在黑色背景上合成这些混合模式，
           导致黑色被"烘焙"到图层数据中，无论组本身是什么混合模式
        
        Returns:
            True: 需要手动渲染（逐层导出）; False: 可以使用 composite()
        """
        # 检查组内是否有特殊混合模式（不限制组本身的混合模式）
        special_blend_modes = {
            'BlendMode.SCREEN',
            'BlendMode.LINEAR_DODGE', 
            'BlendMode.COLOR_DODGE',
            'BlendMode.LIGHTEN',
            'BlendMode.LINEAR_LIGHT',
        }
        
        def has_special_blend(layer) -> bool:
            if not layer.visible or layer.opacity == 0:
                return False
            
            blend_mode_str = str(layer.blend_mode)
            if blend_mode_str in special_blend_modes:
                return True
            
            # 递归检查子组
            if layer.is_group():
                for child in layer:
                    if has_special_blend(child):
                        return True
            
            return False
        
        for child in group_layer:
            if has_special_blend(child):
                return True
        
        return False
    
    def _render_group_manually(
        self, group_layer: Any, grp_bbox: tuple[int, int, int, int], depth: int
    ) -> 'Image.Image | None':
        """
        纯手动渲染组（不扩展画布）。
        
        用于处理 PASS_THROUGH 组 + 特殊混合模式的情况，
        避免 psd-tools composite() 在黑色背景上合成导致的黑色穿透问题。
        
        Returns:
            合成后的 PIL Image（RGBA），尺寸为组的 bbox 尺寸
        """
        from core.render.effects.effects_renderer import render_layer_with_effects

        grp_w = grp_bbox[2] - grp_bbox[0]
        grp_h = grp_bbox[3] - grp_bbox[1]
        
        # 创建透明画布
        canvas = np.zeros((grp_h, grp_w, 4), dtype=np.float32)
        
        def render_subgroup(sub_grp, depth_offset=0):
            """递归渲染子组 - 始终使用手动渲染避免黑色背景"""
            # 递归手动渲染所有子图层
            for child in sub_grp:
                render_layer(child, depth_offset + 1)
        
        def render_layer(layer, depth_offset=0):
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
            
            # 计算图层在画布上的位置
            layer_x = eff_bbox[0] - grp_bbox[0]
            layer_y = eff_bbox[1] - grp_bbox[1]
            layer_w = eff_bbox[2] - eff_bbox[0]
            layer_h = eff_bbox[3] - eff_bbox[1]
            
            layer_arr = ImageArrayUtils.pil_to_float_array(img)
            
            # 合成到 canvas
            y0, y1 = layer_y, layer_y + layer_h
            x0, x1 = layer_x, layer_x + layer_w
            if 0 <= y0 < grp_h and 0 <= x0 < grp_w:
                y1 = min(y1, grp_h)
                x1 = min(x1, grp_w)
                h_copy = y1 - y0
                w_copy = x1 - x0
                if h_copy > 0 and w_copy > 0:
                    canvas[y0:y1, x0:x1] = _alpha_composite_numpy(
                        canvas[y0:y1, x0:x1], layer_arr[:h_copy, :w_copy]
                    )
        
        # 渲染所有子图层
        for child in group_layer:
            render_layer(child)
        
        # 转换为 PIL Image
        return ImageArrayUtils.float_array_to_pil(canvas)
    
    def _render_group_with_hybrid_strategy(
        self, group_layer: Any, grp_bbox: tuple[int, int, int, int],
        expand: int, depth: int
    ) -> 'Image.Image | None':
        """
        混合渲染策略：手动逐层渲染（保留溢出效果），再用 composite() 覆盖内部高质量区域。

        关键事实（PSD composite 的根本限制）：
        - psd-tools 的 composite() **在任何层级都不会输出超出 ancestor.bbox 的效果像素**：
          即使用父组、PSD root + 大 viewport 调用，发光仍被组 bbox 裁切。
        - 【验证】layer-142 OuterGlow blur=24，用 parent.composite(viewport=±80px)：
          仅可见矩形主体（y 80~155），上下各 80px 区域 alpha=0
        - 因此，溢出的 OuterGlow / DropShadow / Stroke 等效果**只能靠手动渲染**

        策略：
        1. 用 render_layer_with_effects 在扩展画布上手动逐层渲染（保留溢出效果）
        2. （TODO）后续可考虑用 group.composite(viewport=grp_bbox) 覆盖内部
           grp_bbox 区域以提升内部清晰度。当前先不覆盖，避免破坏发光。

        Returns:
            合成后的 PIL Image（RGBA），尺寸为 grp_w + 2*expand, grp_h + 2*expand
        """
        from core.render.effects.effects_renderer import render_layer_with_effects

        grp_w = grp_bbox[2] - grp_bbox[0]
        grp_h = grp_bbox[3] - grp_bbox[1]
        ext_w = grp_w + 2 * expand
        ext_h = grp_h + 2 * expand

        # 手动逐层渲染
        canvas = np.zeros((ext_h, ext_w, 4), dtype=np.float32)
        
        def render_subgroup(sub_grp, depth_offset=0):
            """递归渲染子组"""
            # 检查子组bbox是否有效
            sub_bbox = sub_grp.bbox
            sub_w = sub_bbox[2] - sub_bbox[0]
            sub_h = sub_bbox[3] - sub_bbox[1]
            if sub_w <= 0 or sub_h <= 0:
                # 空组，跳过渲染
                return
            
            # 对于子组，使用 composite() 渲染（修复底部多余描边问题）
            try:
                sub_img = sub_grp.composite(viewport=sub_bbox)
                if sub_img and sub_img.mode == 'RGBA':
                    # 计算子组在扩展画布上的位置
                    sub_x = sub_bbox[0] - grp_bbox[0] + expand
                    sub_y = sub_bbox[1] - grp_bbox[1] + expand
                    sub_w = sub_bbox[2] - sub_bbox[0]
                    sub_h = sub_bbox[3] - sub_bbox[1]
                    
                    sub_arr = ImageArrayUtils.pil_to_float_array(sub_img)
                    
                    # 合成到 canvas
                    y0, y1 = sub_y, sub_y + sub_h
                    x0, x1 = sub_x, sub_x + sub_w
                    if 0 <= y0 < ext_h and 0 <= x0 < ext_w:
                        y1 = min(y1, ext_h)
                        x1 = min(x1, ext_w)
                        h_copy = y1 - y0
                        w_copy = x1 - x0
                        if h_copy > 0 and w_copy > 0:
                            canvas[y0:y1, x0:x1] = _alpha_composite_numpy(
                                canvas[y0:y1, x0:x1], sub_arr[:h_copy, :w_copy]
                            )
                    return
            except Exception as e:
                print(f"{'  ' * (depth + depth_offset)}  ⚠️  子组 composite 失败: {e}")
            
            # 降级：递归渲染子图层
            for child in sub_grp:
                render_layer(child, depth_offset + 1)
        
        def render_layer(layer, depth_offset=0):
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
            layer_w = eff_bbox[2] - eff_bbox[0]
            layer_h = eff_bbox[3] - eff_bbox[1]
            
            layer_arr = ImageArrayUtils.pil_to_float_array(img)
            
            # 应用图层不透明度和混合模式
            layer_arr[:, :, 3] *= (layer.opacity / 255.0)
            
            # 合成到 canvas（简化版：仅支持 normal 混合）
            y0, y1 = layer_y, layer_y + layer_h
            x0, x1 = layer_x, layer_x + layer_w
            if 0 <= y0 < ext_h and 0 <= x0 < ext_w:
                y1 = min(y1, ext_h)
                x1 = min(x1, ext_w)
                h_copy = y1 - y0
                w_copy = x1 - x0
                if h_copy > 0 and w_copy > 0:
                    canvas[y0:y1, x0:x1] = _alpha_composite_numpy(
                        canvas[y0:y1, x0:x1], layer_arr[:h_copy, :w_copy]
                    )
        
        # 渲染所有子图层
        for child in group_layer:
            render_layer(child)
        
        # 转回 PIL Image
        result_img = ImageArrayUtils.float_array_to_pil(canvas)
        return result_img

    def _can_merge_group(self, group_layer: Any) -> bool:
        """
        判断组是否可以合并为单张图片。

        条件（全部满足）：
        1. 组内全是图片图层（子组也递归检查是否全是图片，文本图层不允许）
        2. 所有图层都不包含需要扩展画布的效果（无外描边、外发光、投影）
        3. 组的 bbox 不能严重超出画布（避免生成超大图片）
        4. 至少有一个可见图层
        
        注意：如果是按钮组（包含文本+图片），也允许合并（特殊处理）
        """
        # 总开关：smart_merge=False 直接放弃组级合图，保留完整组结构
        if not self.smart_merge:
            return False

        # 条件3：检查组 bbox 是否严重超出画布
        # 允许小幅超出（如边缘效果），但不允许尺寸超过画布的 2 倍
        grp_bbox = group_layer.bbox
        grp_w = grp_bbox[2] - grp_bbox[0]
        grp_h = grp_bbox[3] - grp_bbox[1]
        
        if grp_w > self.canvas_width * 2 or grp_h > self.canvas_height * 2:
            return False
        
        # 【修复】移除特殊混合模式的阻断逻辑
        # 对于 SCREEN 等混合模式，composite() 才是正确的处理方式：
        # - 单独导出会保留原始黑色像素（97%黑色）
        # - 组级 composite() 会正确执行混合运算，让黑色与下层混合后消失
        # 因此包含 SCREEN 的组反而**应该**使用 composite() 合并
        # 注释掉旧的阻断逻辑
        # if self._should_use_manual_render(group_layer):
        #     return False
        
        # **按钮特殊处理**：如果是按钮组，允许包含文本图层
        # 按钮组即使有效果溢出，也应该合并（因为按钮就是要文本+背景一起导出）
        # 只是需要在合并时使用特殊的渲染方式（扩展画布）
        if self._is_button_group(group_layer):
            return True  # 按钮组总是允许合并
        
        
        has_visible = False
        for child in group_layer:
            # 跳过隐藏和透明图层（不影响判断）
            if not child.visible or child.opacity == 0:
                continue

            has_visible = True

            # 如果是子组，递归检查子组是否也全是图片
            if child.is_group():
                if not self._can_merge_group(child):
                    return False
                continue

            # 条件1：不能有文本图层（普通组）
            kind = str(child.kind) if hasattr(child, 'kind') else ''
            if 'type' in kind.lower():
                return False

            # 条件2：不能有扩展画布的效果
            if LayerExporter._has_canvas_expanding_effects(child):
                return False

        return has_visible

    def _can_merge_group_non_text(self, group_layer: Any) -> bool:
        """
        判断组是否适合"非文本图层合并为一张背景图 + 文本图层独立导出"。

        条件（全部满足）：
        1. 组内**直接子图层**不包含子组（有子组则需保留结构交给子组自行处理）
        2. 组内存在**至少一个可见文本图层**（type 图层），
           否则走原 `_can_merge_group` 全量合并
        3. 组内存在**至少一个可见非文本图层**（image/shape/smartobject），
           否则无背景可合并
        4. 组的 bbox 不能严重超出画布
        5. 不是按钮组（按钮组走原"文本+背景一起合"逻辑）
        """
        # 总开关：smart_merge=False 放弃"非文本合成背景图"策略
        if not self.smart_merge:
            return False

        # 条件4：尺寸保护
        grp_bbox = group_layer.bbox
        grp_w = grp_bbox[2] - grp_bbox[0]
        grp_h = grp_bbox[3] - grp_bbox[1]
        if grp_w <= 0 or grp_h <= 0:
            return False
        if grp_w > self.canvas_width * 2 or grp_h > self.canvas_height * 2:
            return False

        # 条件5：按钮组特殊保留
        if self._is_button_group(group_layer):
            return False

        # 条件1、2、3：只看直接子图层
        has_text = False
        has_non_text = False
        for child in group_layer:
            if not child.visible or child.opacity == 0:
                continue
            # 直接子图层若是组 → 不适用本策略
            if child.is_group():
                return False
            kind = str(child.kind) if hasattr(child, 'kind') else ''
            if 'type' in kind.lower():
                has_text = True
            else:
                has_non_text = True

        return has_text and has_non_text

    def _collect_text_layers(self, group_layer: Any) -> list[Any]:
        """收集组内**直接**可见文本图层（type 图层）。"""
        out: list[Any] = []
        for child in group_layer:
            if not child.visible or child.opacity == 0:
                continue
            if child.is_group():
                continue
            kind = str(child.kind) if hasattr(child, 'kind') else ''
            if 'type' in kind.lower():
                out.append(child)
        return out

    def _merge_group_non_text_as_image(
        self, group_layer: Any, group_name: str, full_name: str,
        depth: int, parent_left: int, parent_top: int,
        clip_bbox: tuple[int, int, int, int] | None = None,
    ) -> dict[str, Any] | None:
        """
        合并组内的非文本图层为单张背景图。

        实现方式：临时将组内所有可见文本图层 `visible` 置为 False，
        调用 `_merge_group_as_single_image` 复用现有合成流水线（含效果溢出
        混合渲染策略），完成后恢复 visible。
        """
        text_layers = self._collect_text_layers(group_layer)
        if not text_layers:
            # 没文本，直接走常规合并
            return self._merge_group_as_single_image(
                group_layer, group_name, full_name, depth,
                parent_left, parent_top, clip_bbox=clip_bbox,
            )

        saved: list[tuple[Any, bool]] = []
        try:
            for t in text_layers:
                saved.append((t, t.visible))
                try:
                    t.visible = False
                except Exception:
                    pass

            print(
                f"{'  ' * depth}🎯 {group_name} "
                f"(非文本图层合并为背景图，文本独立：{len(text_layers)} 个文本)"
            )
            merged = self._merge_group_as_single_image(
                group_layer, group_name, full_name, depth,
                parent_left, parent_top, clip_bbox=clip_bbox,
            )
            return merged
        finally:
            for t, vis in saved:
                try:
                    t.visible = vis
                except Exception:
                    pass

    def _adjust_children_offset(self, children: list[dict], offset_x: int, offset_y: int) -> None:
        """
        递归调整子图层的相对坐标偏移。
        
        当父组的 bbox 被约束时（比如从 -904 变成 0），子图层的相对坐标需要相应调整。
        
        关键原则：
        - 直接子图层（image/text）：调整其相对坐标
        - 子组（group）：只调整组自己的坐标，**不递归调整其子图层**
          因为子组的子图层已经是相对于子组的坐标，不受父组约束影响
        
        Args:
            children: 子图层列表
            offset_x: X 轴偏移量
            offset_y: Y 轴偏移量
        """
        for child in children:
            # 调整当前图层的坐标
            child['left'] += offset_x
            child['top'] += offset_y
            
            # ❌ 不递归调整子组的子图层！
            # 子组的子图层已经是相对于子组的坐标，不应该受父组约束的影响

    def _merge_group_as_single_image(
        self, group_layer: Any, group_name: str, full_name: str,
        depth: int, parent_left: int, parent_top: int,
        clip_bbox: tuple[int, int, int, int] | None = None,
    ) -> dict[str, Any] | None:
        """
        使用混合渲染策略将组内所有图层合并为单张图片导出。
        
        混合渲染策略（修复效果质量问题）：
        1. 检测组内是否有效果溢出（OuterGlow/Stroke等）
        2. 如果有溢出：
           a) 在扩展画布上手动逐层渲染（保留溢出效果）
           b) 用 composite() 覆盖内部区域（获得 PS 原生高质量渲染）
        3. 如果无溢出：直接使用 composite() 渲染
        
        这样可以同时获得：
        - 内部区域：PS 原生渲染质量（像素级完美）
        - 外部区域：完整的溢出效果（如外发光、外描边）
        
        Args:
            clip_bbox: 可选的裁剪区域 (left, top, right, bottom)，用于裁剪到父组范围
        """
        try:
            from core.render.effects.effects_renderer import render_layer_with_effects

            # 使用组的 bbox 作为 viewport 进行 composite
            grp_bbox = group_layer.bbox
            grp_w = grp_bbox[2] - grp_bbox[0]
            grp_h = grp_bbox[3] - grp_bbox[1]
            if grp_w <= 0 or grp_h <= 0:
                return None

            child_names = [c.name for c in group_layer if c.visible and c.opacity > 0]
            
            # 检查是否为按钮组，并输出相应的提示信息
            is_button = self._is_button_group(group_layer)
            if is_button:
                print(f"{'  ' * depth}🔘 按钮组: {group_name} ({len(child_names)}层，文本+背景合并导出)")
            else:
                print(f"{'  ' * depth}🔗 合并组图层: {group_name} ({len(child_names)}层: {child_names})")

            # ========== 步骤1：渲染组图层 ==========
            # 情况1：效果溢出（外描边、外发光等） - 使用混合渲染策略
            expand = self._calc_group_expand(group_layer)
            
            # 【修复】移除特殊混合模式的阻断
            # 对于 SCREEN 混合模式，composite() 会正确处理混合运算
            # 之前的逻辑误以为 composite() 会产生黑色背景，但实际上：
            # - 组级 composite() 会正确执行混合：黑色+下层 → 下层显示
            # - 单独导出才会保留黑色像素
            
            if expand > 0:
                print(f"{'  ' * depth}  🌟 检测到效果溢出 {expand}px，使用混合渲染策略")
                # 使用混合渲染策略
                composite_img = self._render_group_with_hybrid_strategy(
                    group_layer, grp_bbox, expand, depth
                )
            else:
                # 无溢出，直接使用 composite() 渲染
                expand = 0
                # 使用默认参数即可，psd-tools 会正确处理混合模式
                composite_img = group_layer.composite(viewport=grp_bbox)
            
            if composite_img is None:
                print(f"{'  ' * depth}  ⚠️  composite() 返回 None，回退到逐层导出")
                return None

            if composite_img.mode != 'RGBA':
                composite_img = composite_img.convert('RGBA')

            # 保存原始坐标（用于计算相对位置）
            # 如果使用了混合渲染（expand > 0），图片尺寸是 grp_w+2*expand, grp_h+2*expand
            # 图片的实际位置需要向左上偏移 expand 像素
            orig_abs_left = grp_bbox[0] - expand
            orig_abs_top = grp_bbox[1] - expand
            actual_w = grp_w + 2 * expand
            actual_h = grp_h + 2 * expand
            
            # 【修复】只裁剪到画布边界，不裁剪到父组边界
            # 这样可以保留完整的组效果（描边、阴影等），然后在 HTML 中通过扩展父组来容纳
            adj_left = 0
            adj_top = 0
            
            # 计算扩展后的边界
            left = grp_bbox[0] - expand
            top = grp_bbox[1] - expand
            right = grp_bbox[2] + expand
            bottom = grp_bbox[3] + expand
            
            # 只裁剪到画布边界
            clip_left, clip_top = 0, 0
            clip_right, clip_bottom = self.psd.width, self.psd.height
            
            cl = max(clip_left, left)
            ct = max(clip_top, top)
            cr = min(clip_right, right)
            cb = min(clip_bottom, bottom)
            
            if cr > cl and cb > ct:
                # 裁剪图片（仅裁剪超出画布的部分）
                crop_left = cl - left
                crop_top = ct - top
                crop_right = composite_img.size[0] - (right - cr)
                crop_bottom = composite_img.size[1] - (bottom - cb)
                
                if crop_right > crop_left and crop_bottom > crop_top:
                    composite_img = composite_img.crop((crop_left, crop_top, crop_right, crop_bottom))
                    adj_left = cl - left
                    adj_top = ct - top
                    # 更新尺寸
                    actual_w = cr - cl
                    actual_h = cb - ct
                else:
                    print(f"{'  ' * depth}🚫 {group_name} (裁剪后为空)")
                    return None
            else:
                print(f"{'  ' * depth}🚫 {group_name} (完全在裁剪区域外)")
                return None

            # 检查是否完全透明
            img_arr = np.array(composite_img)
            if img_arr[:, :, 3].max() == 0:
                print(f"{'  ' * depth}🚫 {group_name} (合并后完全透明)")
                return None

            self._z_counter += 1
            # 使用原始坐标计算相对位置
            rel_left = orig_abs_left - parent_left + adj_left
            rel_top = orig_abs_top - parent_top + adj_top

            layer_info: dict[str, Any] = {
                'id': f'layer-{self._z_counter}',
                'name': group_name,
                'full_name': full_name,
                'left': rel_left,
                'top': rel_top,
                'width': actual_w,
                'height': actual_h,
                'opacity': group_layer.opacity / 255.0,
                'blend_mode': BLEND_MODES.get(str(group_layer.blend_mode), 'normal'),
                'z_index': self._z_counter,
                'type': 'image',
            }

            # 保存图片（去重）
            rel_path = self._save_image_dedup(composite_img, group_name, depth)
            layer_info['image_path'] = rel_path

            visible_count = len(child_names)
            total_count = len(list(group_layer))
            print(f"{'  ' * depth}🖼️  {group_name} [合并{visible_count}/{total_count}层 {actual_w}x{actual_h}] → {rel_path}")
            self.exported_count += total_count
            return layer_info

        except Exception as e:
            print(f"{'  ' * depth}❌ 合并组 {group_name} 失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _merge_clipping_group(
        self, base_layer: Any, clipped_layers: list[Any],
        parent_name: str, depth: int,
        parent_left: int, parent_top: int,
    ) -> dict[str, Any] | None:
        """
        将 base + clipped 图层合并渲染为单张图片并导出。
        Photoshop 渲染顺序：先在 base 原始内容上合成 clip 层，再应用 base 效果。
        """
        try:
            base_name = base_layer.name or 'merged'
            clipped_names = [c.name for c in clipped_layers]
            full_name = f'{parent_name}/{base_name}' if parent_name else base_name
            print(f"{'  ' * depth}🔗 合并剪切蒙版: {base_name} ← {clipped_names}")

            # --- 步骤1: 获取 base 原始图层（不含效果）---
            base_raw_img = base_layer.topil()
            if base_raw_img is None:
                return None
            if base_raw_img.mode != 'RGBA':
                base_raw_img = base_raw_img.convert('RGBA')

            raw_bbox = base_layer.bbox  # 原始 bbox（无效果扩展）
            raw_w = raw_bbox[2] - raw_bbox[0]
            raw_h = raw_bbox[3] - raw_bbox[1]
            if raw_w <= 0 or raw_h <= 0:
                return None

            # --- 步骤2: 在 base 原始内容上合成 clip 层 ---
            canvas_raw = np.array(
                base_raw_img.resize((raw_w, raw_h)) if base_raw_img.size != (raw_w, raw_h) else base_raw_img,
                dtype=np.float32
            ) / 255.0

            # base 原始 alpha 用于 clip
            base_alpha = canvas_raw[:, :, 3:4].copy()

            for cl in clipped_layers:
                if not cl.visible or cl.opacity == 0:
                    continue
                try:
                    # 对 clip 层使用效果渲染（处理 ColorOverlay 等）
                    cl_effect_result = render_layer_with_effects(cl)
                    if cl_effect_result is not None:
                        cl_img, cl_bbox = cl_effect_result
                    else:
                        cl_img = cl.topil()
                        cl_bbox = cl.bbox
                    if cl_img is None:
                        continue
                    if cl_img.mode != 'RGBA':
                        cl_img = cl_img.convert('RGBA')

                    # 应用图层蒙版（user mask）
                    cl_img = _apply_layer_mask(cl, cl_img, cl_bbox)

                    cl_arr = ImageArrayUtils.pil_to_float_array(cl_img)

                    ox = cl_bbox[0] - raw_bbox[0]
                    oy = cl_bbox[1] - raw_bbox[1]
                    cl_h, cl_w = cl_arr.shape[:2]

                    src_x0 = max(0, -ox)
                    src_y0 = max(0, -oy)
                    dst_x0 = max(0, ox)
                    dst_y0 = max(0, oy)
                    copy_w = min(cl_w - src_x0, raw_w - dst_x0)
                    copy_h = min(cl_h - src_y0, raw_h - dst_y0)

                    if copy_w <= 0 or copy_h <= 0:
                        continue

                    src_region = cl_arr[src_y0:src_y0+copy_h, src_x0:src_x0+copy_w]
                    dst_region = canvas_raw[dst_y0:dst_y0+copy_h, dst_x0:dst_x0+copy_w]
                    clip_region = base_alpha[dst_y0:dst_y0+copy_h, dst_x0:dst_x0+copy_w]

                    layer_opacity = cl.opacity / 255.0
                    src_alpha = src_region[:, :, 3:4] * layer_opacity
                    # Clip to base alpha: 使用连续 alpha 值（而非 binary）
                    # Photoshop 剪切蒙版将 clip 层的 alpha 乘以 base alpha，
                    # 使 clip 层在 base 半透明边缘处也能平滑过渡
                    src_alpha = src_alpha * clip_region

                    src_rgb = src_region[:, :, :3]
                    dst_rgb = dst_region[:, :, :3]
                    dst_alpha_ch = dst_region[:, :, 3:4]

                    blend_mode = str(cl.blend_mode)
                    if 'OVERLAY' in blend_mode.upper():
                        blended = np.where(
                            dst_rgb < 0.5,
                            2.0 * src_rgb * dst_rgb,
                            1.0 - 2.0 * (1.0 - src_rgb) * (1.0 - dst_rgb)
                        )
                    elif 'MULTIPLY' in blend_mode.upper():
                        blended = src_rgb * dst_rgb
                    elif 'SCREEN' in blend_mode.upper():
                        blended = 1.0 - (1.0 - src_rgb) * (1.0 - dst_rgb)
                    elif 'SOFT' in blend_mode.upper():
                        blended = np.where(
                            src_rgb < 0.5,
                            dst_rgb - (1.0 - 2.0 * src_rgb) * dst_rgb * (1.0 - dst_rgb),
                            dst_rgb + (2.0 * src_rgb - 1.0) * (np.sqrt(dst_rgb) - dst_rgb)
                        )
                    elif 'HARD' in blend_mode.upper():
                        blended = np.where(
                            src_rgb < 0.5,
                            2.0 * src_rgb * dst_rgb,
                            1.0 - 2.0 * (1.0 - src_rgb) * (1.0 - dst_rgb)
                        )
                    else:
                        blended = src_rgb

                    # Porter-Duff OVER 合成（标准 alpha 合成公式）
                    # 比 max(dst_a, src_a) 更准确，边缘过渡更平滑
                    out_alpha = src_alpha + dst_alpha_ch * (1.0 - src_alpha)
                    safe_out_alpha = np.where(out_alpha > 0, out_alpha, 1.0)
                    out_rgb = (blended * src_alpha + dst_rgb * dst_alpha_ch * (1.0 - src_alpha)) / safe_out_alpha

                    canvas_raw[dst_y0:dst_y0+copy_h, dst_x0:dst_x0+copy_w, :3] = np.clip(out_rgb, 0, 1)
                    canvas_raw[dst_y0:dst_y0+copy_h, dst_x0:dst_x0+copy_w, 3:4] = np.clip(out_alpha, 0, 1)

                except Exception as e:
                    print(f"{'  ' * depth}  ⚠️  合成 {cl.name} 失败: {e}")

            # --- 步骤2.5: 应用 base 图层的 user mask（图层蒙版）---
            merged_raw = ImageArrayUtils.float_array_to_pil(canvas_raw)
            merged_raw = _apply_layer_mask(base_layer, merged_raw, raw_bbox)

            # --- 步骤3: 应用 base 的效果 ---
            # 检查 base 是否有效果需要应用（同时考虑图层样式整体开关）
            has_effects = False
            if hasattr(base_layer, 'effects') and base_layer.effects:
                for e in base_layer.effects:
                    if is_effect_active(e, base_layer):
                        has_effects = True
                        break

            if has_effects:
                # 用合并后的图像替代 base 原始图像，重新渲染效果
                from core.render.effects.effects_renderer import render_layer_with_effects_on_image
                final_result = render_layer_with_effects_on_image(
                    base_layer, merged_raw, raw_bbox
                )
                if final_result is not None:
                    final_img, final_bbox = final_result
                else:
                    final_img = merged_raw
                    final_bbox = raw_bbox
            else:
                final_img = merged_raw
                final_bbox = raw_bbox

            if final_img.mode != 'RGBA':
                final_img = final_img.convert('RGBA')

            canvas_left = final_bbox[0]
            canvas_top = final_bbox[1]
            canvas_w = final_bbox[2] - final_bbox[0]
            canvas_h = final_bbox[3] - final_bbox[1]

            self._z_counter += 1
            abs_left = canvas_left
            abs_top = canvas_top
            rel_left = abs_left - parent_left
            rel_top = abs_top - parent_top

            layer_info: dict[str, Any] = {
                'id': f'layer-{self._z_counter}',
                'name': base_name,
                'full_name': full_name,
                'left': rel_left,
                'top': rel_top,
                'width': canvas_w,
                'height': canvas_h,
                'opacity': base_layer.opacity / 255.0,
                'blend_mode': BLEND_MODES.get(str(base_layer.blend_mode), 'normal'),
                'z_index': self._z_counter,
                'type': 'image',
            }

            # 保存图片（去重）
            rel_path = self._save_image_dedup(final_img, base_name, depth)
            layer_info['image_path'] = rel_path

            print(f"{'  ' * depth}🖼️  {base_name} [合并{len(clipped_layers)+1}层 {canvas_w}x{canvas_h}] → {rel_path}")
            self.exported_count += 1
            # 被合并的 clipped 图层计入已导出
            self.exported_count += len(clipped_layers)
            return layer_info

        except Exception as e:
            print(f"{'  ' * depth}❌ 合并剪切蒙版失败 ({base_layer.name}): {e}")
            import traceback
            traceback.print_exc()
            return None

    def export_layers(
        self,
        layers: Any,
        parent_name: str = '',
        depth: int = 0,
        parent_left: int = 0,
        parent_top: int = 0,
        parent_clip_bbox: tuple[int, int, int, int] | None = None,
    ) -> list[dict[str, Any]]:
        """
        递归导出图层，**保留 PSD 组结构**。

        组 → { type: 'group', children: [...] }
        普通图层 → { type: 'image' | 'text', ... }
        剪切蒙版组 → 多个图层合并为单张图片

        子图层的 left/top 会转换为**相对于父组**的坐标。
        根图层（depth=0）使用画布绝对坐标。

        Args:
            layers: PSD 图层可迭代对象
            parent_name: 父图层路径名
            depth: 当前递归深度
            parent_left: 父组的画布绝对 left
            parent_top: 父组的画布绝对 top
            parent_clip_bbox: 父组的裁剪区域 (left, top, right, bottom)，用于约束子图层

        Returns:
            层级化的图层信息列表
        """
        result: list[dict[str, Any]] = []

        # 先将图层按 clipping 关系分组
        layers_list = list(layers)

        # 顶层（depth=0）：检测底部连续背景图层并合并
        bg_layer_ids: set[int] = set()
        if depth == 0:
            bg_layers = self._detect_background_layers(layers_list)
            if bg_layers:
                merged_bg = self._merge_background_layers(
                    bg_layers, depth, parent_left, parent_top,
                )
                if merged_bg:
                    result.append(merged_bg)
                    bg_layer_ids = {id(l) for l in bg_layers}

        grouped = self._group_clipping_layers(layers_list)

        # 决策链（Chain of Responsibility）驱动：
        # 每个 item 依次经过 [BackgroundSkip / ClippingGroup / Invisible / Group / Leaf]，
        # 首个 can_handle 的 handler 完成处理并终止。
        for item in grouped:
            hctx = HandlerContext(
                exporter=self,
                item=item,
                depth=depth,
                parent_name=parent_name,
                parent_left=parent_left,
                parent_top=parent_top,
                parent_clip_bbox=parent_clip_bbox,
                bg_layer_ids=bg_layer_ids,
            )
            result.extend(run_handlers(hctx))

        return result

    def verify_export(self) -> None:
        """打印导出统计"""
        print(f"\n📊 图层导出统计:")
        print(f"  ✅ 成功: {self.exported_count}")
        print(f"  🚫 跳过: {self.skipped_count}")
        if self._dedup_count > 0:
            print(f"  ♻️  去重: {self._dedup_count} 张图片复用 (实际保存 {len(self._image_hash_map)} 张)")
        if self.exported_count == 0:
            print("  ⚠️  警告: 没有导出任何图层!")

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _export_single_layer(
        self, layer: Any, layer_name: str, full_name: str, depth: int,
        parent_left: int = 0, parent_top: int = 0,
        clip_bbox: tuple[int, int, int, int] | None = None,
    ) -> dict[str, Any] | None:
        """导出单个图层（图片或文本）
        
        Args:
            clip_bbox: 可选的裁剪区域 (left, top, right, bottom)，用于裁剪到父组范围
        """
        try:
            self._z_counter += 1

            # 画布绝对坐标
            abs_left = layer.left if hasattr(layer, 'left') else layer.bbox[0]
            abs_top = layer.top if hasattr(layer, 'top') else layer.bbox[1]
            width = layer.bbox[2] - layer.bbox[0]
            height = layer.bbox[3] - layer.bbox[1]

            # 相对于父组的坐标
            rel_left = abs_left - parent_left
            rel_top = abs_top - parent_top

            layer_info: dict[str, Any] = {
                'id': f'layer-{self._z_counter}',
                'name': layer_name,
                'full_name': full_name,
                'left': rel_left,
                'top': rel_top,
                'width': width,
                'height': height,
                'opacity': layer.opacity / 255.0,
                'blend_mode': BLEND_MODES.get(str(layer.blend_mode), 'normal'),
                'z_index': self._z_counter,
            }

            # 判断是否为文本图层
            kind = str(layer.kind) if hasattr(layer, 'kind') else ''
            is_text = 'type' in kind.lower()

            if is_text:
                # 有旋转/倾斜的文本无法用 CSS 还原，降级为图片
                if TextExtractor.has_transform(layer):
                    print(f"{'  ' * depth}🔄 {layer_name} (文本有旋转/倾斜 → 降级为图片)")
                    is_text = False
                # 有效果（描边/阴影/发光）的文本降级为图片，由效果渲染器处理
                # 注意：必须同时满足"图层样式整体开关 enabled=True"和"至少一个子效果 enabled=True"
                # PSD 中 effects.enabled=False 时，PS 实际不渲染任何效果（即使子项 enabled=True）
                elif hasattr(layer, 'effects') and layer.effects and any(
                    is_effect_active(e, layer) for e in layer.effects
                ):
                    print(f"{'  ' * depth}✨ {layer_name} (文本有效果 → 降级为图片)")
                    is_text = False
                else:
                    text_data = TextExtractor.extract_text_info(
                        layer, layer_height=height, layer_width=width,
                    )
                    if text_data:
                        layer_info['type'] = 'text'
                        layer_info['text'] = text_data['text']
                        layer_info['text_style'] = text_data['style']
                        preview = text_data['text'][:20].replace('\n', '\\n')
                        print(f"{'  ' * depth}📝 {layer_name} [\"{preview}\"]")
                        self.exported_count += 1
                        return layer_info
                    else:
                        is_text = False

            # 导出为图片
            img_result = self._export_layer_image(layer, layer_name, depth, clip_bbox=clip_bbox)
            if img_result:
                layer_info['type'] = 'image'
                layer_info['image_path'] = img_result['path']
                # 应用裁剪 / 效果扩展导致的坐标调整
                layer_info['left'] += img_result['adj_left']
                layer_info['top'] += img_result['adj_top']
                # 使用效果渲染后的实际尺寸（描边/阴影/发光等会扩展图片）
                if 'actual_width' in img_result:
                    layer_info['width'] = img_result['actual_width']
                    layer_info['height'] = img_result['actual_height']
                self.exported_count += 1
                return layer_info
            else:
                self.skipped_count += 1
                return None

        except Exception as e:
            print(f"{'  ' * depth}❌ {layer_name} 导出失败: {e}")
            self.skipped_count += 1
            return None

    def _export_layer_image(
        self, layer: Any, layer_name: str, depth: int,
        clip_bbox: tuple[int, int, int, int] | None = None,
    ) -> dict[str, Any] | None:
        """
        导出图层为图片文件。
        优先使用效果渲染器（处理描边/阴影/发光等），失败则回退到普通 topil()。
        
        Args:
            clip_bbox: 可选的裁剪区域 (left, top, right, bottom)，用于裁剪到父组范围
        """
        # ── 1. 尝试带效果渲染 ──
        img: Image.Image | None = None
        bbox = layer.bbox  # (left, top, right, bottom)

        effect_result = render_layer_with_effects(layer)
        if effect_result is not None:
            img, bbox = effect_result
            if img is not None and img.size[0] > 0 and img.size[1] > 0:
                has_fx = hasattr(layer, 'effects') and layer.effects and any(
                    is_effect_active(e, layer) for e in layer.effects
                )
                if has_fx:
                    print(f"{'  ' * depth}✨ {layer_name} (含效果渲染)")
            else:
                img = None  # 回退

        # ── 2. 回退：普通 topil() ──
        if img is None:
            try:
                img = layer.topil()
                bbox = layer.bbox
            except Exception as e:
                print(f"{'  ' * depth}⚠️  {layer_name} topil 失败: {e}")
                img = None
            
            # ── 2.1. 如果 topil() 失败，尝试 composite() ──
            # shape 图层（如矩形、圆形、进度条）的 topil() 可能返回 None
            # 但 composite() 可以正常渲染
            if img is None and hasattr(layer, 'composite'):
                try:
                    img = layer.composite()
                    bbox = layer.bbox
                    if img is not None and img.size[0] > 0 and img.size[1] > 0:
                        print(f"{'  ' * depth}🔷 {layer_name} (shape图层，使用composite渲染)")
                except Exception as e:
                    print(f"{'  ' * depth}⚠️  {layer_name} composite 失败: {e}")
                    return None

        if img is None or img.size[0] == 0 or img.size[1] == 0:
            print(f"{'  ' * depth}🚫 {layer_name} (空图层)")
            return None

        # ── 2.5. 应用图层蒙版（user mask）──
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        img = _apply_layer_mask(layer, img, bbox)

        # 检查 mask 后是否完全透明
        img_check = np.array(img)
        if img_check[:, :, 3].max() == 0:
            print(f"{'  ' * depth}🚫 {layer_name} (mask后完全透明)")
            return None

        # ── 3. 裁剪到画布边界（保留子图层效果，不裁剪到父组边界）──
        left, top, right, bottom = bbox
        
        # 【修复】只裁剪到画布边界，不裁剪到父组边界
        # 这样可以保留完整的图层效果（如描边），然后在 HTML 中通过扩展父组来容纳
        clip_left, clip_top = 0, 0
        clip_right, clip_bottom = self.psd.width, self.psd.height

        cl = max(clip_left, left)
        ct = max(clip_top, top)
        cr = min(clip_right, right)
        cb = min(clip_bottom, bottom)

        if cr <= cl or cb <= ct:
            print(f"{'  ' * depth}🚫 {layer_name} (完全在裁剪区域外)")
            return None

        adj_left = 0
        adj_top = 0

        if left < clip_left or top < clip_top or right > clip_right or bottom > clip_bottom:
            crop_left = cl - left
            crop_top = ct - top
            crop_right = img.size[0] - (right - cr)
            crop_bottom = img.size[1] - (bottom - cb)
            if crop_right > crop_left and crop_bottom > crop_top:
                img = img.crop((crop_left, crop_top, crop_right, crop_bottom))
                adj_left = cl - left
                adj_top = ct - top
            else:
                print(f"{'  ' * depth}🚫 {layer_name} (裁剪后为空)")
                return None

        if img.size[0] == 0 or img.size[1] == 0:
            print(f"{'  ' * depth}🚫 {layer_name} (裁剪后为空)")
            return None

        # ── 4. 保存图片（去重）──
        rel_path = self._save_image_dedup(img, layer_name, depth)

        print(f"{'  ' * depth}🖼️  {layer_name} [{img.size[0]}x{img.size[1]}] → {rel_path}")

        # 计算效果渲染后的实际坐标偏移（相对于原始 layer bbox）
        # 效果（如描边/阴影）会扩展 bbox，需要把偏移传回给调用方
        orig_left = layer.bbox[0]
        orig_top = layer.bbox[1]
        actual_left = left + adj_left  # 裁剪后的实际起始坐标
        actual_top = top + adj_top

        return {
            'path': rel_path,
            'adj_left': actual_left - orig_left,
            'adj_top': actual_top - orig_top,
            'actual_width': img.size[0],
            'actual_height': img.size[1],
        }
