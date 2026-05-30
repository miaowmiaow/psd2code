"""DOM 重构转换器 - 空间聚类 + Flex-Ready 产出

核心思路：
    1. 对每个 group 的直接子元素做空间聚类（行/列切分）
    2. 识别叠图组（stack）保留 absolute
    3. row/col 容器产出 flex-ready 布局：
       - 容器自身设置 display:flex + flex-direction
       - 子元素用 margin 表达偏移，移除 position/left/top
       - 子元素之间间距也用 margin 表达
    4. stack 容器内子元素保留 absolute

附加 pass（按执行顺序，全部默认开启，由 ClusterConfig 单独开关）：
    - 高瘦跨行装饰剥离（方案 A）：在 _split_by_rows 之前剥出"纵向跨多行
      + 跨过的行本身在 X 上对齐"的高瘦 leaf，避免它把多行"引力捕获"成
      一行 envelope（典型：领奖.psd icon-refresh 跨过 5 条说明文本）。
    - 容器背景吸收 pass（restructure 完成后）：扫描所有真实 group + 虚拟
      v-stack/v-row/v-col wrapper 的直接子 image，把"近全覆盖 + opacity≈1
      + normal blend + 不溢出"的 image leaf 吸收为容器 background-image，
      覆盖 _absorb_normal_backgrounds 触达不到的场景。
    - Stack→Col 反向升级 pass（背景吸收之后）：重新评估"被吸收过背景"的
      v-stack：若剩余子元素呈现"完美单列多行列表"，升级为 v-col。N=2
      场景由 reclassify_n2_min_x_overlap (≥0.95) + reclassify_n2_max_gap_px
      (≤50) 双强信号防止真叠图对被误升级。

产出的 DOM 层次已经是"一维整齐"的 flex 结构，下游 SiblingGroupDetector
做平铺同质卡片识别，再由 FlexApplier 做剩余非 v-row/v-col 容器的 flex
推断。
"""

