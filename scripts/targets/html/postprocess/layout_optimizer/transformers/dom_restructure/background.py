"""背景剥离 / 吸收 / 多层合并

包含以下功能族：
- ``extract_background_leaves``: 从 leaves 中识别并剥离全覆盖背景层
- ``absorb_normal_backgrounds``: 把可吸收的背景 leaf 合并为父 group CSS
- ``merge_bg_candidates_into_container_css``: 多层 background-image 合并写入
- ``try_inline_compose_backgrounds``: 离线合成单张 PNG
- 统一的分层闸门: ``bg_passes_safety_filter`` / ``is_absorbable_bg_leaf``
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .data_types import BBox, LeafInfo


class BackgroundMixin:
    """背景剥离 / 吸收 / 合并的 Mixin

    使用者（DOMRestructure）须提供以下属性：
    - ``self.css_rules``
    - ``self.config`` (ClusterConfig)
    - ``self.parser`` (CSSParser)
    - ``self.images_dir``
    - ``self.stats``
    - ``self.soup``
    """

    # ------------------------------------------------------------------
    # 背景剥离
    # ------------------------------------------------------------------

    def _extract_background_leaves(
        self,
        leaves: List[LeafInfo],
    ) -> Tuple[List[LeafInfo], List[LeafInfo]]:
        """从 leaves 中识别并剥离所有"全覆盖/近全覆盖背景层"（可能多层叠加）

        基础闸门：所有候选必须先通过 _bg_passes_safety_filter
          - data_type == 'image'
          - mix-blend-mode 缺失或 'normal'
          - opacity 缺失或 ≥ 0.99
        与后续 _absorb_* 的吸收判定共享同一安全条件，保证"能剥离 ⟺
        能吸收"的一致性，避免剥离后产生"无法吸收的孤儿背景"。

        形状/覆盖率规则（满足任一即剥离）：
          [主规则] 完全包含型：
            1. bbox 面积占整体 envelope 面积 ≥ background_area_ratio（默认 90%）
            2. bbox 基本包含所有 remaining leaves 的 bbox（容忍 tolerance_px）
          [兜底1] 主轴覆盖型：
            1. bbox 面积占整体 envelope 面积 ≥ background_area_ratio（默认 90%）
            2. bbox 在主轴（宽度或高度）方向上完全覆盖 envelope
          [兜底2] 双轴主导覆盖型：
            1. bbox 面积占整体 envelope 面积 ≥ background_area_ratio（默认 90%）
            2. bbox 在宽/高两轴都覆盖 envelope ≥ background_dual_axis_ratio（默认 80%）

        采用迭代剥离：每次从当前 leaves 里找满足条件的"面积最大候选"，
        移除后再用剩余 leaves 继续尝试；直到再也找不到为止。
        envelope 基准**固定为初始 leaves 的 envelope**，避免剥到后面
        envelope 持续缩小导致把真正的前景元素也当背景剥掉。

        Returns:
            (background_leaves, foreground_leaves)
        """
        if len(leaves) < 2:
            return [], leaves

        envelope = self._envelope([l.bbox for l in leaves])
        env_area = envelope.area
        if env_area <= 0:
            return [], leaves

        tol = self.config.background_contain_tolerance_px
        bg_list: List[LeafInfo] = []
        remaining = list(leaves)

        while True:
            candidates: List[LeafInfo] = []
            for leaf in remaining:
                styles = self.css_rules.get(leaf.css_class) or {}
                if not self._bg_passes_safety_filter(leaf, styles):
                    continue
                if leaf.bbox.area / env_area < self.config.background_area_ratio:
                    continue
                # 优先：完全包含型
                if self._bbox_contains_all(leaf.bbox, remaining, tol):
                    candidates.append(leaf)
                    continue
                # 兜底1：主轴覆盖型
                if self._bbox_covers_main_axis(leaf.bbox, envelope, tol):
                    candidates.append(leaf)
                    continue
                # 兜底2：双轴主导覆盖型
                if self._bbox_dominates_both_axes(
                    leaf.bbox, envelope,
                    self.config.background_dual_axis_ratio,
                ):
                    candidates.append(leaf)

            if not candidates:
                break

            # z 序最低性约束
            non_cand_min_z = min(
                self._sibling_index_in_dom(leaf)
                for leaf in remaining
                if leaf not in candidates
            ) if any(leaf not in candidates for leaf in remaining) else None

            if non_cand_min_z is not None:
                candidates = [
                    leaf for leaf in candidates
                    if self._sibling_index_in_dom(leaf) <= non_cand_min_z
                ]

            if not candidates:
                break
            bg = max(candidates, key=lambda l: l.bbox.area)
            bg_list.append(bg)
            remaining = [l for l in remaining if l is not bg]
            if len(remaining) < 2:
                break

        if not bg_list:
            return [], leaves
        return bg_list, remaining

    # ------------------------------------------------------------------
    # 背景吸收
    # ------------------------------------------------------------------

    def _absorb_normal_backgrounds(self, group, tree: 'LayoutNode') -> List[LeafInfo]:
        """识别 stack tree 中"可吸收"的背景 leaf，并将其合并为 group 的 CSS background。"""
        candidates: List[Tuple[LeafInfo, Dict[str, str]]] = []
        
        container_bbox = self._container_css_bbox(group, fallback=tree.bbox)
        cw = container_bbox.width
        ch = container_bbox.height
        cover_ratio = self.config.container_bg_cover_ratio
        overflow_tol = self.config.container_bg_overflow_tolerance_px
        
        for child in tree.children:
            if child.kind != 'leaf' or child.leaf is None:
                continue
            leaf = child.leaf
            styles = self.css_rules.get(leaf.css_class)
            if styles is None:
                continue
            if not self._is_absorbable_bg_leaf(leaf, styles):
                continue
            
            # 添加覆盖率检查（与 _try_absorb_container_bg 保持一致）
            lbw = leaf.bbox.width
            lbh = leaf.bbox.height
            if lbw / cw < cover_ratio or lbh / ch < cover_ratio:
                continue
            if (leaf.bbox.left < -overflow_tol or
                    leaf.bbox.top < -overflow_tol or
                    leaf.bbox.right > cw + overflow_tol or
                    leaf.bbox.bottom > ch + overflow_tol):
                continue
            
            candidates.append((leaf, styles))

        if not candidates:
            return []

        return self._merge_bg_candidates_into_container_css(
            container_elem=group,
            container_bbox=container_bbox,
            candidates=candidates,
        )

    # ------------------------------------------------------------------
    # 统一闸门
    # ------------------------------------------------------------------

    @staticmethod
    def _bg_passes_safety_filter(
        leaf: LeafInfo, styles: Dict[str, str],
    ) -> bool:
        """判定一个 leaf 是否满足"可视为背景"的安全基线（合成层面）"""
        # 允许 image 或 layer-group 类型通过（layer-group 可能是背景容器如 frame）
        if leaf.data_type not in ('image', 'layer-group'):
            return False
        blend = (styles.get('mix-blend-mode') or '').strip().lower()
        if blend and blend != 'normal':
            return False
        opacity_raw = (styles.get('opacity') or '1').strip()
        try:
            opacity = float(opacity_raw)
        except ValueError:
            return False
        if opacity < 0.99:
            return False
        return True

    def _is_absorbable_bg_leaf(
        self, leaf: LeafInfo, styles: Dict[str, str],
    ) -> bool:
        """判定一个 leaf 是否具备"吸收为容器 background-image"的条件"""
        if not self._bg_passes_safety_filter(leaf, styles):
            return False
        if 'background-image' not in styles:
            return False
        return True

    @staticmethod
    def _sibling_index_in_dom(leaf: LeafInfo) -> int:
        """leaf.element 在其 DOM parent.children 中的位置"""
        elem = leaf.element
        parent = elem.parent
        if parent is None:
            return 0
        for idx, sib in enumerate(parent.children):
            if sib is elem:
                return idx
        return 0

    # ------------------------------------------------------------------
    # 多层合并写入 CSS
    # ------------------------------------------------------------------

    def _merge_bg_candidates_into_container_css(
        self,
        container_elem,
        container_bbox: BBox,
        candidates: List[Tuple[LeafInfo, Dict[str, str]]],
    ) -> List[LeafInfo]:
        """把多个候选 leaf 作为 background-image 多层合并写入 container CSS"""
        if not candidates:
            return []

        # 按 DOM 顺序（z 从低到高）排序，再 reversed（z 从高到低）
        candidates_sorted = sorted(
            candidates, key=lambda c: self._sibling_index_in_dom(c[0]),
        )
        candidates_visual = list(reversed(candidates_sorted))

        classes = container_elem.get('class', [])
        if not classes:
            return []
        container_css_class = f'.{classes[0]}'
        container_styles = self.css_rules.setdefault(container_css_class, {})

        bg_images: List[str] = []
        bg_positions: List[str] = []
        bg_sizes: List[str] = []
        bg_repeats: List[str] = []

        cx, cy = container_bbox.left, container_bbox.top
        cw, ch = container_bbox.width, container_bbox.height

        # 过滤溢出容器边界的 leaf
        filtered_candidates = []
        for leaf, styles in candidates_visual:
            offset_x = leaf.bbox.left - cx
            offset_y = leaf.bbox.top - cy
            if offset_x < -0.5 or offset_y < -0.5:
                leaf.element['data-no-bg-absorb'] = '1'
                continue
            filtered_candidates.append((leaf, styles))

        if not filtered_candidates:
            return []

        for leaf, styles in filtered_candidates:
            bg_images.append(styles['background-image'])

            offset_x = leaf.bbox.left - cx
            offset_y = leaf.bbox.top - cy
            if abs(offset_x) < 0.5 and abs(offset_y) < 0.5:
                bg_positions.append('left top')
            else:
                bg_positions.append(
                    f'{int(round(offset_x))}px {int(round(offset_y))}px'
                )

            if cw > 0 and ch > 0:
                pct_w = leaf.bbox.width / cw * 100.0
                pct_h = leaf.bbox.height / ch * 100.0
                if abs(pct_w - 100.0) < 0.5 and abs(pct_h - 100.0) < 0.5:
                    bg_sizes.append('100% 100%')
                else:
                    bg_sizes.append(
                        f'{int(round(leaf.bbox.width))}px '
                        f'{int(round(leaf.bbox.height))}px'
                    )
            else:
                bg_sizes.append(styles.get('background-size', 'auto'))

            bg_repeats.append(styles.get('background-repeat', 'no-repeat'))

        # container 已有 background-image → 作为更底层拼到末尾
        existing_image = container_styles.get('background-image')
        existing_position = container_styles.get('background-position')
        existing_size = container_styles.get('background-size')
        existing_repeat = container_styles.get('background-repeat')
        if existing_image:
            bg_images.append(existing_image)
            bg_positions.append(existing_position or 'left top')
            bg_sizes.append(existing_size or 'auto')
            bg_repeats.append(existing_repeat or 'no-repeat')

        # 离线合成
        flattened = False
        if self.images_dir is not None and len(bg_images) >= 2:
            flattened = self._try_inline_compose_backgrounds(
                container_styles=container_styles,
                bg_images=bg_images,
                bg_positions=bg_positions,
                bg_sizes=bg_sizes,
                bg_repeats=bg_repeats,
            )

        if not flattened:
            container_styles['background-image'] = ', '.join(bg_images)
            container_styles['background-position'] = ', '.join(bg_positions)
            container_styles['background-size'] = ', '.join(bg_sizes)
            container_styles['background-repeat'] = ', '.join(bg_repeats)

        if self._has_blend_mode_descendant(container_elem):
            container_styles['isolation'] = 'isolate'

        return [leaf for leaf, _ in filtered_candidates]

    # ------------------------------------------------------------------
    # 离线背景合成
    # ------------------------------------------------------------------

    def _try_inline_compose_backgrounds(
        self,
        container_styles: Dict[str, str],
        bg_images: List[str],
        bg_positions: List[str],
        bg_sizes: List[str],
        bg_repeats: List[str],
    ) -> bool:
        """尝试把 ≥2 层背景离线合成为单张 PNG，写回 container_styles。"""
        from ....background_compose import (  # type: ignore
            ComposeLayer,
            compose_layers,
            estimate_bytes_saved,
        )

        if self.images_dir is None or not self.images_dir.is_dir():
            return False

        n = len(bg_images)
        if n < 2:
            return False
        if len(bg_positions) != n or len(bg_sizes) != n:
            return False

        html_dir = self.images_dir.parent

        layers = []
        src_paths: List[Path] = []
        for k in range(n - 1, -1, -1):
            png_path = self._parse_local_png_url(bg_images[k], html_dir)
            if png_path is None:
                return False
            pos = self._parse_position_px(bg_positions[k])
            if pos is None:
                return False
            size = self._parse_size_px(bg_sizes[k])
            if size is None:
                return False
            rep = bg_repeats[k].strip().lower() if k < len(bg_repeats) else 'no-repeat'
            if rep != 'no-repeat':
                return False
            layers.append(ComposeLayer(
                png_path=png_path,
                pos_x=pos[0], pos_y=pos[1],
                size_w=size[0], size_h=size[1],
            ))
            src_paths.append(png_path)

        result = compose_layers(layers, self.images_dir)
        if result is None:
            return False

        new_pos = (
            'left top'
            if (result.origin_x == 0 and result.origin_y == 0)
            else f'{result.origin_x}px {result.origin_y}px'
        )
        container_styles['background-image'] = f'url("{result.rel_url}")'
        container_styles['background-position'] = new_pos
        container_styles['background-size'] = (
            f'{result.canvas_w}px {result.canvas_h}px'
        )
        container_styles['background-repeat'] = 'no-repeat'

        agg = self.stats.setdefault('bg_inline_flatten', {
            'rules_flattened': 0,
            'layers_collapsed': 0,
            'bytes_saved': 0,
        })
        agg['rules_flattened'] += 1
        agg['layers_collapsed'] += n - 1
        agg['bytes_saved'] += estimate_bytes_saved(src_paths, result)
        return True

    @staticmethod
    def _parse_local_png_url(value: str, html_dir: Path) -> Optional[Path]:
        """把单一 ``url("images/xxx.png")`` 解析为物理路径"""
        import re as _re
        m = _re.search(
            r"""url\(\s*(?:"([^"]+)"|'([^']+)'|([^)]+?))\s*\)""", value
        )
        if not m:
            return None
        rel = (m.group(1) or m.group(2) or m.group(3) or '').strip()
        if not rel.lower().endswith('.png'):
            return None
        if '://' in rel or rel.startswith('data:'):
            return None
        if '..' in rel.split('/'):
            return None
        p = html_dir / rel
        return p if p.exists() else None

    @staticmethod
    def _parse_position_px(value: str) -> Optional[Tuple[int, int]]:
        s = value.strip()
        if s in ('left top', '0 0', '0px 0px'):
            return (0, 0)
        import re as _re
        m = _re.match(
            r'^(-?\d+(?:\.\d+)?)px\s+(-?\d+(?:\.\d+)?)px$', s
        )
        if not m:
            return None
        return (int(round(float(m.group(1)))), int(round(float(m.group(2)))))

    @staticmethod
    def _parse_size_px(value: str) -> Optional[Tuple[int, int]]:
        s = value.strip()
        if s in ('100% 100%', 'auto', 'cover', 'contain'):
            return None
        import re as _re
        m = _re.match(r'^(\d+(?:\.\d+)?)px\s+(\d+(?:\.\d+)?)px$', s)
        if not m:
            return None
        return (int(round(float(m.group(1)))), int(round(float(m.group(2)))))

    def _has_blend_mode_descendant(self, container_elem) -> bool:
        """容器内是否存在任意子元素 CSS 含 mix-blend-mode != normal"""
        for desc in container_elem.find_all(True):
            cls_attr = desc.get('class')
            if not cls_attr:
                continue
            for cls in cls_attr:
                styles = self.css_rules.get(f'.{cls}')
                if not styles:
                    continue
                blend = styles.get('mix-blend-mode')
                if blend and blend.strip().lower() != 'normal':
                    return True
        return False

    # ------------------------------------------------------------------
    # 几何工具（背景相关）
    # ------------------------------------------------------------------

    @staticmethod
    def _bbox_covers_main_axis(bbox: BBox, envelope: BBox, tol: float) -> bool:
        """bbox 是否在 envelope 的宽或高方向上完全覆盖"""
        covers_width = (bbox.left <= envelope.left + tol and
                        bbox.right >= envelope.right - tol)
        covers_height = (bbox.top <= envelope.top + tol and
                         bbox.bottom >= envelope.bottom - tol)
        return covers_width or covers_height

    @staticmethod
    def _bbox_dominates_both_axes(
        bbox: BBox, envelope: BBox, ratio: float,
    ) -> bool:
        """bbox 在宽 / 高两个方向上都覆盖 envelope ≥ ratio"""
        env_w = envelope.right - envelope.left
        env_h = envelope.bottom - envelope.top
        if env_w <= 0 or env_h <= 0:
            return False
        cover_w = max(0.0, min(bbox.right, envelope.right) -
                      max(bbox.left, envelope.left)) / env_w
        cover_h = max(0.0, min(bbox.bottom, envelope.bottom) -
                      max(bbox.top, envelope.top)) / env_h
        return cover_w >= ratio and cover_h >= ratio

    @staticmethod
    def _bbox_contains_all(outer: BBox, leaves: List[LeafInfo], tol: float) -> bool:
        """outer 是否基本包含 leaves 中每个元素的 bbox"""
        for leaf in leaves:
            b = leaf.bbox
            if b is outer:
                continue
            if (b.left + tol < outer.left or
                b.top + tol < outer.top or
                b.right - tol > outer.right or
                b.bottom - tol > outer.bottom):
                return False
        return True
