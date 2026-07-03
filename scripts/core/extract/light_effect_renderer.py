#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
光效层穿透渲染系统

负责识别、验证和应用 PSD 中的光效层（如 COLOR_DODGE、SCREEN 等混合模式的图层），
使其能够穿透容器限制，正确合成到下层元素。

详见 doc/03-topics/light-blend-penetrate.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from PIL import Image
import numpy as np

# 光照类 blend mode 集合——这些模式下黑色像素是恒等色（与底色混合后
# 相当于透明/不贡献），topil() 导出时黑色会"暴露"成假的黑色区块。
# 只有命中此集合 + 图层原始像素含显著黑色区域，才值得走穿透渲染。
_LIGHT_BLEND_MODES: frozenset[str] = frozenset({
    'COLOR_DODGE',
    'LINEAR_DODGE',   # PS add → CSS screen（黑色=恒等）
    'SCREEN',         # 黑色=恒等
    'LIGHTEN',        # 黑色=恒等（取 max）
    'LIGHTER_COLOR',  # 黑色=恒等（取亮色）
})

#: Phase 2 判定 alpha 不透明阈值（避免抗锯齿边缘误判）
_ALPHA_OPAQUE_THRESHOLD = 10  # 0-255 量级

#: 目标亮度阈值：光效合成到亮度 >= 此值的区域时，效果近似无效
#: COLOR_DODGE(white, fg) ≈ white, SCREEN(white, fg) ≈ white
_LIGHT_TARGET_BRIGHTNESS_THRESHOLD = 0.92


@dataclass
class LightEffectLayerInfo:
    """一个被标记为需要穿透渲染的光效图层。"""

    layer: Any                                # 光效图层对象
    bbox: tuple[int, int, int, int]           # (left, top, right, bottom)
    parent_pt_group: Any                      # 所在的 PASS_THROUGH 父组
    needs_penetrate: bool = False             # Phase 2 判定结果
    fallback_css_blend: bool = False          # Phase 3 判定：所有目标无效时降级为 CSS blend


