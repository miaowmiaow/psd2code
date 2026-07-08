"""数据结构与配置阈值

包含 DOM 重构全流程共用的数据类型：
- ``BBox``: 轴对齐包围盒
- ``LeafInfo``: 叶子节点（对应原始 PSD 图层）
- ``LayoutNode``: 布局树节点
- ``ClusterConfig``: 聚类 / 背景剥离 / 装饰剥离 / 反向升级 等全部阈值
"""

from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class BBox:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def overlap_ratio(self, other: "BBox") -> float:
        """两 bbox 重叠面积 / min(area1, area2)"""
        ox = max(0.0, min(self.right, other.right) - max(self.left, other.left))
        oy = max(0.0, min(self.bottom, other.bottom) - max(self.top, other.top))
        inter = ox * oy
        if inter <= 0:
            return 0.0
        smaller = min(self.area, other.area)
        return inter / smaller if smaller > 0 else 0.0


@dataclass
class LeafInfo:
    """叶子节点：对应原始 PSD 图层"""
    element: object  # bs4 Tag
    css_class: str  # ".classname"
    name: str
    data_type: str
    bbox: BBox


@dataclass
class LayoutNode:
    kind: str  # 'leaf' | 'row' | 'col' | 'stack'
    bbox: BBox
    leaf: Optional[LeafInfo] = None
    children: List["LayoutNode"] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 配置阈值
# ---------------------------------------------------------------------------

