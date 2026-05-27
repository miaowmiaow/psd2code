#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图层导出模块（纯解析版）
- 一 PSD 图层 = 一 layer_info（叶图层）或 group_info（组）
- 解析阶段不做任何"装饰性合图"
- 唯一例外：PSD 原生剪贴蒙版语义（CSS 无法等价还原），见 _merge_clipping_group
  与 _export_clipped_layer_against_group_base
- 文本图层保留为 text 类型，有旋转/倾斜或图层效果的文本降级为图片
- 图片图层会渲染描边/阴影/发光等效果（PSD 像素级还原）
- 图片命名 = 原图层名（拼音）+ 内容指纹

合图（多 PNG → 1 PNG、多 div 折叠为 1 div）的优化由下游
LayoutOptimizer (targets/html/postprocess/layout_optimizer) 接管。
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
    """图层导出器（纯解析版） - 保留完整层级结构，1 图层 = 1 div = 1 PNG"""

    def __init__(self, psd: Any, output_dir: Path):
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

    def _merge_clipping_group(
        self, base_layer: Any, clipped_layers: list[Any],
        parent_name: str, depth: int,
        parent_left: int, parent_top: int,
    ) -> dict[str, Any] | None:
        """
        将 base + clipped 图层合并渲染为单张图片并导出。
        Photoshop 渲染顺序：先在 base 原始内容上合成 clip 层，再应用 base 效果。

        ⚠️ 这是 PSD 像素语义的还原（CSS 无法等价），不是装饰性合图。
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
        剪切蒙版组 → 多个图层合并为单张图片（PSD 原生语义还原）

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
        grouped = self._group_clipping_layers(layers_list)

        # 决策链（Chain of Responsibility）驱动：
        # 每个 item 依次经过 [ClippingGroup / Invisible / Group / Leaf]，
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

    def _export_clipped_layer_against_group_base(
        self,
        cl: Any,
        base_group: Any,
        layer_name: str,
        full_name: str,
        depth: int,
        parent_left: int = 0,
        parent_top: int = 0,
    ) -> dict[str, Any] | None:
        """
        导出剪贴蒙版图层（clipped layer），用 base group 的 alpha 通道做剪裁。

        PS 中 clipping=1 的图层只在下方 base 的 alpha 范围内可见。
        当 base 是 group 时，旧路径走 _export_single_layer 直接导出 clipped 自身的全
        bbox 像素（如 719×152 的黄色光斑），完全忽略 base group 提供的剪裁形状，
        导致 HTML 中出现 PSD 视觉里"看不见"的大色块。

        本方法用 base_group.composite() 的 alpha 通道与 cl 自身像素相乘，再裁剪空白边
        缘，得到与 PS 视觉一致的小图。

        坐标返回与 _export_single_layer 一致：相对父组的 (left, top, width, height)。
        """
        try:
            self._z_counter += 1

            # ── 1. 渲染 cl 自身（含图层效果 / 蒙版）──
            cl_img = None
            cl_bbox = cl.bbox
            try:
                effect_result = render_layer_with_effects(cl)
                if effect_result is not None:
                    eimg, ebbox = effect_result
                    if eimg is not None and eimg.size[0] > 0 and eimg.size[1] > 0:
                        cl_img = eimg
                        cl_bbox = ebbox
            except Exception:
                cl_img = None

            if cl_img is None:
                try:
                    cl_img = cl.topil()
                except Exception:
                    cl_img = None

            if cl_img is None and hasattr(cl, 'composite'):
                try:
                    cl_img = cl.composite()
                except Exception:
                    cl_img = None

            if cl_img is None or cl_img.size[0] == 0 or cl_img.size[1] == 0:
                print(f"{'  ' * depth}🚫 {layer_name} (空剪贴层)")
                self.skipped_count += 1
                return None

            if cl_img.mode != 'RGBA':
                cl_img = cl_img.convert('RGBA')

            cl_img = _apply_layer_mask(cl, cl_img, cl_bbox)

            # ── 2. 取 base group 的 alpha mask ──
            base_alpha_img = None
            try:
                base_alpha_img = base_group.composite()
            except Exception:
                base_alpha_img = None
            if base_alpha_img is None or base_alpha_img.size[0] == 0 or base_alpha_img.size[1] == 0:
                # base 无法 composite → 退回不剪裁的导出（保留旧行为）
                print(
                    f"{'  ' * depth}⚠️  {layer_name} 剪贴 base '{base_group.name}' "
                    f"composite 失败，回退普通导出"
                )
                # 通过 _export_single_layer 的逻辑兜底
                # 此处回滚 z_counter，让 _export_single_layer 自行 +1
                self._z_counter -= 1
                return self._export_single_layer(
                    cl, layer_name, full_name, depth, parent_left, parent_top
                )
            if base_alpha_img.mode != 'RGBA':
                base_alpha_img = base_alpha_img.convert('RGBA')
            base_bbox = base_group.bbox

            # ── 3. 把 cl 限制到 base bbox 内（位置/尺寸都收缩） ──
            cl_left, cl_top, cl_right, cl_bottom = cl_bbox
            base_left, base_top, base_right, base_bottom = base_bbox

            inter_left = max(cl_left, base_left)
            inter_top = max(cl_top, base_top)
            inter_right = min(cl_right, base_right)
            inter_bottom = min(cl_bottom, base_bottom)

            if inter_right <= inter_left or inter_bottom <= inter_top:
                print(f"{'  ' * depth}🚫 {layer_name} (剪贴交集为空)")
                self.skipped_count += 1
                return None

            iw = inter_right - inter_left
            ih = inter_bottom - inter_top

            # 取 cl_img 内对应交集的 patch
            cl_arr = np.array(cl_img, dtype=np.float32) / 255.0
            cl_patch = cl_arr[
                inter_top - cl_top: inter_top - cl_top + ih,
                inter_left - cl_left: inter_left - cl_left + iw,
            ].copy()

            # ── 4. 按 base 形状剪裁 ──
            # PS 行为差异：
            #   • base 为"普通组"（NORMAL 等独立合成模式）→ clipped 被 base 的实际 alpha
            #     形状剪裁（如文字形状）
            #   • base 为 PASS_THROUGH 组 → 组内不形成独立合成层，剪贴蒙版作用于 base 的
            #     bbox 矩形区域（即 base 是"窗口"），clipped 在矩形内全部可见
            # 实测：抽奖活动 PSD "切换按钮" 内 "组 86" 是 PASS_THROUGH，PS composite 时
            # "图层 660" 黄色辉光铺满整个按钮区域；若按 alpha 剪会得到"文字形状的黄图"，
            # 与文本图层重叠出现"两个勇士特供"。
            base_blend_mode = str(getattr(base_group, 'blend_mode', '')) or ''
            base_is_pass_through = 'pass' in base_blend_mode.lower()

            if not base_is_pass_through:
                # 普通组：按 base alpha 剪
                base_arr = np.array(base_alpha_img, dtype=np.float32) / 255.0
                base_patch_alpha = base_arr[
                    inter_top - base_top: inter_top - base_top + ih,
                    inter_left - base_left: inter_left - base_left + iw,
                    3:4,
                ]
                cl_patch[:, :, 3:4] = cl_patch[:, :, 3:4] * base_patch_alpha
            # PASS_THROUGH 组：保留 cl 在 base bbox 矩形内的原始 alpha（不再乘 base alpha）
            # 这样 cl 的形状保持自身像素形状，仅被裁到 base 矩形区域内

            # 检查是否完全透明
            if cl_patch[:, :, 3].max() <= 0.0:
                print(f"{'  ' * depth}🚫 {layer_name} (剪贴后完全透明)")
                self.skipped_count += 1
                return None

            # ── 5. 裁剪透明边缘以缩小 PNG ──
            alpha_2d = cl_patch[:, :, 3]
            ys = np.where(alpha_2d > 0)[0]
            xs = np.where(alpha_2d > 0)[1]
            if ys.size == 0 or xs.size == 0:
                print(f"{'  ' * depth}🚫 {layer_name} (剪贴后空)")
                self.skipped_count += 1
                return None
            y0, y1 = int(ys.min()), int(ys.max()) + 1
            x0, x1 = int(xs.min()), int(xs.max()) + 1
            trimmed = cl_patch[y0:y1, x0:x1]

            # ── 6. 转回 PIL 并保存 ──
            trimmed_uint8 = np.clip(trimmed * 255.0, 0, 255).astype(np.uint8)
            out_img = Image.fromarray(trimmed_uint8, mode='RGBA')

            rel_path = self._save_image_dedup(out_img, layer_name, depth)

            # 最终图片左上角的画布绝对坐标
            abs_left = inter_left + x0
            abs_top = inter_top + y0
            width = trimmed_uint8.shape[1]
            height = trimmed_uint8.shape[0]

            rel_left = abs_left - parent_left
            rel_top = abs_top - parent_top

            print(
                f"{'  ' * depth}✂️  {layer_name} "
                f"(剪贴 {base_group.name}"
                f"{'(pass-thru/矩形)' if base_is_pass_through else '/alpha'} → "
                f"[{width}x{height}]) → {rel_path}"
            )

            layer_info: dict[str, Any] = {
                'id': f'layer-{self._z_counter}',
                'name': layer_name,
                'full_name': full_name,
                'left': rel_left,
                'top': rel_top,
                'width': width,
                'height': height,
                'opacity': cl.opacity / 255.0,
                'blend_mode': BLEND_MODES.get(str(cl.blend_mode), 'normal'),
                'z_index': self._z_counter,
                'type': 'image',
                'image_path': rel_path,
            }
            self.exported_count += 1
            return layer_info

        except Exception as e:
            print(f"{'  ' * depth}❌ {layer_name} 剪贴导出失败: {e}")
            import traceback
            traceback.print_exc()
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

    # ------------------------------------------------------------------
    # PSD 像素语义还原：基于 compose_cluster 的合图（含 merge_full /
    #   merge_with_text_kept / merge_partial 三条路径）。
    # 触发条件由 compose_cluster.decide_group_merge() 给出，
    # 不再使用启发式（按钮关键词 / 文本数量等）。
    # ------------------------------------------------------------------

    def _calc_group_expand(self, group_layer: Any) -> int:
        """计算组内所有图层效果溢出所需的最大扩展像素数。"""
        import math
        max_expand = 0

        def calc_layer_expand(layer) -> int:
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
                    if 'OutF' in style_str or 'Outset' in style_str:
                        expand = max(expand, size + 2)
                    elif 'CtrF' in style_str or 'Center' in style_str:
                        expand = max(expand, size // 2 + 2)
                elif name == 'OuterGlow':
                    blur = float(desc.get(b'blur', 0))
                    spread = float(desc.get(b'Ckmt', 0))
                    radius = blur * (1.0 + spread / 100.0) * 1.5
                    expand = max(expand, int(math.ceil(radius)) + 2)
                elif name == 'DropShadow':
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
                for child in layer:
                    check_layer(child)
            else:
                max_expand = max(max_expand, calc_layer_expand(layer))

        for child in group_layer:
            check_layer(child)
        return max_expand

    def _render_group_with_hybrid_strategy(
        self, group_layer: Any, grp_bbox: tuple,
        expand: int, depth: int,
    ):
        """混合渲染策略：手动逐层渲染（保留溢出效果）。

        子组优先用 composite() 渲染；普通图层用 render_layer_with_effects。
        见 memory 32006918：psd-tools composite() 不会输出超出 ancestor.bbox
        的效果像素，故溢出区域只能靠手动渲染。
        """
        grp_w = grp_bbox[2] - grp_bbox[0]
        grp_h = grp_bbox[3] - grp_bbox[1]
        ext_w = grp_w + 2 * expand
        ext_h = grp_h + 2 * expand
        canvas = np.zeros((ext_h, ext_w, 4), dtype=np.float32)

        def render_subgroup(sub_grp, depth_offset=0):
            sub_bbox = sub_grp.bbox
            sub_w = sub_bbox[2] - sub_bbox[0]
            sub_h = sub_bbox[3] - sub_bbox[1]
            if sub_w <= 0 or sub_h <= 0:
                return
            try:
                sub_img = sub_grp.composite(viewport=sub_bbox)
                if sub_img and sub_img.mode == 'RGBA':
                    sub_x = sub_bbox[0] - grp_bbox[0] + expand
                    sub_y = sub_bbox[1] - grp_bbox[1] + expand
                    sub_arr = ImageArrayUtils.pil_to_float_array(sub_img)
                    y0, y1 = sub_y, sub_y + sub_h
                    x0, x1 = sub_x, sub_x + sub_w
                    if 0 <= y0 < ext_h and 0 <= x0 < ext_w:
                        y1 = min(y1, ext_h)
                        x1 = min(x1, ext_w)
                        h_copy = y1 - y0
                        w_copy = x1 - x0
                        if h_copy > 0 and w_copy > 0:
                            canvas[y0:y1, x0:x1] = _alpha_composite_numpy(
                                canvas[y0:y1, x0:x1],
                                sub_arr[:h_copy, :w_copy],
                            )
                    return
            except Exception as e:
                print(f"{'  ' * (depth + depth_offset)}  ⚠️  子组 composite 失败: {e}")
            # 降级：递归渲染子图层
            for child in sub_grp:
                render_layer(child, depth_offset + 1)

        def render_layer(layer, depth_offset=0):
            if not layer.visible or layer.opacity == 0:
                return
            if layer.is_group():
                render_subgroup(layer, depth_offset)
                return
            result = render_layer_with_effects(layer)
            if result is None:
                return
            img, eff_bbox = result
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            layer_x = eff_bbox[0] - grp_bbox[0] + expand
            layer_y = eff_bbox[1] - grp_bbox[1] + expand
            layer_w = eff_bbox[2] - eff_bbox[0]
            layer_h = eff_bbox[3] - eff_bbox[1]
            layer_arr = ImageArrayUtils.pil_to_float_array(img)
            layer_arr[:, :, 3] *= (layer.opacity / 255.0)
            y0, y1 = layer_y, layer_y + layer_h
            x0, x1 = layer_x, layer_x + layer_w
            if 0 <= y0 < ext_h and 0 <= x0 < ext_w:
                y1 = min(y1, ext_h)
                x1 = min(x1, ext_w)
                h_copy = y1 - y0
                w_copy = x1 - x0
                if h_copy > 0 and w_copy > 0:
                    canvas[y0:y1, x0:x1] = _alpha_composite_numpy(
                        canvas[y0:y1, x0:x1],
                        layer_arr[:h_copy, :w_copy],
                    )

        for child in group_layer:
            render_layer(child)

        return ImageArrayUtils.float_array_to_pil(canvas)

    def _merge_group_as_single_image(
        self, group_layer: Any, group_name: str, full_name: str,
        depth: int, parent_left: int, parent_top: int,
    ) -> dict | None:
        """整组合成单 PNG（merge_full 路径核心）。

        策略：
        - 有效果溢出 → 手动逐层渲染（hybrid）
        - 无溢出 → 直接 group.composite(viewport=grp_bbox)

        见 memory 89140607（混合渲染）/ 96847396（子组用 composite）。
        """
        try:
            grp_bbox = group_layer.bbox
            grp_w = grp_bbox[2] - grp_bbox[0]
            grp_h = grp_bbox[3] - grp_bbox[1]
            if grp_w <= 0 or grp_h <= 0:
                return None

            child_names = [c.name for c in group_layer if c.visible and c.opacity > 0]
            print(f"{'  ' * depth}🔗 合并组图层(PSD 合成簇): {group_name} "
                  f"({len(child_names)}层: {child_names})")

            expand = self._calc_group_expand(group_layer)
            if expand > 0:
                print(f"{'  ' * depth}  🌟 检测到效果溢出 {expand}px，使用混合渲染")
                # ── 双路径：hybrid（保留溢出效果） + 内部 composite 覆盖 ──
                # hybrid 手动逐层 alpha_composite，无法处理剪贴蒙版关系
                # （clip=True 图层独立 render_layer_with_effects 出来常常是
                # 全透明），导致内部内容丢失（典型：SPECIAL 椭圆装饰带消失）。
                # 解决：用 group.composite(viewport=grp_bbox) 拿 PSD 原生
                # 渲染（剪贴蒙版关系正确），覆盖 hybrid 输出的内部区域；
                # 外圈 expand 像素带保留 hybrid 渲染的发光/描边溢出。
                # 见 memory 32006918 / 89140607。
                composite_img = self._render_group_with_hybrid_strategy(
                    group_layer, grp_bbox, expand, depth,
                )
                if composite_img is not None:
                    try:
                        inner_img = group_layer.composite(viewport=grp_bbox)
                        if inner_img is not None:
                            if inner_img.mode != 'RGBA':
                                inner_img = inner_img.convert('RGBA')
                            if composite_img.mode != 'RGBA':
                                composite_img = composite_img.convert('RGBA')
                            # 先清空内部区域（覆盖式而非合成式），
                            # 避免 hybrid 错渲染的像素透出 inner 透明位置
                            grp_w_inner = grp_bbox[2] - grp_bbox[0]
                            grp_h_inner = grp_bbox[3] - grp_bbox[1]
                            transparent = Image.new(
                                'RGBA', (grp_w_inner, grp_h_inner), (0, 0, 0, 0)
                            )
                            composite_img.paste(transparent, (expand, expand))
                            composite_img.paste(inner_img, (expand, expand))
                    except Exception as e:
                        print(f"{'  ' * depth}  ⚠️  内部 composite 覆盖失败: {e}")
            else:
                composite_img = group_layer.composite(viewport=grp_bbox)

            if composite_img is None:
                print(f"{'  ' * depth}  ⚠️  composite() 返回 None")
                return None
            if composite_img.mode != 'RGBA':
                composite_img = composite_img.convert('RGBA')

            # 扩展画布的实际左上 = grp_bbox 左上 - expand
            orig_abs_left = grp_bbox[0] - expand
            orig_abs_top = grp_bbox[1] - expand
            actual_w = grp_w + 2 * expand
            actual_h = grp_h + 2 * expand
            adj_left = 0
            adj_top = 0

            # 仅裁剪到画布边界
            left = grp_bbox[0] - expand
            top = grp_bbox[1] - expand
            right = grp_bbox[2] + expand
            bottom = grp_bbox[3] + expand
            cl = max(0, left)
            ct = max(0, top)
            cr = min(self.psd.width, right)
            cb = min(self.psd.height, bottom)

            if cr > cl and cb > ct:
                crop_left = cl - left
                crop_top = ct - top
                crop_right = composite_img.size[0] - (right - cr)
                crop_bottom = composite_img.size[1] - (bottom - cb)
                if crop_right > crop_left and crop_bottom > crop_top:
                    composite_img = composite_img.crop(
                        (crop_left, crop_top, crop_right, crop_bottom)
                    )
                    adj_left = cl - left
                    adj_top = ct - top
                    actual_w = cr - cl
                    actual_h = cb - ct
                else:
                    print(f"{'  ' * depth}🚫 {group_name} (裁剪后为空)")
                    return None
            else:
                print(f"{'  ' * depth}🚫 {group_name} (完全在画布外)")
                return None

            img_arr = np.array(composite_img)
            if img_arr[:, :, 3].max() == 0:
                print(f"{'  ' * depth}🚫 {group_name} (合并后完全透明)")
                return None

            self._z_counter += 1
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

            rel_path = self._save_image_dedup(composite_img, group_name, depth)
            layer_info['image_path'] = rel_path

            visible_count = len(child_names)
            total_count = len(list(group_layer))
            print(f"{'  ' * depth}🖼️  {group_name} "
                  f"[合并{visible_count}/{total_count}层 {actual_w}x{actual_h}] → {rel_path}")
            self.exported_count += total_count
            return layer_info

        except Exception as e:
            print(f"{'  ' * depth}❌ 合并组 {group_name} 失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    # ------------------------------------------------------------------
    # merge_with_text_kept / merge_partial 路径所需的辅助合成函数
    # ------------------------------------------------------------------

    def _collect_recursive_text_layers(self, group_layer: Any) -> list[Any]:
        """递归收集组内所有可见文本图层（type 图层）。"""
        out: list[Any] = []

        def walk(node: Any) -> None:
            if not getattr(node, 'visible', True) or getattr(node, 'opacity', 255) == 0:
                return
            if node.is_group():
                for c in node:
                    walk(c)
                return
            kind = str(getattr(node, 'kind', '') or '').lower()
            if 'type' in kind:
                out.append(node)

        for c in group_layer:
            walk(c)
        return out

    def _merge_group_non_text_as_image(
        self, group_layer: Any, group_name: str, full_name: str,
        depth: int, parent_left: int, parent_top: int,
    ) -> dict | None:
        """合并组内"非文本"图层为单张背景图（merge_with_text_kept 路径）。

        实现方式：临时把组内所有递归可见文本图层的 visible 置为 False，
        调用 _merge_group_as_single_image 复用合成流水线，然后恢复 visible。
        文本图层稍后在 GroupHandler 里通过普通递归路径独立导出。
        """
        text_layers = self._collect_recursive_text_layers(group_layer)
        if not text_layers:
            # 没文本，直接走整组合成
            return self._merge_group_as_single_image(
                group_layer, group_name, full_name,
                depth, parent_left, parent_top,
            )

        print(f"{'  ' * depth}🎯 {group_name} "
              f"(非文本合并为背景图，文本独立: {len(text_layers)} 个文本)")

        saved: list[tuple[Any, bool]] = []
        try:
            for t in text_layers:
                saved.append((t, t.visible))
                try:
                    t.visible = False
                except Exception:
                    pass
            return self._merge_group_as_single_image(
                group_layer, group_name, full_name,
                depth, parent_left, parent_top,
            )
        finally:
            for t, vis in saved:
                try:
                    t.visible = vis
                except Exception:
                    pass

    def _merge_cluster_layers_as_image(
        self, group_layer: Any, cluster_members: list[Any],
        group_name: str, full_name: str,
        depth: int, parent_left: int, parent_top: int,
        suffix: str = '',
    ) -> dict | None:
        """把组内"指定 sibling 子集"合成为单张图（merge_partial 路径）。

        策略：临时隐藏组内不属于 cluster_members 的所有 sibling，
        然后调用 _merge_group_as_single_image 走标准合成流程；
        合成结果的 bbox = 这些 sibling 的 union（用 group.composite()
        + 合成完后裁剪）。最后恢复 visible 状态。

        ⚠️ 调用方必须为**每个**glued cluster 单独调用一次此函数；
        不要把多个 cluster 的成员合到一个 list 传入——这会跨越独立子项
        导致 z 序错乱。
        """
        # cluster 成员去重（按 id）
        member_ids = {id(m) for m in cluster_members}

        # 先记 group_layer 自身 bbox（用 force_bbox 是没有的，所以这里靠
        # composite() 自动按 visible 子的 union 出图——隐藏掉非 cluster
        # sibling 后，psd-tools 的 group.composite() 给出的 bbox 会自动
        # 收缩到 cluster 的视觉 union，但效果溢出会被裁。所以我们仍走
        # _merge_group_as_single_image 的统一逻辑：用整组 bbox + expand）。

        # 隐藏所有不在 cluster 内的 sibling（直接子）
        saved: list[tuple[Any, bool]] = []
        for c in group_layer:
            if id(c) in member_ids:
                continue
            if not c.visible:
                continue
            saved.append((c, c.visible))
            try:
                c.visible = False
            except Exception:
                pass

        try:
            mname = f"{group_name}{suffix}" if suffix else group_name
            mfull = f"{full_name}{suffix}" if suffix else full_name
            print(f"{'  ' * depth}🧬 合并 cluster: {mname} "
                  f"({len(cluster_members)}层: {[m.name for m in cluster_members]})")
            return self._merge_group_as_single_image(
                group_layer, mname, mfull,
                depth, parent_left, parent_top,
            )
        finally:
            for c, vis in saved:
                try:
                    c.visible = vis
                except Exception:
                    pass
