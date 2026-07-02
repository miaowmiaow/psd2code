"""空间聚类：行列切分 + 叠图判定

包含以下功能：
- ``_split_by_rows``: 按 Y 轴主导重叠率聚类
- ``_split_by_cols``: 按 X 轴区间聚类（带微重叠容忍）
- ``_is_stack_group``: 叠图组判定
- ``_cluster``: 递归聚类入口
- ``_cluster_row``: 行内聚类
- ``_is_fake_multirow_stack``: 伪多行堆叠装饰检测
"""

from itertools import combinations
from typing import List

from .data_types import BBox, LeafInfo, LayoutNode


class ClusteringMixin:
    """空间聚类 Mixin

    使用者须提供：
    - ``self.config`` (ClusterConfig)
    - ``self._extract_tall_decor_leaves()``（来自 TallDecorMixin）
    - ``self._extract_background_leaves()``（来自 BackgroundMixin）
    - ``self._envelope()``
    """

    def _cluster(self, leaves: List[LeafInfo]) -> LayoutNode:
        """递归聚类：先按行切，多行则包 col；单行走列聚类"""
        if len(leaves) == 1:
            leaf = leaves[0]
            return LayoutNode(kind='leaf', bbox=leaf.bbox, leaf=leaf)

        rows = self._split_by_rows(leaves)

        if len(rows) > 1:
            if self._is_fake_multirow_stack(rows):
                return LayoutNode(
                    kind='stack',
                    bbox=self._envelope([l.bbox for l in leaves]),
                    children=[self._leaf_to_node(l) for l in leaves],
                )

            children_nodes = [self._cluster_row(r) for r in rows]
            return LayoutNode(
                kind='col',
                bbox=self._envelope([l.bbox for l in leaves]),
                children=children_nodes,
            )

        return self._cluster_row(rows[0])

    def _is_fake_multirow_stack(self, rows: List[List[LeafInfo]]) -> bool:
        """判定多行结果是否实为"上下贴边的堆叠装饰"，应回退 stack。"""
        if len(rows) < 2:
            return False
        if len(rows) >= self.config.fake_multirow_max_rows:
            return False
        if any(len(r) > 1 for r in rows):
            return False

        envelopes = [self._envelope([l.bbox for l in r]) for r in rows]
        ratio_threshold = self.config.multi_row_stack_fallback_x_ratio
        for a, b in zip(envelopes, envelopes[1:]):
            ax_w = max(0.0, a.right - a.left)
            bx_w = max(0.0, b.right - b.left)
            min_w = min(ax_w, bx_w)
            if min_w <= 0:
                return False
            overlap_x = max(0.0, min(a.right, b.right) - max(a.left, b.left))
            if overlap_x / min_w < ratio_threshold:
                return False
        return True

    def _cluster_row(self, row_leaves: List[LeafInfo]) -> LayoutNode:
        if len(row_leaves) == 1:
            leaf = row_leaves[0]
            return LayoutNode(kind='leaf', bbox=leaf.bbox, leaf=leaf)

        if self._is_stack_group([l.bbox for l in row_leaves]):
            return LayoutNode(
                kind='stack',
                bbox=self._envelope([l.bbox for l in row_leaves]),
                children=[self._leaf_to_node(l) for l in row_leaves],
            )

        cols = self._split_by_cols(row_leaves)
        if len(cols) > 1:
            children_nodes = [self._cluster(c) for c in cols]
            return LayoutNode(
                kind='row',
                bbox=self._envelope([l.bbox for l in row_leaves]),
                children=children_nodes,
            )

        return LayoutNode(
            kind='stack',
            bbox=self._envelope([l.bbox for l in row_leaves]),
            children=[self._leaf_to_node(l) for l in row_leaves],
        )

    @staticmethod
    def _leaf_to_node(leaf: LeafInfo) -> LayoutNode:
        return LayoutNode(kind='leaf', bbox=leaf.bbox, leaf=leaf)

    # ------------------------------------------------------------------
    # 空间聚类：行/列切分
    # ------------------------------------------------------------------

    def _split_by_rows(self, leaves: List[LeafInfo]) -> List[List[LeafInfo]]:
        """按 Y 轴区间聚类（V3：主导重叠率 + 行 envelope 切分）"""
        ordered = sorted(leaves, key=lambda l: (l.bbox.top, l.bbox.left))
        rows: List[List[LeafInfo]] = []
        if not ordered:
            return rows

        current_row = [ordered[0]]
        row_top = ordered[0].bbox.top
        row_bottom = ordered[0].bbox.bottom
        ratio_threshold = self.config.row_dominant_overlap_ratio

        for l in ordered[1:]:
            e_top, e_bottom = l.bbox.top, l.bbox.bottom
            overlap = max(0.0, min(e_bottom, row_bottom) - max(e_top, row_top))
            e_height = max(0.0, e_bottom - e_top)
            row_height = max(0.0, row_bottom - row_top)
            smaller = min(e_height, row_height)
            ratio = overlap / smaller if smaller > 0 else 0.0

            if ratio >= ratio_threshold:
                current_row.append(l)
                row_top = min(row_top, e_top)
                row_bottom = max(row_bottom, e_bottom)
            else:
                rows.append(current_row)
                current_row = [l]
                row_top = e_top
                row_bottom = e_bottom
        rows.append(current_row)
        return rows

    def _split_by_cols(self, leaves: List[LeafInfo]) -> List[List[LeafInfo]]:
        """按 X 轴区间聚类（带微重叠容忍）"""
        ordered = sorted(leaves, key=lambda l: (l.bbox.left, l.bbox.top))
        cols: List[List[LeafInfo]] = []
        if not ordered:
            return cols

        cols.append([ordered[0]])
        right_edge = ordered[0].bbox.right

        for l in ordered[1:]:
            overlap_x = right_edge - l.bbox.left
            min_width = min(cols[-1][-1].bbox.width, l.bbox.width)
            tolerance = max(min_width * self.config.overlap_split_ratio, self.config.col_gap_px * 0.5)

            if overlap_x > tolerance:
                cols[-1].append(l)
                right_edge = max(right_edge, l.bbox.right)
            else:
                cols.append([l])
                right_edge = l.bbox.right
        return cols

    # ------------------------------------------------------------------
    # 叠图判定
    # ------------------------------------------------------------------

    def _is_stack_group(self, bboxes: List[BBox]) -> bool:
        """判定是否为叠图组（stack）
        
        基于两个条件：
        1. 存在高重叠对（重叠率 >= threshold）
        2. 存在包含关系（某个元素被其他元素完全包含）
        
        只有同时满足这两个条件，才判定为真正的堆叠。
        这样可以避免"装饰元素与前景元素有部分重叠"被误判为堆叠。
        """
        effective = [b for b in bboxes if b.area > 0]
        n = len(effective)
        if n < 2:
            return False
        
        # 检查是否存在包含关系
        has_containment = False
        for i, a in enumerate(effective):
            for j, b in enumerate(effective):
                if i != j and self._contains(a, b):
                    has_containment = True
                    break
            if has_containment:
                break
        
        # 没有包含关系，不判定为 stack
        if not has_containment:
            return False
        
        # 存在包含关系，再检查高重叠对比例
        total_pairs = n * (n - 1) // 2
        stack_pairs = 0
        for a, b in combinations(effective, 2):
            if a.overlap_ratio(b) >= self.config.stack_pair_threshold:
                stack_pairs += 1
        return stack_pairs / total_pairs >= self.config.stack_majority
    
    @staticmethod
    def _contains(parent: BBox, child: BBox) -> bool:
        """判定 parent 是否完全包含 child"""
        return (parent.left <= child.left and
                parent.top <= child.top and
                parent.left + parent.width >= child.left + child.width and
                parent.top + parent.height >= child.top + child.height)