@dataclass
class ClusterConfig:
    row_gap_px: float = 8.0
    col_gap_px: float = 8.0
    # X 重叠 / 较窄元素宽度 > 此值 → 视为同列（允许微重叠分列）
    overlap_split_ratio: float = 0.2
    # 两 bbox 重叠率超过视为"叠图对"
    stack_pair_threshold: float = 0.6
    # 叠图对 / 总对数 超过视为"整组叠图"
    stack_majority: float = 0.5
    min_children_to_cluster: int = 2
    # 背景层剥离：子 leaf 的 bbox 面积占所有 leaves envelope 面积比例 ≥ 此值
    # 且其 bbox 基本包含所有其他 leaves 的 bbox，即视为"全覆盖背景层"，
    # 从聚类输入中剥离，避免其污染行列切分
    background_area_ratio: float = 0.9
    # 背景层"包含其他 leaves"时允许的四边溢出像素（容错）
    background_contain_tolerance_px: float = 2.0
    # 双轴主导覆盖背景：image 在宽 / 高两个方向都覆盖 envelope 的比例
    # ≥ 此值（默认 80%），即认为是"略带边距的卡片底图"，从聚类剥离。
    # 这条规则比"完全包含型"宽松：允许背景在四边各内缩若干像素
    # （例如 11px 边距），且允许背景顶部低于真正的标题区。
    # 用于解决：底板背景受标题/小图章顶部偏移而无法通过严格"包含全部"检测。
    background_dual_axis_ratio: float = 0.8
    # _split_by_rows 主导重叠率切行：新元素与"当前行 envelope" 的纵向
    # 重叠率（overlap / min(elem.height, row.height)）≥ 此值才算同行；
    # 否则即便严格重叠（top < current_bottom）也切到新行。
    # 默认 0.5：新元素至少覆盖较小者一半的纵向跨度，才认为它属于这一行；
    # 否则它只是"碰边"，应该独立成行。
    row_dominant_overlap_ratio: float = 0.5
    # _cluster 多行回退 stack 的保护阈值：当切出多行但相邻行 envelope 在 X
    # 轴上的覆盖率（overlap / min(row_w)）≥ 此值，认为是"上下贴边的装饰对"，
    # 而非真正的多行列表，回退为 stack。
    # 默认 0.8：要求相邻两行在横向几乎完全重叠才回退（典型场景：宝箱列里
    # 「图标 + 按钮」上下叠放，纵向重叠率 < 50% 被切成 2 行，但横向 100%
    # 重叠，本质就是堆叠装饰）。
    multi_row_stack_fallback_x_ratio: float = 0.8
    # _is_fake_multirow_stack 的"行数过多不回退"上限：当切出的行数
    # ≥ 此值时，即使每行单元素 + X 全对齐，也不回退 stack（视为真列表）。
    # 默认 4：典型"上下贴边装饰对"通常 2~3 个，4 个以上几乎只可能是
    # 列表/段落（典型场景：领奖.psd 文案 5 条说明剥 icon 后形成 5 行 col）。
    fake_multirow_max_rows: int = 4
    # ------------------------------------------------------------------
    # 统一背景吸收 pass（方案 2）：restructure 完成后再对所有容器（真实
    # group + 虚拟 v-stack / v-row / v-col wrapper）扫描直接子 image，把
    # 满足"近全覆盖 + opacity≈1 + normal blend + z-index 最低 + 不溢出"
    # 的 image leaf 吸收为容器 background-image。
    #
    # 作用：覆盖 _absorb_normal_backgrounds 触达不到的场景——当某个容器
    # 的聚类结果中 image leaf 未被 _extract_background_leaves 剥离（例如
    # 它作为 stack 的普通成员被保留），但事实上是该容器的整体背景。
    # ------------------------------------------------------------------
    enable_container_bg_absorb_pass: bool = False
    # 候选 leaf 的宽/高覆盖父容器比例阈值
    container_bg_cover_ratio: float = 0.95
    # 候选 leaf 允许的四边溢出像素（相对父容器 bbox）
    container_bg_overflow_tolerance_px: float = 2.0
    # ------------------------------------------------------------------
    # Stack → Col 反向升级 pass（方案 B，2026-04-29）：背景吸收 pass 之后，
    # 重新评估那些"被吸收过背景"的 v-stack。如果剩余子元素呈现"单列多行
    # 列表"的形态（典型：5 行助力卡），就把 v-stack 升级为 v-col。
    #
    # 触发原因：dom_restructure 早期 _cluster_row 遇到"大底框 + N 个并列卡"
    # 这种"前景与底框 100% 纵向重叠 + X 同列"的组合时，会 fallback 判 stack；
    # 等容器背景吸收 pass 把底框吸走后，本质上剩下的是真列布局，应该升级。
    # ------------------------------------------------------------------
    enable_stack_to_col_reclassify: bool = True
    # 升级所需的最少剩余子元素行数。设为 2 以接住"恰好 2 张卡"的真列表，
    # N=2 场景靠下方 reclassify_n2_* 两个收紧阈值兜底防误升级（真叠图对）。
    reclassify_min_rows: int = 2
    # 各行 envelope 横向覆盖率阈值（相邻行）：均 ≥ 此值才视为"真单列"
    reclassify_x_overlap_ratio: float = 0.8
    # 相邻行 gap 的"变异系数"上限（标准差 / 均值）：超过则 gap 不均匀，
    # 不像规则列表，放弃升级
    reclassify_gap_cv_max: float = 0.4
    # ---- N=2 专用收紧阈值（仅当子元素恰好 2 个时生效） ----
    # 由于 N=2 时 gap 变异系数恒为 0（只有 1 个 gap），失去均匀度判据，
    # 因此用 横向覆盖率 + 绝对 gap 上限 两个独立强信号代替。
    # 横向覆盖率收紧到 0.95（真叠图对的 badge 几乎不可能与底图同 left+width）
    reclassify_n2_min_x_overlap: float = 0.95
    # 相邻 gap 必须 ≤ 此像素值（紧密列表 vs 上下分隔大块的判别）
    reclassify_n2_max_gap_px: float = 50.0
    # ------------------------------------------------------------------
    # 高瘦跨行装饰剥离（方案 A，2026-04-29）：
    # 在 _split_by_rows 之前剥出"高瘦 + 纵向跨过多行 + 跨过的行本身在 X
    # 上对齐"的 leaf，避免它把多个独立行"引力捕获"成同一行 envelope。
    #
    # 触发原因：_split_by_rows 用 row envelope 切行，一旦某个高瘦元素
    # 入行后会把行底 row_bottom 拉高，所有下方贴边小元素都按 ratio = 1.0
    # 被吸到同行，形成"虚胖"行 → 错误的 v-row。
    #
    # 典型场景：领奖.psd wenan__93 内 4 条说明文本（450×21）+ 1 个
    # icon-refresh 73×84（视觉上跨过 2 条文本），被并入 v-row-17 + v-stack-18。
    #
    # 判定（AND，全部命中才剥离）：
    #   1. leaf.height ≥ 其余 leaves 中位高度 × tall_decor_height_ratio
    #   2. leaf.height / leaf.width ≥ tall_decor_aspect_min（接近正方或更瘦高）
    #   3. leaf 在 Y 区间上"覆盖" ≥ tall_decor_min_crossed_rows 个其他 leaves
    #      （即至少跨过 N 个其他独立元素的纵向区间）
    #   4. 这些被跨过的 leaves 在 X 轴上彼此对齐（任意一对的 left/right 之差
    #      均 ≤ tall_decor_x_align_tolerance × min(width)），表明它们本身是
    #      稳定的多行结构（多行文本/列表）
    # ------------------------------------------------------------------
    enable_tall_decor_extraction: bool = True
    # leaf.height 与其余 leaves 中位高度比的下限
    tall_decor_height_ratio: float = 2.0
    # leaf.height / leaf.width 的下限（防止把宽横条误剥）
    tall_decor_aspect_min: float = 0.8
    # leaf 必须在 Y 上跨过至少 N 个其他独立 leaves 才算"跨行"
    tall_decor_min_crossed_rows: int = 2
    # 被跨过的 leaves 之间 X 轴重叠率容忍度：
    # 任意两两 X 重叠率 ≥ (1 - tolerance) × min(width) 才算同列
    tall_decor_x_align_tolerance: float = 0.2