from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..analyzers.layout_analyzer import LayoutAnalyzer
from ..utils.css_parser import CSSParser


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
    enable_container_bg_absorb_pass: bool = True
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


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class DOMRestructure:
    """DOM 重构转换器

    入口：``restructure_dom()``

    对每个 ``layer-group`` 递归做空间聚类，并把结果写回 soup 和 css_rules。
    """

    def __init__(
        self,
        soup,
        css_rules: Dict[str, Dict[str, str]],
        stats: Dict,
        images_dir: Optional[Path] = None,
    ):
        """
        Args:
            images_dir: 物理 ``images/`` 目录。若提供，则在
                ``_merge_bg_candidates_into_container_css`` 决定写出多 url 背景
                时，会尝试把所有候选层离线合成为单张 PNG，CSS 仅写一层 url。
                None（或目录不存在）则跳过合成，按原"多 url"逻辑写出，由
                下游 ``background_flatten`` 兜底处理。
        """
        self.soup = soup
        self.css_rules = css_rules
        self.stats = stats
        self.images_dir = images_dir
        self.parser = CSSParser()
        self.config = ClusterConfig()
        self._virtual_seq = 0  # 虚拟容器自增 id

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def restructure_dom(self):
        print("  📦 步骤0: DOM重构（空间聚类 + Flex-Ready 产出）...")

        # 自顶向下遍历：先处理外层 group，后处理内层 group
        # 注意：处理过程中会新增虚拟容器，但我们只对 layer-group 做聚类，
        # 虚拟容器内部的坐标已经是"相对父 row/col"，不再需要处理。
        all_groups = self._collect_all_groups()

        for group in all_groups:
            try:
                self._restructure_group(group)
            except Exception as exc:  # noqa: BLE001
                name = group.get('data-name', 'unknown')
                print(f"    ⚠️  {name} 处理失败: {exc}")
                import traceback
                traceback.print_exc()

        # 聚类 + 背景剥离完成后，统一再做一次"容器级"背景吸收扫描：
        # 覆盖 _absorb_normal_backgrounds 触达不到的容器（真实 group 未走
        # 剥离分支，或 v-stack 虚拟 wrapper 内有 100% 覆盖的 image leaf）。
        if self.config.enable_container_bg_absorb_pass:
            self._absorb_container_backgrounds_pass()

    # ------------------------------------------------------------------
    # 统一背景吸收 pass（方案 2）
    # ------------------------------------------------------------------

    def _absorb_container_backgrounds_pass(self):
        """遍历所有容器，对每个容器的直接子 image leaf 做统一背景吸收

        容器范围：
          - 真实 group（class 含 'layer-group'）
          - 虚拟 wrapper（data-virtual 为 'stack' / 'row' / 'col'）

        判定条件（全部满足才吸收）：
          1. `_is_absorbable_bg_leaf`：image + bg + opacity≈1 + normal blend
          2. 子 leaf 的 bbox 覆盖容器 ≥ container_bg_cover_ratio（默认 95%）
          3. 子 leaf 的 bbox 不溢出容器超过 container_bg_overflow_tolerance_px
          4. 子 leaf 的 z-index 在容器所有直接子中最低（或并列最低）
             —— 保证吸收后"容器背景" CSS 层级语义正确
          5. 容器自身尺寸有效（width/height > 0）

        实施：找到候选 leaf 后，复用 _merge_bg_candidates_into_container_css
        合并成 container 的 background-image；从 DOM/css_rules 中移除 leaf。

        副作用：成功吸收后给 container 打 `data-bg-absorbed="1"` 标记，
        供后续 `_reclassify_stacks_after_bg_absorption` pass 识别。
        """
        stats_absorbed = 0
        absorbed_containers: List = []

        # 采集所有容器（按从内到外排序：先处理深的，避免吸收后改变父容器子关系）
        # 实际上这里从内到外不是必需——吸收只发生在 container 自身的 CSS，
        # 不会改变 parent-of-container 的子关系；任意顺序都正确。
        containers = self._collect_bg_absorb_target_containers()

        for container in containers:
            absorbed = self._try_absorb_container_bg(container)
            if absorbed:
                stats_absorbed += len(absorbed)
                container['data-bg-absorbed'] = '1'
                absorbed_containers.append(container)


        if stats_absorbed > 0:
            print(f"    🌐 容器背景吸收 pass: 共吸收 {stats_absorbed} 个 image leaf 为容器 background")

        # 反向升级 pass：被吸收过背景的 v-stack，若剩余子元素呈现"单列多行"
        # 形态，则升级为 v-col（带 display:flex; flex-direction:column）
        if self.config.enable_stack_to_col_reclassify and absorbed_containers:
            self._reclassify_stacks_after_bg_absorption(absorbed_containers)

        # 清理临时标记属性（避免残留到最终 HTML 输出）
        for el in self.soup.find_all(attrs={'data-no-bg-absorb': True}):
            del el['data-no-bg-absorb']

    def _collect_bg_absorb_target_containers(self) -> List:
        """采集所有可能作为"背景吸收目标"的容器元素

        包括：
          - 所有 layer-group（真实容器）
          - 所有虚拟 wrapper（data-virtual 属性）
        """
        result = []
        # layer-group
        result.extend(
            self.soup.find_all(
                'div',
                class_=lambda x: x and 'layer-group' in str(x),
            )
        )
        # 虚拟 wrapper
        result.extend(
            self.soup.find_all('div', attrs={'data-virtual': True})
        )
        # 去重（避免将来有同一元素既是 layer-group 又带 data-virtual）
        seen = set()
        unique = []
        for elem in result:
            key = id(elem)
            if key in seen:
                continue
            seen.add(key)
            unique.append(elem)
        return unique

    def _try_absorb_container_bg(self, container) -> List[LeafInfo]:
        """对单个容器尝试吸收其直接子中的"全覆盖背景 image leaf"

        Returns: 被吸收并已从 DOM/css_rules 清除的 leaf 列表
        """
        # 容器自身的 bbox（从 CSS 读取尺寸；虚拟 wrapper 的 width/height 已由
        # _write_wrapper_css 写入；真实 group 的 width/height 也在 CSS 中）
        classes = container.get('class', [])
        if not classes:
            return []
        container_css_class = f'.{classes[0]}'
        container_styles = self.css_rules.get(container_css_class)
        if not container_styles:
            return []
        try:
            cw = self.parser.parse_px(container_styles.get('width', '0'))
            ch = self.parser.parse_px(container_styles.get('height', '0'))
        except (ValueError, AttributeError):
            return []
        if cw <= 0 or ch <= 0:
            return []

        # 容器内坐标系的 bbox：原点 (0,0)，宽高就是容器尺寸
        # 注：容器的直接子 leaf 的 left/top 已经是"相对容器"的坐标
        # （由 _render_stack / _render_flex / _apply_flex_child_margins 保证）
        # 真实 group 的直接子同样是相对 group 的（原 CSS 语义）
        container_bbox = BBox(0.0, 0.0, cw, ch)

        # 采集直接子 leaves（非 group/wrapper）
        children_info = self._collect_direct_children_info(container)
        if not children_info:
            return []

        # 候选：image + 满足吸收基本条件 + 覆盖率/溢出/z 最低
        cover_ratio = self.config.container_bg_cover_ratio
        overflow_tol = self.config.container_bg_overflow_tolerance_px

        candidates: List[Tuple[LeafInfo, Dict[str, str]]] = []
        for info in children_info:
            if info['kind'] != 'leaf':
                continue
            leaf = info['leaf']
            styles = info['styles']
            # 被 tree 路径标记为"不可吸收"的 leaf 跳过
            if leaf.element.get('data-no-bg-absorb'):
                continue
            if not self._is_absorbable_bg_leaf(leaf, styles):
                continue

            # 覆盖率校验（相对容器）
            lbw = leaf.bbox.width
            lbh = leaf.bbox.height
            if lbw / cw < cover_ratio or lbh / ch < cover_ratio:
                continue
            # 溢出校验
            if (leaf.bbox.left < -overflow_tol or
                    leaf.bbox.top < -overflow_tol or
                    leaf.bbox.right > cw + overflow_tol or
                    leaf.bbox.bottom > ch + overflow_tol):
                continue
            candidates.append((leaf, styles))

        if not candidates:
            return []

        # z-index 最低性校验：候选 leaf 的 z-index 必须 ≤ 所有非候选子的 z-index
        # 否则吸收到父 background 会让它压到其他子元素下方（CSS 语义：
        # 父 background 永远在子元素之下），破坏原叠序
        candidate_leaves = {id(c[0]) for c in candidates}
        non_candidate_z_list: List[int] = []
        for info in children_info:
            if info['kind'] == 'leaf' and id(info['leaf']) in candidate_leaves:
                continue
            z = info.get('z_index')
            if z is not None:
                non_candidate_z_list.append(z)

        candidate_z_list: List[int] = []
        for leaf, styles in candidates:
            try:
                z = int(float(styles.get('z-index', '0')))
            except (ValueError, TypeError):
                z = 0
            candidate_z_list.append(z)
        max_candidate_z = max(candidate_z_list) if candidate_z_list else 0

        # 严格要求：任何候选 z 不得高于任何非候选 z
        if non_candidate_z_list and max_candidate_z > min(non_candidate_z_list):
            return []

        # 执行合并
        absorbed = self._merge_bg_candidates_into_container_css(
            container_elem=container,
            container_bbox=container_bbox,
            candidates=candidates,
        )
        if not absorbed:
            return []

        # 从 DOM / css_rules 中清除被吸收的 leaf
        for leaf in absorbed:
            leaf.element.extract()
            self.css_rules.pop(leaf.css_class, None)

        return absorbed

    def _collect_direct_children_info(self, container) -> List[Dict]:
        """采集 container 的直接子元素信息（供容器级吸收判定用）

        每项：
          {
            'kind': 'leaf' | 'group' | 'wrapper',
            'leaf': LeafInfo 或 None (仅 kind='leaf' 有),
            'styles': CSS dict (leaf 的或 group/wrapper 的),
            'z_index': int 或 None,
          }

        只 kind='leaf' 的项才可能是吸收候选。其他 kind 只用于 z-index 对比。
        """
        result: List[Dict] = []
        for child in list(container.find_all(recursive=False)):
            classes = child.get('class', [])
            if not classes:
                continue
            css_class = f'.{classes[0]}'
            styles = self.css_rules.get(css_class)
            if styles is None:
                continue

            # 读 z-index（缺失记 None，不参与 min 对比）
            z_raw = styles.get('z-index')
            try:
                z_index = int(float(z_raw)) if z_raw is not None else None
            except (ValueError, TypeError):
                z_index = None

            # 判定 kind
            is_layer_group = 'layer-group' in classes
            is_wrapper = child.get('data-virtual') is not None
            if is_layer_group or is_wrapper:
                result.append({
                    'kind': 'group' if is_layer_group else 'wrapper',
                    'leaf': None,
                    'styles': styles,
                    'z_index': z_index,
                })
                continue

            # leaf：必须能读出 bbox
            try:
                left = self.parser.parse_px(styles.get('left', '0'))
                top = self.parser.parse_px(styles.get('top', '0'))
                width = self.parser.parse_px(styles.get('width', '0'))
                height = self.parser.parse_px(styles.get('height', '0'))
            except (ValueError, AttributeError):
                continue

            leaf = LeafInfo(
                element=child,
                css_class=css_class,
                name=child.get('data-name', ''),
                data_type=child.get('data-type', ''),
                bbox=BBox(left, top, left + width, top + height),
            )
            result.append({
                'kind': 'leaf',
                'leaf': leaf,
                'styles': styles,
                'z_index': z_index,
            })
        return result

    # ------------------------------------------------------------------
    # Stack → Col 反向升级 pass（方案 B，2026-04-29）
    # ------------------------------------------------------------------

    def _reclassify_stacks_after_bg_absorption(self, absorbed_containers: List):
        """背景吸收后，重新评估那些"被吸收过背景"的 v-stack 容器。

        升级条件（全部满足才升级为 v-col）：
          1. container 是 v-stack（data-virtual="stack"）
          2. 剩余直接子（leaf 或子 wrapper/group）≥ reclassify_min_rows（默认 2）
          3. _split_by_rows 切出的行数 == 子元素总数（每行单元素，纯单列）
          4. 各行 envelope 横向覆盖率 ≥ x 阈值
             - N=2：reclassify_n2_min_x_overlap（默认 0.95，更严）
             - N≥3：reclassify_x_overlap_ratio（默认 0.8）
          5. gap 校验：
             - N=2：单 gap ≤ reclassify_n2_max_gap_px（默认 50）
             - N≥3：相邻行 gap 的变异系数 ≤ reclassify_gap_cv_max（默认 0.4）

        注意：**不**调用 `_is_fake_multirow_stack`——它会把"完美单列多行列表"
        100% 误拦。N=2 场景靠 4 + 5 双强信号防止真叠图对被误升级。

        升级动作：
          - 容器 class 列表中把 v-stack 标记替换为 v-col
          - 容器 css_rules：移除 position:relative，加入 display:flex /
            flex-direction:column / align-items:flex-start
          - data-virtual: stack → col
          - 子元素：清掉 left/top/position（absolute），按 col flex 流写入
            margin-top（gap）和 margin-left（cross 偏移）
        """
        upgraded = 0
        for container in absorbed_containers:
            if self._try_reclassify_stack_to_col(container):
                upgraded += 1
        if upgraded > 0:
            print(f"    🔄 Stack→Col 反向升级 pass: 升级 {upgraded} 个 v-stack 为 v-col")

    def _try_reclassify_stack_to_col(self, container) -> bool:
        """对单个 container 尝试 stack → col 升级。返回是否升级。"""
        # 仅处理 v-stack
        if container.get('data-virtual') != 'stack':
            return False
        classes = container.get('class', [])
        if not classes:
            return False

        # 收集容器自身 CSS（取得宽度，用于横向覆盖率计算）
        container_css_class = f'.{classes[0]}'
        container_styles = self.css_rules.get(container_css_class)
        if not container_styles:
            return False
        try:
            cw = self.parser.parse_px(container_styles.get('width', '0'))
            ch = self.parser.parse_px(container_styles.get('height', '0'))
        except (ValueError, AttributeError):
            return False
        if cw <= 0 or ch <= 0:
            return False

        # 收集剩余直接子（含 leaf/wrapper/group），统一抽出 bbox
        # 注意：reclassify pass 在背景吸收**之后**执行，所以 image 背景已经移除
        children_data = self._collect_reclassify_children(container)
        if len(children_data) < self.config.reclassify_min_rows:
            return False

        # 子元素 bbox 是相对容器原点 (0,0) 的（_render_stack 已写入相对坐标）
        leaves_for_split = [
            LeafInfo(
                element=cd['element'],
                css_class=cd['css_class'],
                name='',
                data_type='',
                bbox=cd['bbox'],
            )
            for cd in children_data
        ]

        # 按 top 排序
        leaves_for_split.sort(key=lambda l: (l.bbox.top, l.bbox.left))

        # 跑 _split_by_rows：每行必须只含 1 个元素（真单列）
        rows = self._split_by_rows(leaves_for_split)
        if len(rows) != len(leaves_for_split):
            return False
        if len(rows) < self.config.reclassify_min_rows:
            return False

        # 注意：此处**不调用** `_is_fake_multirow_stack`。该函数用于 `_cluster`
        # 内防误切（2 个元素上下贴边被切成各 1 行），但 reclassify 期望的就是
        # "完美单列多行列表"，会 100% 命中 fake_stack 而被误拦。我们改用：
        # N≥3 时 `reclassify_min_rows + 横向覆盖率 + gap 均匀度` 三条校验；
        # N=2 时 `n2_min_x_overlap (≥0.95) + n2_max_gap_px (≤50)` 双强信号。

        # 横向覆盖率校验：相邻两行（每行单元素 → 直接用 bbox）横向覆盖率
        # / min(width) ≥ 阈值。N=2 场景下收紧到 reclassify_n2_min_x_overlap，
        # 因为只有"几乎完全同 left+width"才可能是真列表（而不是叠图对）。
        if len(rows) == 2:
            x_ratio_threshold = self.config.reclassify_n2_min_x_overlap
        else:
            x_ratio_threshold = self.config.reclassify_x_overlap_ratio
        for i in range(len(rows) - 1):
            a = rows[i][0].bbox
            b = rows[i + 1][0].bbox
            overlap_x = max(0.0, min(a.right, b.right) - max(a.left, b.left))
            min_w = min(a.width, b.width)
            if min_w <= 0:
                return False
            if overlap_x / min_w < x_ratio_threshold:
                return False

        # gap 均匀度校验：相邻行 gap = next.top - prev.bottom
        gaps: List[float] = []
        for i in range(len(rows) - 1):
            gap = rows[i + 1][0].bbox.top - rows[i][0].bbox.bottom
            gaps.append(gap)

        # N=2 专用：只有 1 个 gap，无法算变异系数。改用绝对 gap 上限来防止
        # "上下分开的两个大块"（比如 banner + 按钮区）被误升级成 flex-col 列表。
        if len(rows) == 2 and gaps:
            if gaps[0] > self.config.reclassify_n2_max_gap_px:
                return False
        elif gaps:
            mean_gap = sum(gaps) / len(gaps)
            # 允许全部 gap 都为 0 / 接近 0（紧贴）：此时 cv 不计算，直接通过
            if abs(mean_gap) > 0.5:
                variance = sum((g - mean_gap) ** 2 for g in gaps) / len(gaps)
                std_gap = variance ** 0.5
                cv = std_gap / abs(mean_gap)
                if cv > self.config.reclassify_gap_cv_max:
                    return False

        # 全部条件满足 → 执行升级
        self._upgrade_stack_container_to_col(
            container=container,
            container_css_class=container_css_class,
            container_styles=container_styles,
            container_bbox=BBox(0.0, 0.0, cw, ch),
            ordered_children=children_data,
            rows=rows,
        )
        return True

    def _collect_reclassify_children(self, container) -> List[Dict]:
        """采集 container 的直接子，每项含 element / css_class / styles / bbox

        bbox 来自 CSS 的 left/top/width/height（_render_stack 写入的"相对容器
        原点"坐标）。读不出 bbox 的子元素让整个 reclassify 失败（保守策略）。
        """
        result: List[Dict] = []
        for child in list(container.find_all(recursive=False)):
            classes = child.get('class', [])
            if not classes:
                return []
            css_class = f'.{classes[0]}'
            styles = self.css_rules.get(css_class)
            if styles is None:
                return []
            try:
                left = self.parser.parse_px(styles.get('left', '0'))
                top = self.parser.parse_px(styles.get('top', '0'))
                width = self.parser.parse_px(styles.get('width', '0'))
                height = self.parser.parse_px(styles.get('height', '0'))
            except (ValueError, AttributeError):
                return []
            if width <= 0 or height <= 0:
                return []
            result.append({
                'element': child,
                'css_class': css_class,
                'styles': styles,
                'bbox': BBox(left, top, left + width, top + height),
                'is_wrapper_or_group': (
                    child.get('data-virtual') is not None
                    or 'layer-group' in classes
                ),
            })
        return result

    def _upgrade_stack_container_to_col(
        self,
        container,
        container_css_class: str,
        container_styles: Dict[str, str],
        container_bbox: BBox,
        ordered_children: List[Dict],
        rows: List[List[LeafInfo]],
    ):
        """把 v-stack 容器及其子元素改写为 v-col 形态"""
        # 1. 改容器 class 标记：v-stack → v-col（保留 v-N 类名不变以维持
        #    DOM/CSS 唯一标识；marker 类替换；data-virtual 同步）
        old_classes = list(container.get('class', []))
        new_classes = []
        for c in old_classes:
            if c == 'v-stack':
                new_classes.append('v-col')
            else:
                new_classes.append(c)
        container['class'] = new_classes
        container['data-virtual'] = 'col'

        # 2. 改容器 css：移除 stack 的 position:relative，加 flex 三件套
        #    （保留 width/height/box-sizing 等，不要清其他键）
        # position:relative 是 stack 子元素 absolute 的定位基准，col 模式下
        # 子元素已经回到 flex 流，不再依赖；但保留 relative 也无害（不影响
        # flex 行为），出于"最小改动"考虑保留。
        container_styles['display'] = 'flex'
        container_styles['flex-direction'] = 'column'
        container_styles['align-items'] = 'flex-start'

        # 3. 子元素改写：按 _apply_flex_child_margins(flex_kind='col') 的
        #    语义重写 left/top/position/margin。子元素顺序按 rows 顺序
        #    （已按 top 排序）。
        # 准备 element → bbox 映射
        elem_to_bbox: Dict[int, BBox] = {
            id(cd['element']): cd['bbox'] for cd in ordered_children
        }
        elem_to_iswrapper: Dict[int, bool] = {
            id(cd['element']): cd['is_wrapper_or_group'] for cd in ordered_children
        }

        # 用 rows 顺序（每行 1 元素）拿到排序后的 leaves
        sorted_leaves = [r[0] for r in rows]

        # 4. 重新挂载子元素到容器，保证 DOM 顺序与 rows 顺序一致
        #    （v-stack 原本 DOM 顺序可能与 z-index 相关，不一定按 top；
        #    flex col 必须按视觉顺序排）
        for leaf in sorted_leaves:
            leaf.element.extract()
        for leaf in sorted_leaves:
            container.append(leaf.element)

        # 5. 写 margin
        prev_bbox: Optional[BBox] = None
        for leaf in sorted_leaves:
            child_bbox = elem_to_bbox[id(leaf.element)]
            is_wrapper_or_group = elem_to_iswrapper[id(leaf.element)]
            # 子元素若是 stack wrapper（v-stack）→ 需要保持 position:relative
            # 作为其内部 absolute 子元素的定位基准；其余（leaf / row / col /
            # 真实 group）→ static
            child_position = 'static'
            if is_wrapper_or_group and leaf.element.get('data-virtual') == 'stack':
                child_position = 'relative'
            self._apply_flex_child_margins(
                child_css_class=leaf.css_class,
                child_bbox=child_bbox,
                parent_bbox=container_bbox,
                prev_bbox=prev_bbox,
                flex_kind='col',
                child_position=child_position,
            )
            prev_bbox = child_bbox

        self.stats['dom_restructured'] = self.stats.get('dom_restructured', 0) + 1

    # ------------------------------------------------------------------
    # 收集所有 group（自顶向下）
    # ------------------------------------------------------------------

    def _collect_all_groups(self) -> List:
        """按 DOM 文档顺序收集所有 layer-group 元素（父先于子）"""
        return self.soup.find_all(
            'div',
            class_=lambda x: x and 'layer-group' in str(x),
        )

    # ------------------------------------------------------------------
    # 处理单个 group
    # ------------------------------------------------------------------

    def _can_flex_applier_handle(self, group) -> bool:
        """探测 group 是否能被 FlexApplier 识别为 vertical / horizontal flex。

        目的：dom_restructure 把"含半透明全覆盖底框 + 多元素"的容器误判为
        叠图组（典型：rounded__19 opacity=0.5 跨满 bbox，导致 _split_by_rows
        把所有子并入同一行）。这类容器实际上 FlexApplier 的 V10 装饰剥离 +
        trend 算法可以正确识别。早退前调一次 LayoutAnalyzer 探测：若识别为
        vertical / horizontal 形态，跳过早退、不动 DOM，让 FlexApplier 接手；
        否则保持原叠图组判定。
        """
        children = list(group.find_all(recursive=False))
        if len(children) < 3:
            return False
        try:
            analyzer = LayoutAnalyzer(self.css_rules)
            result = analyzer.analyze_children_layout(children)
        except Exception:
            return False
        return result.get('layout_type') in ('vertical', 'horizontal')

    def _restructure_group(self, group):
        leaves = self._collect_leaves(group)
        if len(leaves) < self.config.min_children_to_cluster:
            return

        # 构建布局树
        tree = self._build_tree(leaves)

        # 树的根若为单叶：保留原样
        if tree.kind == 'leaf':
            return

        # 根是 stack：区分两种情况
        # 1. 原生叠图组（全部是 leaf 子节点）→ 保留原样
        # 2. 背景层剥离产物（含 row/col 子树）→ 走 stack 分支
        #    在写回 DOM 前，先尝试"吸收"可合并的背景 leaf：
        #    - normal blend + 不透明 + 有 background-image → 合并到父 group CSS，删除 leaf
        #    - 其余背景（带 blend mode / 半透明）保留为 stack 子节点
        if tree.kind == 'stack':
            has_flex_subtree = any(c.kind in ('row', 'col') for c in tree.children)
            if not has_flex_subtree:
                name = group.get('data-name', 'unknown')
                # 放行例外：如果 LayoutAnalyzer 在装饰剥离后能识别为
                # vertical / horizontal 形态（典型：卡片底框 + 单列/单行内容），
                # 跳过早退、不做任何 DOM 改动，让下游 FlexApplier 接手。
                if self._can_flex_applier_handle(group):
                    print(f"    ⏭ {name}: 叠图组判定撤销，转交 FlexApplier")
                    return
                print(f"    ⊙ {name}: 识别为叠图组 ({len(leaves)} 个图层)，保持 absolute")
                return

            name = group.get('data-name', 'unknown')

            # 尝试吸收可合并的背景 leaf 到 group CSS
            absorbed_leaves = self._absorb_normal_backgrounds(group, tree)

            # 重建 children：剔除已吸收的 leaf
            if absorbed_leaves:
                tree.children = [
                    c for c in tree.children
                    if not (c.kind == 'leaf' and c.leaf in absorbed_leaves)
                ]
                # 从 DOM 中删除被吸收的 leaf 元素，并清理其 CSS
                for leaf in absorbed_leaves:
                    leaf.element.extract()
                    self.css_rules.pop(leaf.css_class, None)

            # 全部背景都被吸收 + 只剩一个 row/col 前景子树 → 降级为 flex
            if (len(tree.children) == 1 and
                    tree.children[0].kind in ('row', 'col')):
                fg = tree.children[0]
                # 把剩下的 leaves 重新计算（仅前景）
                remaining_leaves = [
                    l for l in leaves if l not in absorbed_leaves
                ]
                summary = self._summarize_tree(fg)
                absorbed_info = (
                    f"（吸收 {len(absorbed_leaves)} 个背景）"
                    if absorbed_leaves else ""
                )
                print(f"    ✓ {name}: 背景吸收 → flex {summary}{absorbed_info}")

                # 清空 group 原有子元素
                for leaf in remaining_leaves:
                    leaf.element.extract()

                # group 变为 flex 容器（沿用 row/col 渲染逻辑）
                self._apply_flex_to_existing_container(group, fg)

                root_kind = fg.kind
                if root_kind == 'row':
                    sorted_root_children = sorted(
                        fg.children, key=lambda c: (c.bbox.left, c.bbox.top))
                else:
                    sorted_root_children = sorted(
                        fg.children, key=lambda c: (c.bbox.top, c.bbox.left))

                prev_bbox: Optional[BBox] = None
                for child_tree in sorted_root_children:
                    if child_tree.kind == 'leaf':
                        child_elem = child_tree.leaf.element
                        child_css_class = child_tree.leaf.css_class
                    else:
                        child_elem = self._render_tree(
                            child_tree, parent_origin=fg.bbox)
                        virtual_class = child_elem.get('class', [])
                        child_css_class = (
                            f".{virtual_class[0]}" if virtual_class else None
                        )
                    child_position = (
                        'relative' if child_tree.kind == 'stack' else 'static'
                    )
                    self._apply_flex_child_margins(
                        child_css_class,
                        child_bbox=child_tree.bbox,
                        parent_bbox=fg.bbox,
                        prev_bbox=prev_bbox,
                        flex_kind=root_kind,
                        child_position=child_position,
                    )
                    group.append(child_elem)
                    prev_bbox = child_tree.bbox
                return

            # 仍有不可吸收的背景：走原 stack 渲染分支
            summary = self._summarize_tree(tree)
            absorbed_info = (
                f"（吸收 {len(absorbed_leaves)} 个背景）"
                if absorbed_leaves else ""
            )
            print(f"    ✓ {name}: 背景剥离 → {summary}{absorbed_info}")

            # 清空 group 原有子元素（剩余的）
            remaining_leaves = [
                l for l in leaves if l not in absorbed_leaves
            ]
            for leaf in remaining_leaves:
                leaf.element.extract()

            # group 本身变为 stack 容器（position:relative 已由原 CSS 保证）
            self._apply_stack_to_existing_container(group, tree)

            # 渲染 stack 的每个子节点：背景 leaf 设为 absolute 填充；
            # row/col 子树作为独立 wrapper 追加（坐标相对 group 原点）
            for child_tree in tree.children:
                if child_tree.kind == 'leaf':
                    leaf = child_tree.leaf
                    styles = self.css_rules.setdefault(leaf.css_class, {})
                    styles['position'] = 'absolute'
                    styles['left'] = f'{int(round(leaf.bbox.left - tree.bbox.left))}px'
                    styles['top'] = f'{int(round(leaf.bbox.top - tree.bbox.top))}px'
                    for k in ('margin', 'margin-left', 'margin-top',
                              'margin-right', 'margin-bottom'):
                        styles.pop(k, None)
                    group.append(leaf.element)
                else:
                    child_elem = self._render_tree(child_tree, parent_origin=tree.bbox)
                    # 子 wrapper 在 stack 父中，需 absolute 定位相对 stack 原点
                    sub_classes = child_elem.get('class', [])
                    if sub_classes:
                        sub_css_class = f'.{sub_classes[0]}'
                        sub_styles = self.css_rules.setdefault(sub_css_class, {})
                        sub_styles['position'] = 'absolute'
                        sub_styles['left'] = f'{int(round(child_tree.bbox.left - tree.bbox.left))}px'
                        sub_styles['top'] = f'{int(round(child_tree.bbox.top - tree.bbox.top))}px'
                        for k in ('margin', 'margin-left', 'margin-top',
                                  'margin-right', 'margin-bottom'):
                            sub_styles.pop(k, None)
                    group.append(child_elem)
            return

        # 将树写回 DOM：替换 group 的直接子元素
        name = group.get('data-name', 'unknown')
        summary = self._summarize_tree(tree)
        print(f"    ✓ {name}: 聚类为 {summary}")

        # 清空 group 原有子元素（element 引用已经在 leaves 中保存）
        for leaf in leaves:
            leaf.element.extract()

        # 把树渲染到 group 下
        # 根容器（row/col）不自己生成 wrapper div，而是把 flex 样式应用到 group 本身
        self._apply_flex_to_existing_container(group, tree)

        # 根节点的子元素需要按 row/col 语义设置 margin，然后 append 到 group 下
        root_kind = tree.kind  # 'row' or 'col'
        if root_kind == 'row':
            sorted_root_children = sorted(tree.children, key=lambda c: (c.bbox.left, c.bbox.top))
        else:
            sorted_root_children = sorted(tree.children, key=lambda c: (c.bbox.top, c.bbox.left))

        prev_bbox: Optional[BBox] = None
        for child_tree in sorted_root_children:
            # 先渲染子节点（可能返回新 wrapper 或原 leaf element）
            if child_tree.kind == 'leaf':
                child_elem = child_tree.leaf.element
                child_css_class = child_tree.leaf.css_class
            else:
                child_elem = self._render_tree(child_tree, parent_origin=tree.bbox)
                virtual_class = child_elem.get('class', [])
                child_css_class = f".{virtual_class[0]}" if virtual_class else None

            # stack wrapper 需要保持 position:relative（给内部 absolute 子元素作定位基准）
            child_position = 'relative' if child_tree.kind == 'stack' else 'static'

            self._apply_flex_child_margins(
                child_css_class,
                child_bbox=child_tree.bbox,
                parent_bbox=tree.bbox,
                prev_bbox=prev_bbox,
                flex_kind=root_kind,
                child_position=child_position,
            )

            group.append(child_elem)
            prev_bbox = child_tree.bbox

    # ------------------------------------------------------------------
    # 叶子收集
    # ------------------------------------------------------------------

    def _collect_leaves(self, group) -> List[LeafInfo]:
        leaves: List[LeafInfo] = []
        for child in list(group.find_all(recursive=False)):
            classes = child.get('class', [])
            if not classes:
                continue
            css_class = f".{classes[0]}"
            if css_class not in self.css_rules:
                continue

            styles = self.css_rules[css_class]
            try:
                left = self.parser.parse_px(styles.get('left', '0'))
                top = self.parser.parse_px(styles.get('top', '0'))
                width = self.parser.parse_px(styles.get('width', '0'))
                height = self.parser.parse_px(styles.get('height', '0'))
            except (ValueError, AttributeError):
                continue

            leaves.append(
                LeafInfo(
                    element=child,
                    css_class=css_class,
                    name=child.get('data-name', ''),
                    data_type=child.get('data-type', ''),
                    bbox=BBox(left, top, left + width, top + height),
                )
            )
        return leaves

    def _container_css_bbox(
        self, container, fallback: "BBox",
    ) -> "BBox":
        """返回容器自身 CSS 坐标系下的 bbox（原点 0,0，宽高 = CSS width/height）。

        当 CSS 未提供 width/height（罕见：虚拟 wrapper 未写 size）时回退到
        fallback（通常是 tree.bbox envelope），但此时 offset/size 计算可能
        与 leaf 绝对坐标不一致，调用方应对此 case 有容错能力。
        """
        classes = container.get('class', [])
        if classes:
            styles = self.css_rules.get(f'.{classes[0]}')
            if styles:
                try:
                    cw = self.parser.parse_px(styles.get('width', '0'))
                    ch = self.parser.parse_px(styles.get('height', '0'))
                    if cw > 0 and ch > 0:
                        return BBox(0.0, 0.0, cw, ch)
                except (ValueError, AttributeError):
                    pass
        return fallback

    # ------------------------------------------------------------------
    # 构建布局树
    # ------------------------------------------------------------------

    def _build_tree(self, leaves: List[LeafInfo]) -> LayoutNode:
        if len(leaves) == 1:
            leaf = leaves[0]
            return LayoutNode(kind='leaf', bbox=leaf.bbox, leaf=leaf)

        # 背景层剥离：识别全覆盖的 image 型背景层，避免其干扰行列切分
        # 支持剥离多个背景层（某些组有多层叠加背景）
        bg_leaves, fg_leaves = self._extract_background_leaves(leaves)
        if bg_leaves and len(fg_leaves) >= 1:
            fg_tree = self._build_tree_without_bg(fg_leaves)
            # 前景是 row/col 时，才值得把背景包回 stack；
            # 前景还是 stack/leaf 时，剥离无意义，回退到原逻辑
            if fg_tree.kind in ('row', 'col'):
                envelope = self._envelope([l.bbox for l in leaves])
                children = [self._leaf_to_node(bg) for bg in bg_leaves]
                children.append(fg_tree)
                return LayoutNode(
                    kind='stack',
                    bbox=envelope,
                    children=children,
                )

        # 高瘦跨行装饰剥离（无背景剥离场景下）：方案 A
        # 背景已剥离的场景由 _build_tree_without_bg 内部处理；
        # 这里处理"无背景层但有跨行装饰"的场景。
        decor_leaves, fg_leaves2 = self._extract_tall_decor_leaves(leaves)
        if decor_leaves and len(fg_leaves2) >= 2:
            fg_tree = self._build_tree_without_bg(fg_leaves2)
            if fg_tree.kind in ('row', 'col'):
                envelope = self._envelope([l.bbox for l in leaves])
                children = [self._leaf_to_node(d) for d in decor_leaves]
                children.append(fg_tree)
                return LayoutNode(
                    kind='stack',
                    bbox=envelope,
                    children=children,
                )

        # 整组叠图判定
        if self._is_stack_group([l.bbox for l in leaves]):
            return LayoutNode(
                kind='stack',
                bbox=self._envelope([l.bbox for l in leaves]),
                children=[self._leaf_to_node(l) for l in leaves],
            )

        return self._cluster(leaves)

    def _build_tree_without_bg(self, leaves: List[LeafInfo]) -> LayoutNode:
        """构建前景 leaves 的布局树（不再递归做背景剥离，避免无限循环）

        会再做一次"高瘦跨行装饰剥离"：背景剥完后剩下的前景里，
        仍可能含 icon/徽章这类跨行高瘦元素，需要剥出独立成 stack 子节点
        而不被纳入 row/col 切分。
        """
        if len(leaves) == 1:
            leaf = leaves[0]
            return LayoutNode(kind='leaf', bbox=leaf.bbox, leaf=leaf)

        # 高瘦跨行装饰剥离（前景内部，不再递归背景）
        decor_leaves, fg_leaves = self._extract_tall_decor_leaves(leaves)
        if decor_leaves and len(fg_leaves) >= 2:
            inner_tree = self._cluster(fg_leaves) if not self._is_stack_group(
                [l.bbox for l in fg_leaves]) else LayoutNode(
                    kind='stack',
                    bbox=self._envelope([l.bbox for l in fg_leaves]),
                    children=[self._leaf_to_node(l) for l in fg_leaves],
                )
            if inner_tree.kind in ('row', 'col'):
                envelope = self._envelope([l.bbox for l in leaves])
                children = [self._leaf_to_node(d) for d in decor_leaves]
                children.append(inner_tree)
                return LayoutNode(
                    kind='stack',
                    bbox=envelope,
                    children=children,
                )

        if self._is_stack_group([l.bbox for l in leaves]):
            return LayoutNode(
                kind='stack',
                bbox=self._envelope([l.bbox for l in leaves]),
                children=[self._leaf_to_node(l) for l in leaves],
            )

        return self._cluster(leaves)

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
            （这类用于识别"横条背景"/"纵条背景"，它们虽然不完整包含每个前景，
              但因为跨度极大，会严重干扰 row/col 切分）
          [兜底2] 双轴主导覆盖型：
            1. bbox 面积占整体 envelope 面积 ≥ background_area_ratio（默认 90%）
            2. bbox 在宽/高两轴都覆盖 envelope ≥ background_dual_axis_ratio（默认 80%）
            （略带 padding 的卡片底图）

        采用迭代剥离：每次从当前 leaves 里找满足条件的"面积最大候选"，
        移除后再用剩余 leaves 继续尝试；直到再也找不到为止。
        envelope 基准**固定为初始 leaves 的 envelope**，避免剥到后面
        envelope 持续缩小导致把真正的前景元素也当背景剥掉。

        Returns:
            (background_leaves, foreground_leaves)
            按剥离顺序（面积从大到小）返回背景列表；前景保持原顺序。
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
                # 统一安全闸门：必须是 image + 非合成影响（opacity/blend）
                # 否则即使形状像背景也不能剥离——剥离后若又无法吸收会退化为
                # 尴尬的 absolute 子节点，且剥离本身意味着"它应该被当作底板"，
                # 前提是它自身合成透明行为正常。
                styles = self.css_rules.get(leaf.css_class) or {}
                if not self._bg_passes_safety_filter(leaf, styles):
                    continue
                if leaf.bbox.area / env_area < self.config.background_area_ratio:
                    continue
                # 优先：完全包含型
                if self._bbox_contains_all(leaf.bbox, remaining, tol):
                    candidates.append(leaf)
                    continue
                # 兜底1：主轴覆盖型（水平/垂直横条背景）
                if self._bbox_covers_main_axis(leaf.bbox, envelope, tol):
                    candidates.append(leaf)
                    continue
                # 兜底2：双轴主导覆盖型（带边距的卡片底图）
                # area 已 ≥ 90% + 在宽/高两轴都覆盖 envelope ≥ 80%
                # 这种背景不严格包含所有元素（标题/小图标可能突出在边距内），
                # 但本质就是"略带 padding 的底板"，剥离才不会污染行切分。
                if self._bbox_dominates_both_axes(
                    leaf.bbox, envelope,
                    self.config.background_dual_axis_ratio,
                ):
                    candidates.append(leaf)

            if not candidates:
                break

            # z 序最低性约束（关键）：候选必须是 z 最低的之一，否则它在
            # PSD 中是"上层装饰大图"。被错误剥离后会被 _absorb_normal_backgrounds
            # 吸收到容器 CSS background-image，CSS 语义上 background 永远在
            # 子元素之下 → 视觉叠序被破坏（上层装饰被压到底层，下层米黄底反而
            # 暴露在视觉中显眼位置）。
            #
            # DOM 中 leaf.element 的 sibling index 就是 PSD z-index 从低到高，
            # 因此候选必须满足：sibling_index(候选) ≤ min(sibling_index(非候选))。
            # 与 _absorb_container_backgrounds_pass 中的 z 校验语义一致。
            #
            # 典型 case（抽奖活动页面-01-520 PSD 组 51）：
            #   layer-7/8/9（z=低）+ layer-10/11（z=高，覆盖整组的上层装饰大图）
            #   旧逻辑：layer-10/11 几何上 area=99.84% 完全包含其他 → 被错误剥离
            #   新逻辑：sibling_index(10/11) 高于 sibling_index(7/8/9) → 不剥离
            #   → 整组 5 layers 进入 _is_stack_group → 80% 重叠对 ≥ 50% → 判 stack
            #   → 保持原 absolute z 序，与未优化版渲染一致。
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
    # 背景吸收：把可吸收的背景 leaf 合并为父 group 的 CSS background-image
    # ------------------------------------------------------------------

    def _absorb_normal_backgrounds(self, group, tree: 'LayoutNode') -> List[LeafInfo]:
        """识别 stack tree 中"可吸收"的背景 leaf，并将其合并为 group 的 CSS background。

        可吸收条件（全部满足）：
          1. tree.children 中的 leaf 节点（即 stack 直接子节点中的图层）
          2. data_type == 'image'
          3. CSS 中无 mix-blend-mode 或值为 'normal'
          4. CSS 中 opacity 缺失或 ≥ 0.99
          5. CSS 中存在 background-image

        多个可吸收背景按 z 序（DOM 顺序：靠后的在视觉上方）拼接为
        background-image: url(top), url(mid), url(bottom);
        每层的 background-position/size/repeat 也对齐拼接，按 leaf 在 group
        bbox 中的相对位置/尺寸计算。

        被吸收的 leaf 由调用方负责从 DOM/css_rules 中移除。
        """
        candidates: List[Tuple[LeafInfo, Dict[str, str]]] = []
        for child in tree.children:
            if child.kind != 'leaf' or child.leaf is None:
                continue
            leaf = child.leaf
            styles = self.css_rules.get(leaf.css_class)
            if styles is None:
                continue
            if not self._is_absorbable_bg_leaf(leaf, styles):
                continue
            candidates.append((leaf, styles))

        if not candidates:
            return []

        # container_bbox 必须是"容器 CSS 实际占据的 bbox"（自身坐标系，
        # 原点 0,0，宽高 = 容器 CSS width/height），而不是 tree.bbox
        # （= 子 leaves 的 envelope）。两者在"容器 CSS 尺寸 ≠ envelope
        # 尺寸/位置"时会不同，典型：
        #   section-bg__37 容器 CSS 363×80，内有单子 candy__34
        #   bbox (-4,-4,361×78)（PSD 原装饰向左上溢出 4px）。
        # 若用 envelope (-4,-4,361×78) 做 cx/cy/cw/ch：
        #   offset_x = -4 - (-4) = 0 → bg-position: left top（丢失 -4px）
        #   pct_w = 361/361 = 100% → bg-size: 100% 100%（图被拉伸到 363×80）
        # 浏览器视觉：装饰整张拉伸后贴容器左上 → 比 PSD 原 absolute 下
        # left:-4/top:-4 偏右下 4px（"偏下"）。
        # 改用 (0,0,cw,ch) 后：offset=(-4,-4) 写入 background-position，
        # bg-size 按 361×78 实际尺寸写入，视觉等价 PSD absolute 原布局。
        container_bbox = self._container_css_bbox(group, fallback=tree.bbox)

        return self._merge_bg_candidates_into_container_css(
            container_elem=group,
            container_bbox=container_bbox,
            candidates=candidates,
        )

    # ------------------------------------------------------------------
    # 共享工具：候选判定 + 多层合并写入 CSS
    # 由 _absorb_normal_backgrounds（stack tree 路径）与统一
    # _absorb_container_backgrounds_pass（容器扫描路径）共同调用，
    # 保证"一份判定 + 一份合并逻辑"，避免分散（对照方案 1 陷阱）。
    # ------------------------------------------------------------------

    # --- 背景判定：统一的分层闸门 --------------------------------------
    # 把"一个 leaf 是否应该被视为容器背景"的判定集中成一个分层函数家族，
    # 剥离路径（_extract_background_leaves）与吸收路径（_absorb_*）都
    # 强制走这里，消除"同一概念的三份实现、阈值各异、新增规则要改多处"
    # 的方案 1 陷阱。
    #
    # 层级 1（_bg_passes_safety_filter）：合成安全前提。一个 leaf 只要
    # 打算"被合并到容器 CSS"或"从聚类中剥离"，都必须先通过这一关；否则
    # 剥离后会产生"无法吸收、也不适合独立渲染"的尴尬状态。条件：
    #   - data_type == 'image'
    #   - mix-blend-mode 缺失或 'normal'
    #   - opacity 缺失或 ≥ 0.99
    # 注意：不强制要求存在 background-image——该字段在 CSS 写回时才
    # 存在，剥离阶段 styles 一定已有它（因为 layer-renderer 先于
    # layout-optimizer 执行）；但为了适度解耦，把"是否有 background-image"
    # 留给层级 2。
    #
    # 层级 2（_is_absorbable_bg_leaf）：吸收前提。层级 1 + 必须存在
    # background-image（才有东西可以合并到容器 CSS）。
    #
    # 形状/覆盖率判定（_bbox_covers_main_axis / _bbox_dominates_both_axes
    # / _bbox_contains_all / 容器级覆盖率 cover_ratio）仍分散在各自上下文，
    # 因为它们依赖不同的"参考系"（envelope / 容器 bbox）；但**作为"是否
    # 背景"的基础资格判定**，必须统一到这两层闸门。
    # ------------------------------------------------------------------

    @staticmethod
    def _bg_passes_safety_filter(
        leaf: LeafInfo, styles: Dict[str, str],
    ) -> bool:
        """判定一个 leaf 是否满足"可视为背景"的安全基线（合成层面）

        这是所有"把 leaf 当成背景处理"的代码路径都必须通过的最小闸门，
        确保该 leaf 后续无论走剥离还是吸收，合成结果都正确。
        """
        if leaf.data_type != 'image':
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
        """判定一个 leaf 是否具备"吸收为容器 background-image"的条件

        = 安全基线（_bg_passes_safety_filter） + 存在 background-image。
        形状/覆盖率校验由调用方按所处参考系各自执行。
        """
        if not self._bg_passes_safety_filter(leaf, styles):
            return False
        if 'background-image' not in styles:
            return False
        return True

    @staticmethod
    def _sibling_index_in_dom(leaf: LeafInfo) -> int:
        """leaf.element 在其 DOM parent.children 中的位置（=PSD z-index 从低到高）"""
        elem = leaf.element
        parent = elem.parent
        if parent is None:
            return 0
        for idx, sib in enumerate(parent.children):
            if sib is elem:
                return idx
        return 0

    def _merge_bg_candidates_into_container_css(
        self,
        container_elem,
        container_bbox: BBox,
        candidates: List[Tuple[LeafInfo, Dict[str, str]]],
    ) -> List[LeafInfo]:
        """把多个候选 leaf 作为 background-image 多层合并写入 container CSS

        Args:
            container_elem: bs4 Tag（真实 group 或 v-stack/v-row/v-col wrapper）
            container_bbox: 容器内部坐标系的 bbox（通常 left/top=0 或 envelope 起点）
            candidates: [(leaf, styles), ...] 候选集合。调用方已完成判定。

        规则：
          - 多层按 DOM 顺序（=PSD z 序）排列，后置 = 视觉顶层
          - CSS background-image 多层：第一个 = 视觉顶层，最后一个 = 视觉最底层
          - 所以拼写入顺序 = DOM 顺序 reversed
          - 若 container 已有 background-image，视作更底层拼到末尾

        被吸收的 leaf 由调用方负责从 DOM/css_rules 中移除。
        """
        if not candidates:
            return []

        # 按 DOM 顺序（z 从低到高）排序，再 reversed（z 从高到低）→ CSS 前置 = 视觉顶层
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

        # 过滤掉溢出容器边界的 leaf（offset 为负表示图片超出容器左/上边界，
        # background-image 会被容器边界裁剪，无法等价还原 absolute 溢出效果）
        filtered_candidates = []
        for leaf, styles in candidates_visual:
            offset_x = leaf.bbox.left - cx
            offset_y = leaf.bbox.top - cy
            if offset_x < -0.5 or offset_y < -0.5:
                # 标记该 leaf 不可被吸收为背景（防止后续容器背景吸收 pass
                # 因坐标相对化而误判）
                leaf.element['data-no-bg-absorb'] = '1'
                continue  # 溢出容器边界，不适合吸收为 background
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

        # ---- 离线合成（主路径，下沉自原 background_flatten 后处理）----
        # 当 self.images_dir 提供且所有候选层均为本地 PNG / no-repeat / 像素
        # 偏移与尺寸时，把多层离线合成为单张 PNG 直接写一层 url。这样：
        #   1. CSS 一开始就是单 url，CssDedup/CssPretty 不会再看到多层背景
        #   2. 不留下"被弃用"的源 PNG（这些 PNG 仍是其他 leaf 的产物，本来
        #      就由 layer_exporter 写出，合成只是新增一张 flat-*.png）
        #   3. 浏览器只需 1 次请求 + 1 次解码
        # 失败/不满足条件 → 退回多 url，由 background_flatten 兜底
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

        # 背景被吸收为容器 background-image 后，若容器内残留子元素带有
        # mix-blend-mode != normal，浏览器（尤其 Chromium）会让 blend
        # 跨越父背景继续向上合成（穿透到祖先 stacking context），导致
        # 视觉与未优化版（原本子元素只与下方兄弟 background 合成）不一致——
        # 典型表现：color-burn 子元素被烧穿成接近背景色，几乎不可见。
        # 加 `isolation: isolate` 让容器自身成为隔离上下文，限定 blend
        # 仅在容器范围内合成，等价恢复原来的视觉。
        if self._has_blend_mode_descendant(container_elem):
            container_styles['isolation'] = 'isolate'

        return [leaf for leaf, _ in filtered_candidates]

    # ------------------------------------------------------------------
    # 离线背景合成（主路径，下沉自原 background_flatten 后处理）
    # ------------------------------------------------------------------

    def _try_inline_compose_backgrounds(
        self,
        container_styles: Dict[str, str],
        bg_images: List[str],
        bg_positions: List[str],
        bg_sizes: List[str],
        bg_repeats: List[str],
    ) -> bool:
        """尝试把 ≥2 层背景离线合成为单张 PNG，写回 container_styles。

        所有层均需满足：
          - background-image 是单一 ``url("images/xxx.png")`` 形式（本地 PNG）
          - background-position 是 ``Npx Mpx`` 或 ``left top``
          - background-size 是 ``Wpx Hpx``（不接受 100% 100% / auto）
          - background-repeat 全 no-repeat（缺省视为 no-repeat）

        任一不满足或合成失败 → 返回 False，调用方走多 url 路径。
        """
        from ...background_compose import (  # type: ignore
            ComposeLayer,
            compose_layers,
            estimate_bytes_saved,
        )

        if self.images_dir is None or not self.images_dir.is_dir():
            return False

        n = len(bg_images)
        if n < 2:
            return False
        # 简化保险：若 position/size 长度对不上（理论上不会发生，因为本函数
        # 内部刚拼出来），跳过合成
        if len(bg_positions) != n or len(bg_sizes) != n:
            return False

        # html_dir = images_dir.parent；url 路径形如 'images/xxx.png'
        html_dir = self.images_dir.parent

        # bg_images[k] 视觉顺序：第 0 = 顶层，第 n-1 = 底层
        # compose_layers 期望"底层在前"，因此倒序构造
        layers: List[ComposeLayer] = []
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

        # 写回单层 url
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

        # 统计
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
        """把单一 ``url("images/xxx.png")`` 解析为物理路径；不合规返回 None"""
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

    @staticmethod
    def _bbox_covers_main_axis(bbox: BBox, envelope: BBox, tol: float) -> bool:
        """bbox 是否在 envelope 的宽或高方向上完全覆盖（允许 tol 像素误差）

        用于识别横条/纵条背景：它们在一个轴上跨度等于父 envelope，
        另一个轴上占比不到 100%（所以不被"完全包含"规则命中）。
        """
        covers_width = (bbox.left <= envelope.left + tol and
                        bbox.right >= envelope.right - tol)
        covers_height = (bbox.top <= envelope.top + tol and
                         bbox.bottom >= envelope.bottom - tol)
        return covers_width or covers_height

    @staticmethod
    def _bbox_dominates_both_axes(
        bbox: BBox, envelope: BBox, ratio: float,
    ) -> bool:
        """bbox 在宽 / 高两个方向上都覆盖 envelope ≥ ratio（默认 80%）

        用于识别"略带 padding 的卡片底图"：它在两个轴上都几乎跨满 envelope，
        但四边可能各内缩若干像素（10-20px），或顶部因为上方有标题区而不到顶。
        这种背景虽然不严格包含每个元素，但本质就是底板，必须剥离否则会
        在 split_by_rows 时跟所有元素纵向重叠，把全部元素吃到同一行里。
        """
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
        """outer 是否基本包含 leaves 中每个元素的 bbox（允许四边各 tol 像素溢出）"""
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

    # ------------------------------------------------------------------
    # 高瘦跨行装饰剥离（方案 A，2026-04-29）
    # ------------------------------------------------------------------

    def _extract_tall_decor_leaves(
        self,
        leaves: List[LeafInfo],
    ) -> Tuple[List[LeafInfo], List[LeafInfo]]:
        """识别并剥离"高瘦跨行装饰" leaf

        典型场景：领奖.psd wenan__93 内 4 条说明文本（450×21）+ 1 个
        icon-refresh 73×84，icon 视觉上跨过 2~4 条文本。如果不剥离，
        _split_by_rows 会因 icon 高 84 把行 envelope 撑大，把所有
        贴边的小元素吸到同一行 → 形成不合理的 v-row。

        判定（AND，全部命中才剥离）：
          1. leaf.height ≥ 其余 leaves 中位高度 × tall_decor_height_ratio
          2. leaf.height / leaf.width ≥ tall_decor_aspect_min
          3. leaf 在 Y 轴上"显著覆盖" ≥ tall_decor_min_crossed_rows 个其他
             leaves（每个的纵向重叠 / 该 leaf 自身高 ≥ 0.5，即被 leaf 真
             跨过，不是只碰边）
          4. 这些被跨过的 leaves 在 X 上彼此对齐：任意两两 left/right
             差 ≤ tall_decor_x_align_tolerance × min(width)，表明它们本
             身是稳定的多行结构（多行文本、列表）

        剥离顺序：每轮选最满足条件的（按 height 降序）剥一个，再用剩余
        leaves 重新计算"其余中位高度"，直到再也找不到。
        envelope 基准固定为初始 leaves，避免误剥。

        Returns:
            (decor_leaves, foreground_leaves)
            decor 按剥离顺序返回；fg 保持原顺序。
        """
        if not self.config.enable_tall_decor_extraction:
            return [], leaves
        # 至少需要 1 个候选 + N 个其他 leaves 才有"跨行"语义
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
            # 剥离后剩余太少则停（必须再保留 ≥ 2 个 fg 才有意义聚类）
            if len(remaining) < 2:
                break
        if not decor_list:
            return [], leaves
        return decor_list, remaining

    def _pick_one_tall_decor(
        self,
        leaves: List[LeafInfo],
    ) -> Optional[LeafInfo]:
        """从当前 leaves 找一个"高瘦跨行装饰"候选；找不到返回 None"""
        if len(leaves) < self.config.tall_decor_min_crossed_rows + 1:
            return None

        # 按 height 降序优先尝试最高的（更可能是跨行装饰）
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
                # 用"被跨者自身高度"作分母：该 leaf 至少有一半被
                # cand 在 Y 上覆盖，才算被真跨过（非碰边）
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
        """判定 leaves 在 X 轴上属于同一"列结构"

        放宽于严格 left/right 同位：只要任意两两的 X 区间显著重叠
        （overlap_x / min(width) ≥ 1 - tolerance），即视为同列。

        放宽原因：被高瘦装饰跨过的多行说明文本，常见左对齐但右端
        参差不齐（不同句子长短不同），right 不严格相等；但它们的
        X 轴投影区间彼此包含或大幅重叠，本质上仍是稳定的列结构。
        """
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

    def _cluster(self, leaves: List[LeafInfo]) -> LayoutNode:
        """递归聚类：先按行切，多行则包 col；单行走列聚类"""
        if len(leaves) == 1:
            leaf = leaves[0]
            return LayoutNode(kind='leaf', bbox=leaf.bbox, leaf=leaf)

        rows = self._split_by_rows(leaves)

        if len(rows) > 1:
            # 保护：相邻行 envelope 横向几乎完全重叠 → 视为伪多行装饰堆叠
            # 典型场景：宝箱列「图标 + 按钮」上下贴边，纵向重叠率 < 50% 被切成
            # 2 行，但横向 100% 重叠，本质是堆叠（参见 prop-4__93 回归）
            if self._is_fake_multirow_stack(rows):
                return LayoutNode(
                    kind='stack',
                    bbox=self._envelope([l.bbox for l in leaves]),
                    children=[self._leaf_to_node(l) for l in leaves],
                )

            # 多行 → ColNode，每行继续做列切分
            children_nodes = [self._cluster_row(r) for r in rows]
            return LayoutNode(
                kind='col',
                bbox=self._envelope([l.bbox for l in leaves]),
                children=children_nodes,
            )

        # 只有一行 → 走列聚类
        return self._cluster_row(rows[0])

    def _is_fake_multirow_stack(self, rows: List[List[LeafInfo]]) -> bool:
        """判定多行结果是否实为"上下贴边的堆叠装饰"，应回退 stack。

        判定条件：所有相邻行 envelope 之间，横向覆盖率 ≥
        multi_row_stack_fallback_x_ratio（默认 80%）。

        典型反例（不应回退）：
            - 网格 6×4：相邻行横向 100% 重叠，但每行有 4 个并列元素
              → 这种**单行内列数 ≥ 2** 的多行不回退，仍走 col。
            - 多行说明文本（≥ 4 行单行 + 全部 X 同列）：本质上是真正
              的 col 列表，不应回退。典型场景：领奖.psd "文案" 组
              在剥离 icon 跨行装饰后剩 5 条说明，每条 1 行单元素
              且 X 完全对齐，旧规则会误判为堆叠装饰。
              引入 fake_multirow_max_rows 上限：行数 ≥ 该阈值不回退。

        典型正例（应回退）：
            prop-4__93 内只有 img + btn（每行 1 元素），上下贴边
            → 横向 100% 重叠，所有行都是单元素 → 回退 stack
        """
        if len(rows) < 2:
            return False
        # 行数过多 → 几乎不可能是装饰对，视为真列表
        if len(rows) >= self.config.fake_multirow_max_rows:
            return False
        # 任一行有多个元素 → 真正的网格行，不回退
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

        # 行内叠图
        if self._is_stack_group([l.bbox for l in row_leaves]):
            return LayoutNode(
                kind='stack',
                bbox=self._envelope([l.bbox for l in row_leaves]),
                children=[self._leaf_to_node(l) for l in row_leaves],
            )

        cols = self._split_by_cols(row_leaves)
        if len(cols) > 1:
            # 多列 → RowNode，每列递归做行切分
            children_nodes = [self._cluster(c) for c in cols]
            return LayoutNode(
                kind='row',
                bbox=self._envelope([l.bbox for l in row_leaves]),
                children=children_nodes,
            )

        # 单列多元素 → 作为 stack 处理（说明它们在 X/Y 上高度重叠）
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
        """按 Y 轴区间聚类（V3：主导重叠率 + 行 envelope 切分）

        判定规则：
            一个新元素 e 是否归入"当前行"，看它与当前行 envelope
            [row_top, row_bottom] 的纵向重叠 / min(e.height, row.height) 是否
            ≥ row_dominant_overlap_ratio（默认 0.5）。

            - ≥ 阈值 → 同行，扩展行 envelope
            - <  阈值 → 切到新行（即使 e.top < row_bottom，"碰边"不算同行）

        为什么这样改（V2 → V3）：
            V2 旧规则只看 `top < current_bottom` 就归入同行，对"高瘦元素与
            矮元素混排"严重失效。例如某个超大行容器（如组9，T=1093,B=1291）
            底部 1291 紧邻下方真正独立的散落 prop 行（T~1300，B~1495），后者
            会被吃到行内变成"超大行"，破坏后续 col 切分。

            V3 用 envelope 重叠率：散落 prop（T=1300~1495）与组9（T=1093~1291）
            纵向只有 0px 重叠 → 切到新行；而真正同行的 5 个 prop 互相重叠
            ≥ 90% → 仍归入同行。

        阈值依据：
            - 网格化排列（同行元素 height 接近且 top 几乎相同）：
              重叠率 ≈ 90~100%，远 ≥ 0.5
            - "上一行底部 + 下一行顶部"碰边但跨行的元素：
              重叠率 ≈ 0~10%，远 < 0.5
            - 0.5 给同行 height 差异 ≤ 50% 留足空间
        """
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
            # 计算纵向重叠
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
                # 明显重叠 → 同列
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
        # 排除零面积 bbox（常见于"子图层全隐藏的空 group"占位），
        # 它们跟任何实体 bbox 的 overlap_ratio 都是 0，会稀释 stack_pairs
        # 比率，导致真正的叠图组被误判为非叠图。典型案例：QQ炫舞 web 封面
        # PSD 里 qiuguang__39 (0×0) 让组 17 的 18 leaves 从 69/136=50.7%
        # 被稀释到 69/153=45.1%，落到 0.5 阈值下，全组被错误聚类成 col。
        effective = [b for b in bboxes if b.area > 0]
        n = len(effective)
        if n < 2:
            return False
        total_pairs = n * (n - 1) // 2
        stack_pairs = 0
        for a, b in combinations(effective, 2):
            if a.overlap_ratio(b) >= self.config.stack_pair_threshold:
                stack_pairs += 1
        return stack_pairs / total_pairs >= self.config.stack_majority

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    @staticmethod
    def _envelope(bboxes: List[BBox]) -> BBox:
        return BBox(
            left=min(b.left for b in bboxes),
            top=min(b.top for b in bboxes),
            right=max(b.right for b in bboxes),
            bottom=max(b.bottom for b in bboxes),
        )

    def _summarize_tree(self, tree: LayoutNode) -> str:
        def walk(node: LayoutNode) -> str:
            if node.kind == 'leaf':
                return 'leaf'
            tag = {'row': 'R', 'col': 'C', 'stack': 'S'}[node.kind]
            inner = ','.join(walk(c) for c in node.children)
            return f"{tag}[{inner}]"
        return walk(tree)

    def _next_virtual_id(self, kind: str) -> str:
        self._virtual_seq += 1
        return f"v-{kind}-{self._virtual_seq}"

    # ------------------------------------------------------------------
    # 将布局树渲染到 DOM
    # ------------------------------------------------------------------

    def _apply_flex_to_existing_container(self, group_elem, tree: LayoutNode):
        """把树的根节点（row / col）flex 样式应用到已有的 group 容器上

        注意：tree 的 bbox 是相对 group 的（因为 leaves 的 bbox 也是相对 group 的）
        所以 group 容器自身的 left/top/width/height 无需改变。

        关键：tree.bbox 是子元素 envelope，可能不从 group 原点 (0, 0) 开始（即
        group 内部上方/左侧有"留白带"，例如卡片内 padding）。下游
        `_apply_flex_child_margins` 用 envelope 作 parent_bbox 计算 margin，无法
        恢复"envelope 起点相对 group 原点"的偏移。这里把它写为容器自身的
        padding-left / padding-top（配合 box-sizing:border-box 不撑大容器）。
        """
        classes = group_elem.get('class', [])
        if not classes:
            return
        css_class = f".{classes[0]}"
        styles = self.css_rules.setdefault(css_class, {})

        if tree.kind == 'stack':
            # 整组叠图：保证父容器有 position（已有 absolute）
            return

        # row / col：添加 flex 样式
        styles['display'] = 'flex'
        styles['flex-direction'] = 'row' if tree.kind == 'row' else 'column'
        styles['align-items'] = 'flex-start'

        # 把 envelope 起点偏移转为容器 padding，避免内容被推到容器左/上边
        pad_left = int(round(tree.bbox.left))
        pad_top = int(round(tree.bbox.top))
        if pad_left > 0 or pad_top > 0:
            styles['box-sizing'] = 'border-box'
            if pad_left > 0:
                styles['padding-left'] = f'{pad_left}px'
            if pad_top > 0:
                styles['padding-top'] = f'{pad_top}px'

        # 添加标记类，便于下游识别
        marker = 'v-row' if tree.kind == 'row' else 'v-col'
        if marker not in classes:
            classes.append(marker)
            group_elem['class'] = classes

    def _apply_stack_to_existing_container(self, group_elem, tree: LayoutNode):
        """把 stack 根节点应用到已有 group 容器上（用于背景剥离场景）

        group 作为 stack 容器，需要满足子元素 absolute 定位的 containing block
        要求。如果该 group 是顶层组（CSS 已为 position:absolute），可以直接用；
        若该 group 处于父 flex 流式布局中（无 position 或 position:static），
        则必须显式置为 position:relative，否则内部 absolute 子元素会脱离 group
        相对到更外层的 positioned 祖先。

        本方法负责：
          - 清除可能被误写入的 display:flex 残留
          - 确保 position 是 absolute/fixed/relative 之一
          - 添加 v-stack 标记类，便于下游识别
        """
        classes = group_elem.get('class', [])
        if not classes:
            return
        css_class = f".{classes[0]}"
        styles = self.css_rules.setdefault(css_class, {})

        # 清除 flex 残留
        for k in ('display', 'flex-direction', 'align-items',
                  'justify-content', 'gap'):
            if styles.get(k, '').startswith('flex') or k in (
                'flex-direction', 'align-items', 'justify-content', 'gap'):
                styles.pop(k, None)

        # 确保 position 能作为子 absolute 元素的 containing block
        current_pos = (styles.get('position') or '').strip().lower()
        if current_pos not in ('absolute', 'fixed', 'relative', 'sticky'):
            styles['position'] = 'relative'

        marker = 'v-stack'
        if marker not in classes:
            classes.append(marker)
            group_elem['class'] = classes

    def _render_tree(self, node: LayoutNode, parent_origin: BBox):
        """递归渲染布局节点为 DOM 元素

        parent_origin: 父容器的 bbox（用于坐标相对化）
                       - 对 flex 父（row/col）：parent_origin 是父容器的 bbox（bbox 原点是它的 left/top）
                       - 对 stack 父：同样是父容器的 bbox

        返回：已经设置好相对坐标/margin 的 bs4 Tag
        """
        if node.kind == 'leaf':
            return self._render_leaf(node, parent_origin)

        if node.kind == 'stack':
            return self._render_stack(node, parent_origin)

        # row / col 虚拟容器
        return self._render_flex(node, parent_origin)

    # -- leaf ----------------------------------------------------------

    def _render_leaf(self, node: LayoutNode, parent_origin: BBox):
        """叶子节点：注意 margin 写入在父容器渲染时已经完成，这里只返回元素

        但我们需要根据父容器类型在外层决定如何改 CSS。这里不改 CSS，只负责返回元素。
        """
        return node.leaf.element

    # -- stack ---------------------------------------------------------

    def _render_stack(self, node: LayoutNode, parent_origin: BBox):
        """叠图容器：创建 wrapper，子元素保留 absolute 但坐标相对 wrapper"""
        wrapper = self._make_wrapper_div('stack')
        # wrapper 自身定位（相对 parent_origin）
        self._write_wrapper_css(wrapper, node.bbox, parent_origin, flex_kind=None)

        for child in node.children:
            if child.kind != 'leaf':
                # stack 内嵌套 row/col/stack（常见于"背景层 + 前景flex子树"场景）：
                # 把子 wrapper 渲染后，设为 absolute 并相对 stack 原点定位
                rendered = self._render_tree(child, parent_origin=node.bbox)
                sub_classes = rendered.get('class', [])
                if sub_classes:
                    sub_css_class = f'.{sub_classes[0]}'
                    sub_styles = self.css_rules.setdefault(sub_css_class, {})
                    sub_left = child.bbox.left - node.bbox.left
                    sub_top = child.bbox.top - node.bbox.top
                    sub_styles['position'] = 'absolute'
                    sub_styles['left'] = f'{int(round(sub_left))}px'
                    sub_styles['top'] = f'{int(round(sub_top))}px'
                    # 清除可能的 flex-child margin 残留
                    for k in ('margin', 'margin-left', 'margin-top',
                              'margin-right', 'margin-bottom'):
                        sub_styles.pop(k, None)
                wrapper.append(rendered)
                continue

            leaf = child.leaf
            # 子元素坐标：相对 stack 原点（node.bbox）
            new_left = leaf.bbox.left - node.bbox.left
            new_top = leaf.bbox.top - node.bbox.top

            styles = self.css_rules.setdefault(leaf.css_class, {})
            styles['position'] = 'absolute'
            styles['left'] = f'{int(round(new_left))}px'
            styles['top'] = f'{int(round(new_top))}px'
            # 确保没有残留的 margin
            for k in ('margin', 'margin-left', 'margin-top', 'margin-right', 'margin-bottom'):
                styles.pop(k, None)

            wrapper.append(leaf.element)

        self.stats['dom_restructured'] += 1
        return wrapper

    # -- row / col -----------------------------------------------------

    def _render_flex(self, node: LayoutNode, parent_origin: BBox):
        """row / col 容器：创建 wrapper，子元素用 margin 表达偏移"""
        wrapper = self._make_wrapper_div(node.kind)
        self._write_wrapper_css(wrapper, node.bbox, parent_origin, flex_kind=node.kind)

        # 子节点按 row：left 排序；按 col：top 排序
        if node.kind == 'row':
            sorted_children = sorted(node.children, key=lambda c: (c.bbox.left, c.bbox.top))
        else:
            sorted_children = sorted(node.children, key=lambda c: (c.bbox.top, c.bbox.left))

        prev_bbox: Optional[BBox] = None
        for idx, child in enumerate(sorted_children):
            # 先递归渲染子节点（可能是 leaf/row/col/stack）
            if child.kind == 'leaf':
                child_elem = child.leaf.element
                child_css_class = child.leaf.css_class
            else:
                child_elem = self._render_tree(child, parent_origin=node.bbox)
                # 虚拟容器的 css class 用 wrapper id/class
                virtual_class = child_elem.get('class', [])
                child_css_class = f".{virtual_class[0]}" if virtual_class else None

            child_position = 'relative' if child.kind == 'stack' else 'static'

            # 写 margin：子元素在 flex 流中的位置
            self._apply_flex_child_margins(
                child_css_class,
                child_bbox=child.bbox,
                parent_bbox=node.bbox,
                prev_bbox=prev_bbox,
                flex_kind=node.kind,
                child_position=child_position,
            )

            wrapper.append(child_elem)
            prev_bbox = child.bbox

        self.stats['dom_restructured'] += 1
        return wrapper

    def _apply_flex_child_margins(
        self,
        child_css_class: Optional[str],
        child_bbox: BBox,
        parent_bbox: BBox,
        prev_bbox: Optional[BBox],
        flex_kind: str,
        child_position: str = 'static',
    ):
        """子元素在 row/col 容器中，用 margin 表达偏移，移除 left/top

        Args:
            child_position: 子元素要设置的 position 值。
                - ``static``  （默认）：普通 flex 子项，不写入（浏览器默认即 static）
                - ``relative``：stack wrapper 需要作为其内部 absolute 子元素的定位基准
        """
        if not child_css_class:
            return
        styles = self.css_rules.setdefault(child_css_class, {})

        # 清除 left / top / right / bottom（它们在 flex 流里无意义，且可能影响布局）
        for k in ('left', 'top', 'right', 'bottom'):
            styles.pop(k, None)

        # 清除原有的 position:absolute（如果有），让它回到 flex 流
        # - static 场景不写 position（浏览器默认就是 static）
        # - relative 场景显式写入
        # - 例外：若子元素带 z-index（非 None / 非 auto），强制写 relative
        #   让 stacking context 必然生效；与 flex_applier 的同名规则保持一致，
        #   避免同容器内"static + z-index"与"relative + z-index"混存导致的
        #   跨浏览器视觉层级差异
        styles.pop('position', None)
        if child_position == 'static':
            has_z_index = styles.get('z-index') not in (None, 'auto', '')
            if has_z_index:
                styles['position'] = 'relative'
        else:
            styles['position'] = child_position

        # 清除旧 margin（避免累加）
        for k in ('margin', 'margin-left', 'margin-top', 'margin-right', 'margin-bottom'):
            styles.pop(k, None)

        if flex_kind == 'row':
            # padding 仅在 envelope > 0 时写入；负 envelope 时容器原点 = 0。
            # 子元素的 margin-left/top 必须基于"实际起点"而不是 envelope，否则
            # 当 envelope 起点为负时会被多减一次，导致整体偏移。
            origin_left = max(0.0, parent_bbox.left)
            origin_top = max(0.0, parent_bbox.top)
            if prev_bbox is None:
                main_gap = child_bbox.left - origin_left
            else:
                main_gap = child_bbox.left - prev_bbox.right
            cross_offset = child_bbox.top - origin_top

            if abs(main_gap) > 0.5:
                styles['margin-left'] = f'{int(round(main_gap))}px'
            if abs(cross_offset) > 0.5:
                styles['margin-top'] = f'{int(round(cross_offset))}px'
        else:  # col
            origin_left = max(0.0, parent_bbox.left)
            origin_top = max(0.0, parent_bbox.top)
            if prev_bbox is None:
                main_gap = child_bbox.top - origin_top
            else:
                main_gap = child_bbox.top - prev_bbox.bottom
            cross_offset = child_bbox.left - origin_left

            if abs(main_gap) > 0.5:
                styles['margin-top'] = f'{int(round(main_gap))}px'
            if abs(cross_offset) > 0.5:
                styles['margin-left'] = f'{int(round(cross_offset))}px'

        # 禁止子元素被 flex 挤压：PSD 原本是 absolute 布局，各子元素尺寸独立，
        # 若父 flex 容器（通常用 PSD group bbox 作 height/width）的尺寸 <
        # 子元素累计尺寸（含 margin），浏览器 flex-shrink 默认 1 会按比例压缩
        # 所有子，典型破坏：
        #   prop__68 (h=196) = v-stack-7 (h=115) + prop-bg-2__67 (h=88, mt=-1)
        #   = 202 > 196 → 两者各自按比例收缩 → 南瓜图（120×115）被压成 120×111.6
        # 设 flex-shrink:0 后子元素保持 CSS 声明的 width/height，溢出可见，与
        # PSD absolute 布局语义等价。
        styles['flex-shrink'] = '0'

    # -- wrapper 基础 --------------------------------------------------

    def _make_wrapper_div(self, kind: str):
        """创建一个虚拟容器 <div>，注册对应 CSS 类"""
        vid = self._next_virtual_id(kind)
        # class 名与 vid 同名（便于 CSS 定位）；也加上 role marker
        marker = f'v-{kind}'
        div = self.soup.new_tag('div')
        div['class'] = [vid, marker]
        div['data-virtual'] = kind
        # 预注册 css
        self.css_rules[f'.{vid}'] = {}
        return div

    def _write_wrapper_css(
        self,
        wrapper,
        self_bbox: BBox,
        parent_origin: BBox,
        flex_kind: Optional[str],
    ):
        """给虚拟容器写 CSS：
        - 虚拟容器作为 flex 子项存在（父是 row/col），position 默认 static（不写）
        - 设置 width/height 固定值
        - 如果是 flex 容器（row/col），设置 display:flex 和 flex-direction
        - stack 容器：设置 position:relative 作为子元素 absolute 的定位基准
        """
        cls = wrapper.get('class', [])
        if not cls:
            return
        css_class = f'.{cls[0]}'
        styles = self.css_rules.setdefault(css_class, {})

        # 宽高固定
        styles['width'] = f'{int(round(self_bbox.width))}px'
        styles['height'] = f'{int(round(self_bbox.height))}px'
        styles['box-sizing'] = 'border-box'

        if flex_kind == 'row':
            styles['display'] = 'flex'
            styles['flex-direction'] = 'row'
            styles['align-items'] = 'flex-start'
        elif flex_kind == 'col':
            styles['display'] = 'flex'
            styles['flex-direction'] = 'column'
            styles['align-items'] = 'flex-start'
        else:
            # stack: 作为子元素的 positioning context
            styles['position'] = 'relative'

        # margin / position 等由父容器在 _apply_flex_child_margins 里写入（若父是 flex）