class LightEffectRenderer:
    """光效层穿透渲染引擎
    
    负责 Phase 1-5 的完整处理：
    - Phase 1: 候选识别
    - Phase 2: 穿透判定  
    - Phase 3: 目标匹配
    - Phase 4: 像素合成（由调用方完成）
    - Phase 5: 抑制/降级管理
    """

    def __init__(self, psd: Any, canvas_width: int, canvas_height: int):
        """初始化光效渲染器。
        
        Args:
            psd: PSD 文档对象
            canvas_width: 画布宽度
            canvas_height: 画布高度
        """
        self.psd = psd
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height

        # 光效层穿透映射：target 图层 id → 需要叠加的光效层列表
        self._penetrate_map: dict[int, list[LightEffectLayerInfo]] = {}

        # Phase 3 临时图像缓存：避免对同一图层重复调用 composite()/topil()
        # key = id(layer), value = RGBA PIL Image (或 None 表示获取失败)
        self._phase3_img_cache: dict[int, Any] = {}

        # Phase 5: 需要抑制独立导出的光效层 id 集合
        # 当光效层 needs_penetrate=True 且已成功映射到目标层时，
        # 其 Phase 4 像素合成已在目标层 PNG 上完成，自身不应再独立导出
        self._suppressed_light_layers: set[int] = set()

        # Phase 5 补充：降级为 CSS blend 的光效层 id 集合
        # needs_penetrate=True 但所有穿透目标无效（纯白/高亮度）
        self._fallback_light_layers: set[int] = set()

    def pre_scan(self) -> dict[int, list[LightEffectLayerInfo]]:
        """预扫描 PSD，识别需要穿透渲染的光效层并构建 penetrate_map。

        Phase 1: 遍历所有图层，找出父组是 PASS_THROUGH 模式、且 blend_mode
                 属于 _LIGHT_BLEND_MODES 的可见光效层（候选）。
        Phase 2: 对每个候选，检查其在 PT 组内下方是否有不透明覆盖；
                 若无 → 标记 needs_penetrate=True。
        Phase 3: 对 needs_penetrate 的光效层，在 PT 组的外部向下查找
                 重叠的不透明目标图层，构建 penetrate_map。
        
        Returns:
            penetrate_map 字典
        """
        candidates: list[LightEffectLayerInfo] = []

        # ── Phase 1: 识别候选光效层 ──
        def _scan_recursive(parent: Any) -> None:
            try:
                children = list(parent)
            except Exception:
                return
            is_parent_pt = (
                hasattr(parent, 'is_group')
                and parent.is_group()
                and 'PASS_THROUGH' in str(getattr(parent, 'blend_mode', '')).upper()
            )
            for layer in children:
                if hasattr(layer, 'is_group') and layer.is_group():
                    _scan_recursive(layer)
                    continue
                if not is_parent_pt:
                    continue
                if not layer.visible or layer.opacity == 0:
                    continue
                bm = str(getattr(layer, 'blend_mode', '')).upper()
                # 从 "BlendMode.SCREEN" 提取 "SCREEN"
                bm_short = bm.split('.')[-1] if '.' in bm else bm
                if bm_short not in _LIGHT_BLEND_MODES:
                    continue
                bbox = layer.bbox
                if bbox[2] - bbox[0] <= 0 or bbox[3] - bbox[1] <= 0:
                    continue
                candidates.append(LightEffectLayerInfo(
                    layer=layer,
                    bbox=tuple(bbox),
                    parent_pt_group=parent,
                ))

        _scan_recursive(self.psd)

        if not candidates:
            return self._penetrate_map

        # ── Phase 2: 组内检查 → 标记 needs_penetrate ──
        for info in candidates:
            info.needs_penetrate = self._check_needs_penetrate(info)

        penetrate_candidates = [c for c in candidates if c.needs_penetrate]
        if not penetrate_candidates:
            return self._penetrate_map

        print(f"🔦 光效层穿透扫描: {len(candidates)} 个候选, "
              f"{len(penetrate_candidates)} 个需要穿透")
        for c in penetrate_candidates:
            print(f"   💡 {c.layer.name} (blend={str(c.layer.blend_mode)}, "
                  f"bbox={c.bbox})")

        # ── Phase 3: 向外匹配 → 构建 penetrate_map ──
        for info in penetrate_candidates:
            self._find_penetrate_targets(info)

        if self._penetrate_map:
            print(f"   📋 穿透映射: {len(self._penetrate_map)} 个目标图层"
                  f"需叠加光效")

        # ── Phase 5: 构建抑制集合 ──
        effectively_mapped: set[int] = set()
        for light_list in self._penetrate_map.values():
            for li in light_list:
                effectively_mapped.add(id(li.layer))

        for li_id in effectively_mapped:
            self._suppressed_light_layers.add(li_id)

        if self._suppressed_light_layers:
            print(f"   🚫 抑制独立导出: {len(self._suppressed_light_layers)} "
                  f"个光效层（已合成到目标层）")

        # ── Phase 5 补充：标记降级为 CSS blend 的光效层 ──
        self._fallback_light_layers: set[int] = set()
        for info in penetrate_candidates:
            if id(info.layer) not in effectively_mapped:
                info.fallback_css_blend = True
                self._fallback_light_layers.add(id(info.layer))
                print(f"   🔄 {info.layer.name} → 降级为 CSS blend "
                      f"(所有穿透目标无效)")

        # 释放 Phase 3 临时图像缓存（可能占用大量内存）
        self._phase3_img_cache.clear()

        return self._penetrate_map

    # 覆盖率阈值：组内 sibling 覆盖光效层有效区域达到此比例时认为不需穿透
    _COVERAGE_THRESHOLD = 0.90

    def _check_needs_penetrate(self, info: LightEffectLayerInfo) -> bool:
        """Phase 2: 检查光效层在其 PT 组内下方是否被充分覆盖。

        判定逻辑：
        1. clipping layer → 直接不穿透（base layer 天然提供底色）
        2. 计算有效作用区域（layer_bbox ∩ mask_bbox ∩ clip_base_bbox）
        3. 用有效区域与组内下方 sibling 做覆盖率计算
        4. 覆盖率 >= _COVERAGE_THRESHOLD → 不穿透；否则 → 需要穿透
        """
        group = info.parent_pt_group
        light_layer = info.layer
        light_bbox = info.bbox

        # ── 快速路径：clipping layer 不需要穿透 ──
        if getattr(light_layer, 'clipping', False):
            return False

        try:
            siblings = list(group)
        except Exception:
            return True  # 保守：读不到 sibling → 需要穿透

        # 找到 light_layer 在 sibling 中的 index
        light_idx = -1
        for i, sib in enumerate(siblings):
            if sib is light_layer:
                light_idx = i
                break
        if light_idx <= 0:
            return True  # 在最底层，下方无图层 → 需要穿透

        # ── 计算有效作用区域 ──
        effective_bbox = list(light_bbox)

        # layer mask 限制
        mask = getattr(light_layer, 'mask', None)
        if mask is not None:
            mask_bbox = getattr(mask, 'bbox', None)
            if mask_bbox and len(mask_bbox) == 4:
                effective_bbox[0] = max(effective_bbox[0], mask_bbox[0])
                effective_bbox[1] = max(effective_bbox[1], mask_bbox[1])
                effective_bbox[2] = min(effective_bbox[2], mask_bbox[2])
                effective_bbox[3] = min(effective_bbox[3], mask_bbox[3])

        # vector mask 限制（如果有）
        vector_mask = getattr(light_layer, 'vector_mask', None)
        if vector_mask is not None:
            vm_bbox = getattr(vector_mask, 'bbox', None)
            if vm_bbox and len(vm_bbox) == 4:
                effective_bbox[0] = max(effective_bbox[0], vm_bbox[0])
                effective_bbox[1] = max(effective_bbox[1], vm_bbox[1])
                effective_bbox[2] = min(effective_bbox[2], vm_bbox[2])
                effective_bbox[3] = min(effective_bbox[3], vm_bbox[3])

        # 有效区域退化为空 → 不产生可见像素，不需穿透
        if effective_bbox[2] <= effective_bbox[0] or effective_bbox[3] <= effective_bbox[1]:
            return False

        effective_bbox_t = tuple(effective_bbox)
        effective_area = (
            (effective_bbox_t[2] - effective_bbox_t[0])
            * (effective_bbox_t[3] - effective_bbox_t[1])
        )

        # ── 计算组内下方 sibling 对有效区域的覆盖率 ──
        total_covered_area = 0
        for i in range(light_idx - 1, -1, -1):
            sib = siblings[i]
            if not sib.visible or sib.opacity == 0:
                continue
            # 跳过 clipping layer（它受 base 限制，不作为独立底色）
            if getattr(sib, 'clipping', False):
                continue
            sib_bbox = sib.bbox
            inter = self._intersect_bbox(effective_bbox_t, sib_bbox)
            if inter is None:
                continue
            inter_area = (inter[2] - inter[0]) * (inter[3] - inter[1])
            total_covered_area += inter_area

        coverage = total_covered_area / max(1, effective_area)
        return coverage < self._COVERAGE_THRESHOLD

    def _find_penetrate_targets(self, info: LightEffectLayerInfo) -> None:
        """Phase 3: 在外组下方查找与光效层重叠的目标图层并记录到 penetrate_map。

        递归向上穿透：如果外层父组本身也是 PASS_THROUGH，继续向上查找。
        """
        group = info.parent_pt_group
        light_bbox = info.bbox
        self._find_targets_in_outer_scope(info, group, light_bbox)

    def _find_targets_in_outer_scope(
        self,
        info: LightEffectLayerInfo,
        pt_group: Any,
        light_bbox: tuple[int, int, int, int],
    ) -> None:
        """在 pt_group 的父级中查找下方目标图层。"""
        outer_parent = getattr(pt_group, 'parent', None) or getattr(pt_group, '_parent', None)
        if outer_parent is None:
            outer_parent = self.psd
        if outer_parent is None:
            return

        try:
            outer_children = list(outer_parent)
        except Exception:
            return

        # 找到 pt_group 在外层的 index
        grp_idx = -1
        for i, child in enumerate(outer_children):
            if child is pt_group:
                grp_idx = i
                break
        if grp_idx < 0:
            return

        # 在 pt_group 之下查找目标图层
        for i in range(grp_idx - 1, -1, -1):
            sib = outer_children[i]
            if not sib.visible or sib.opacity == 0:
                continue
            # 跳过调整层（无像素内容）
            kind = str(getattr(sib, 'kind', '') or '').lower()
            if 'adjust' in kind:
                continue

            sib_bbox = sib.bbox
            inter = self._intersect_bbox(light_bbox, sib_bbox)
            if inter is None:
                continue

            # 检查交集区域内是否有不透明内容
            if not self._has_opaque_in_region(sib, sib_bbox, inter):
                continue

            # ── 新增：检查目标是否为"有效合成目标" ──
            if not self._is_effective_light_target(sib, sib_bbox, inter, info):
                sib_name = getattr(sib, 'name', '?')
                light_name = getattr(info.layer, 'name', '?')
                print(f"   ⚡ 跳过无效目标 '{sib_name}' "
                      f"(亮度过高, 光效 '{light_name}' 合成无效)")
                continue

            # 记录到 penetrate_map
            target_id = id(sib)
            if target_id not in self._penetrate_map:
                self._penetrate_map[target_id] = []
            # 避免重复添加同一光效层
            if not any(li.layer is info.layer for li in self._penetrate_map[target_id]):
                self._penetrate_map[target_id].append(info)

        # 如果 outer_parent 本身也是 PASS_THROUGH 组，继续向上穿透
        if (hasattr(outer_parent, 'is_group')
                and outer_parent.is_group()
                and 'PASS_THROUGH' in str(getattr(outer_parent, 'blend_mode', '')).upper()):
            self._find_targets_in_outer_scope(info, outer_parent, light_bbox)

    @staticmethod
    def _intersect_bbox(
        a: tuple[int, int, int, int],
        b: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int] | None:
        """计算两个 bbox 的交集。返回 None 如果无交集。"""
        left = max(a[0], b[0])
        top = max(a[1], b[1])
        right = min(a[2], b[2])
        bottom = min(a[3], b[3])
        if right <= left or bottom <= top:
            return None
        return (left, top, right, bottom)

    def _has_opaque_in_region(
        self,
        layer: Any,
        layer_bbox: tuple[int, int, int, int],
        region: tuple[int, int, int, int],
    ) -> bool:
        """检查图层在指定区域内是否有显著不透明像素。

        对组用 composite()，对叶图层用 topil()。
        使用 stride=4 采样以提高性能。
        结果图像会缓存到 _phase3_img_cache 中避免重复合成。
        """
        try:
            layer_id = id(layer)
            if layer_id in self._phase3_img_cache:
                img = self._phase3_img_cache[layer_id]
            else:
                if hasattr(layer, 'is_group') and layer.is_group():
                    img = layer.composite()
                else:
                    img = layer.topil()
                    if img is None and hasattr(layer, 'composite'):
                        img = layer.composite()
                if img is not None and img.mode != 'RGBA':
                    img = img.convert('RGBA')
                self._phase3_img_cache[layer_id] = img
            if img is None:
                return False

            # 从 img 中提取 region 对应的区域
            rx0 = region[0] - layer_bbox[0]
            ry0 = region[1] - layer_bbox[1]
            rx1 = region[2] - layer_bbox[0]
            ry1 = region[3] - layer_bbox[1]

            # 边界裁剪
            rx0 = max(0, rx0)
            ry0 = max(0, ry0)
            rx1 = min(img.size[0], rx1)
            ry1 = min(img.size[1], ry1)

            if rx1 <= rx0 or ry1 <= ry0:
                return False

            # stride=4 采样检查 alpha
            arr = np.array(img)
            patch_alpha = arr[ry0:ry1:4, rx0:rx1:4, 3]
            return int(patch_alpha.max()) > _ALPHA_OPAQUE_THRESHOLD

        except Exception:
            return False

    def _is_effective_light_target(
        self,
        sib: Any,
        sib_bbox: tuple[int, int, int, int],
        inter: tuple[int, int, int, int],
        info: LightEffectLayerInfo,
    ) -> bool:
        """检查目标层在交集区域的颜色是否能让光效产生有意义的视觉效果。

        光效 blend mode 的数学特性：
        - COLOR_DODGE(white, fg) = white（无效）
        - SCREEN(white, fg) = white（无效）
        - LINEAR_DODGE(white, fg) = clamp(white+fg) = white（无效）
        - LIGHTEN(white, fg) = white（无效）

        如果目标层在交集区域内的不透明像素平均亮度 >= 阈值，
        认为光效合成到此目标上不会产生有意义的视觉变化。

        Returns:
            True 如果目标有效（颜色足够深，光效能产生效果），
            False 如果目标无效（颜色过亮，光效合成等于无效操作）。
        """
        try:
            layer_id = id(sib)
            if layer_id in self._phase3_img_cache:
                img = self._phase3_img_cache[layer_id]
            else:
                if hasattr(sib, 'is_group') and sib.is_group():
                    img = sib.composite()
                else:
                    img = sib.topil()
                    if img is None and hasattr(sib, 'composite'):
                        img = sib.composite()
                if img is not None and img.mode != 'RGBA':
                    img = img.convert('RGBA')
                self._phase3_img_cache[layer_id] = img

            if img is None:
                return True  # 保守：获取失败时认为有效

            # 从 img 中提取交集区域
            rx0 = inter[0] - sib_bbox[0]
            ry0 = inter[1] - sib_bbox[1]
            rx1 = inter[2] - sib_bbox[0]
            ry1 = inter[3] - sib_bbox[1]

            rx0 = max(0, rx0)
            ry0 = max(0, ry0)
            rx1 = min(img.size[0], rx1)
            ry1 = min(img.size[1], ry1)

            if rx1 <= rx0 or ry1 <= ry0:
                return True  # 无区域可检查，保守认为有效

            # stride=4 采样，计算不透明像素的平均亮度
            arr = np.array(img, dtype=np.float32)
            patch = arr[ry0:ry1:4, rx0:rx1:4]  # (H, W, 4)

            # 只看不透明像素（alpha > threshold）
            alpha = patch[:, :, 3]
            opaque_mask = alpha > _ALPHA_OPAQUE_THRESHOLD

            if not opaque_mask.any():
                return True  # 无不透明像素，保守认为有效

            # 计算不透明像素的亮度 (ITU-R BT.601)
            rgb = patch[:, :, :3] / 255.0
            luminance = (0.299 * rgb[:, :, 0]
                         + 0.587 * rgb[:, :, 1]
                         + 0.114 * rgb[:, :, 2])

            avg_lum = float(luminance[opaque_mask].mean())

            # 如果平均亮度 >= 阈值，认为光效合成无效
            return avg_lum < _LIGHT_TARGET_BRIGHTNESS_THRESHOLD

        except Exception:
            return True  # 异常时保守认为有效

    @staticmethod
    def _remove_identity_color(img: Image.Image) -> Image.Image:
        """去除光效层中黑色恒等色区域的不透明度（渐进衰减）。

        光效 blend mode（COLOR_DODGE/SCREEN/LINEAR_DODGE 等）中，黑色是恒等色
        （与底色混合结果 = 底色）。当光效层降级为独立 DOM 元素 + CSS blend 时，
        PNG 中的黑色像素如果保持 alpha=255 会暴露为真实黑底。

        解决方案：用像素亮度作为 alpha 的调制因子——
        - 纯黑 (lum=0) → alpha 衰减到 0（完全透明）
        - 亮色 (lum>=threshold) → alpha 保持不变
        - 中间值 → 线性过渡，避免硬边缘

        这样黑色区域变透明，有发光效果的明亮区域保留，配合 CSS mix-blend-mode
        能正确渲染，且不会产生黑底。
        """
        arr = np.array(img, dtype=np.float32)
        rgb = arr[:, :, :3] / 255.0

        # ITU-R BT.601 亮度
        luminance = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]

        # 衰减曲线：lum < low_threshold 完全透明，> high_threshold 完全保留
        # 中间线性过渡
        low_thresh = 0.05   # 纯黑到深灰 → 透明
        high_thresh = 0.25  # 中灰以上 → 保留

        # 计算调制因子 [0, 1]
        fade_factor = np.clip(
            (luminance - low_thresh) / max(high_thresh - low_thresh, 1e-6),
            0.0, 1.0
        )

        # 对原始 alpha 应用衰减
        arr[:, :, 3] *= fade_factor

        return Image.fromarray(arr.astype(np.uint8))

    @staticmethod
    def _blend_light_layer(
        base_arr: np.ndarray,
        light_arr: np.ndarray,
        blend_mode_str: str,
        opacity: float,
    ) -> np.ndarray:
        """在 base_arr 上按指定光效 blend mode 合成 light_arr。

        base_arr, light_arr: (H, W, 4) float32, 值域 [0, 1]
        blend_mode_str: psd-tools blend mode 字符串（如 "BlendMode.SCREEN"）
        opacity: 光效层不透明度 [0, 1]
        """
        bm = blend_mode_str.upper()
        fg_rgb = light_arr[:, :, :3]
        fg_a = light_arr[:, :, 3:4] * opacity
        bg_rgb = base_arr[:, :, :3]
        bg_a = base_arr[:, :, 3:4]

        # 按 blend mode 计算混合后的 RGB
        if 'COLOR_DODGE' in bm:
            # color dodge: bg / (1 - fg), clamp
            denom = np.maximum(1.0 - fg_rgb, 1e-10)
            blended = np.clip(bg_rgb / denom, 0, 1)
        elif 'LINEAR_DODGE' in bm:
            # linear dodge (add): bg + fg, clamp
            blended = np.clip(bg_rgb + fg_rgb, 0, 1)
        elif 'SCREEN' in bm:
            # screen: 1 - (1 - bg) * (1 - fg)
            blended = 1.0 - (1.0 - bg_rgb) * (1.0 - fg_rgb)
        elif 'LIGHTER_COLOR' in bm:
            # lighter color: 取整体亮度更高的颜色
            bg_lum = 0.299 * bg_rgb[:, :, 0:1] + 0.587 * bg_rgb[:, :, 1:2] + 0.114 * bg_rgb[:, :, 2:3]
            fg_lum = 0.299 * fg_rgb[:, :, 0:1] + 0.587 * fg_rgb[:, :, 1:2] + 0.114 * fg_rgb[:, :, 2:3]
            blended = np.where(fg_lum > bg_lum, fg_rgb, bg_rgb)
        elif 'LIGHTEN' in bm:
            # lighten: max(bg, fg)
            blended = np.maximum(bg_rgb, fg_rgb)
        else:
            # fallback: normal
            blended = fg_rgb

        # ── 光效层合成：仅在底层有内容的区域起作用 ──
        # PSD 中光效 blend mode 的语义是"附着于底层"：底层透明区域不产生新像素
        has_bg = (bg_a > 1e-6).astype(np.float32)
        mix_factor = fg_a * has_bg
        out_rgb = bg_rgb * (1.0 - mix_factor) + blended * mix_factor

        # 输出 alpha = 底层 alpha（光效层不增加覆盖面积）
        out_a = bg_a

        result = np.empty_like(base_arr)
        result[:, :, :3] = np.clip(out_rgb, 0, 1)
        result[:, :, 3:4] = np.clip(out_a, 0, 1)
        return result

    def get_penetrate_map(self) -> dict[int, list[LightEffectLayerInfo]]:
        """获取穿透映射表。"""
        return self._penetrate_map

    def get_suppressed_layers(self) -> set[int]:
        """获取需要抑制独立导出的光效层 id 集合。"""
        return self._suppressed_light_layers

    def get_fallback_layers(self) -> set[int]:
        """获取降级为 CSS blend 的光效层 id 集合。"""
        return self._fallback_light_layers
