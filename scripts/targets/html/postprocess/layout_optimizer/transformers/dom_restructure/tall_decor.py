"""高瘦跨行装饰剥离

在 ``_split_by_rows`` 之前剥出"高瘦 + 纵向跨过多行 + 跨过的行本身
在 X 上对齐"的 leaf，避免它把多个独立行"引力捕获"成同一行 envelope。

典型场景：领奖.psd wenan__93 内 4 条说明文本（450×21）+ 1 个
icon-refresh 73×84，icon 视觉上跨过 2~4 条文本。
"""

from itertools import combinations
from typing import List, Optional, Tuple

from .data_types import LeafInfo


class TallDecorMixin:
    """高瘦跨行装饰剥离 Mixin

    使用者须提供 ``self.config`` (ClusterConfig)。
    """

    def _extract_tall_decor_leaves(
        self,
        leaves: List[LeafInfo],
    ) -> Tuple[List[LeafInfo], List[LeafInfo]]:
        """识别并剥离"高瘦跨行装饰" leaf

        Returns:
            (decor_leaves, foreground_leaves)
        """
        if not self.config.enable_tall_decor_extraction:
            return [], leaves
        if len(leaves) < self.config.tall_decor_min_crossed_rows + 1:
            return [], leaves

        decor_list: List[LeafInfo] = []
        remaining = list(leaves)

        while True:
            picked = self._pick_one_tall_decor(remaining)
            if picked is None:
                break
            decor_list.append(picked)
            remaining = [l for l in remaining if l is not picked]
            if len(remaining) < 2:
                break
        if not decor_list:
            return [], leaves
        return decor_list, remaining

    def _pick_one_tall_decor(
        self,
        leaves: List[LeafInfo],
    ) -> Optional[LeafInfo]:
        """从当前 leaves 找一个"高瘦跨行装饰"候选"""
        if len(leaves) < self.config.tall_decor_min_crossed_rows + 1:
            return None

        ordered = sorted(leaves, key=lambda l: -l.bbox.height)

        for cand in ordered:
            cw = cand.bbox.width
            ch = cand.bbox.height
            if cw <= 0 or ch <= 0:
                continue

            others = [l for l in leaves if l is not cand]
            if len(others) < self.config.tall_decor_min_crossed_rows:
                continue

            # 条件 1：高度 ≥ 其余中位 × ratio
            other_heights = sorted(l.bbox.height for l in others)
            mid = other_heights[len(other_heights) // 2]
            if mid <= 0:
                continue
            if ch < mid * self.config.tall_decor_height_ratio:
                continue

            # 条件 2：纵横比 ≥ aspect_min
            if ch / cw < self.config.tall_decor_aspect_min:
                continue

            # 条件 3：在 Y 上跨过 ≥ N 个其他 leaves
            crossed: List[LeafInfo] = []
            for o in others:
                ov = max(0.0,
                         min(cand.bbox.bottom, o.bbox.bottom) -
                         max(cand.bbox.top, o.bbox.top))
                if o.bbox.height <= 0:
                    continue
                if ov / o.bbox.height >= 0.5:
                    crossed.append(o)
            if len(crossed) < self.config.tall_decor_min_crossed_rows:
                continue

            # 条件 4：被跨过的 leaves 之间 X 轴对齐
            if not self._are_x_aligned(
                crossed, self.config.tall_decor_x_align_tolerance):
                continue

            return cand

        return None

    @staticmethod
    def _are_x_aligned(leaves: List[LeafInfo], tolerance: float) -> bool:
        """判定 leaves 在 X 轴上属于同一列结构"""
        if len(leaves) < 2:
            return True
        threshold = 1.0 - tolerance
        for a, b in combinations(leaves, 2):
            min_w = min(a.bbox.width, b.bbox.width)
            if min_w <= 0:
                return False
            overlap_x = max(
                0.0,
                min(a.bbox.right, b.bbox.right) -
                max(a.bbox.left, b.bbox.left),
            )
            if overlap_x / min_w < threshold:
                return False
        return True
