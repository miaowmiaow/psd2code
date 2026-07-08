"""同质兄弟分组器（V11，2026-04-28）

问题背景
========
PSD 设计稿里，"商品卡 / 道具卡 / 礼包卡"经常被设计师摆成 N 个同名同结构
的图层，**没有用一个父组包起来**。LayoutOptimizer 之前的 DOM 重构只在已
有 group 内部聚类，这种"平铺在 #canvas 直接子"的列表会全部走 absolute
路径输出，开发拿到的 HTML / CSS 完全看不出"它是一个数据列表"，没法直接
写 ``v-for``。

本模块的职责
===========
扫描所有容器（包括 #canvas 和真实 layer-group）的 **直接子节点**，识别出
"同质兄弟序列"（同 class 词根 + 同子结构 + bbox 尺寸近似 + 整齐网格排
列），把它们包一层虚拟 wrapper ``v-list``，并写 ``display: flex;
flex-wrap: wrap; gap`` 等 CSS。下游开发拿到 HTML 可以直接：

    <div class="prop-list" v-for="item in items">...</div>

判定规则（必须全部满足）
=======================
1. 至少 ``min_count`` 个直接兄弟（默认 3）
2. **class 词根**相同：去掉 ``__\\d+`` 后缀和 ``-\\d+`` 序号后比较
   - ``prop__30`` / ``prop-2__38`` / ``prop-10__101`` → 词根都是 ``prop``
   - 这是**最强的设计师意图信号**（设计师把同类卡命名规范化）
3. **bbox 尺寸近似**：width/height 误差 ≤ ``size_tolerance``（默认 5%）
   - 视觉一致性的硬约束
4. **网格规则**：能排成 ``M 列 × K 行``（含单行/单列），同列 left 一致
   且同行 top 一致（误差 ≤ 2px），且 cols × rows == n（满格）
5. **父非 flex 容器**：如果父 class 含 ``v-row`` / ``v-col``，跳过——
   父容器已经在 flex 化它们了，再 wrap 反而多此一举

**不做子结构同构判定的原因**：实际 PSD 中，同类卡的内部结构几乎总是
有差异（首张卡设计完后复制改文案/图片，结构变化包括少了一行文字、
按钮换成图片、装饰图层数量不同等）。如果强求子结构完全一致，会导致
**绝大多数现实场景识别失败**。class 词根 + bbox 尺寸两条已足够强：
- 词根强约束：保证开发语义一致（都是 prop / item / card）
- 尺寸强约束：保证 flex-wrap 后视觉等效

不破坏现状的关键
==============
- wrap 后的 CSS gap 用"相邻列 left 差 - 列宽"计算 column-gap，
  "相邻行 top 差 - 行高"算 row-gap，与原始 absolute 视觉等效。
- ``v-list`` wrapper 的 left/top/width/height 用 N 个节点 union 后的 bbox
  写入；保持原父容器的 flow 不变。
- 被包裹节点改成 ``position: static``，去掉 left/top；其它属性原样保留。

接入点
======
``optimizer.py`` 中，在 ``DOMRestructure.restructure_dom()`` 之后、
``FlexApplier.apply_flex_layouts()`` 之前调用 ``SiblingGroupDetector.run()``。

排查提示
========
- 如果某 N 张卡视觉错位 → 看 ``v-list-N`` 容器的 left/top 是否对齐，
  或被包裹卡的 left/top 是否被去除。
- 如果该被识别的列表没识别 → 检查 5 条规则中哪个 fail（开 ``DEBUG=True``
  会打印每个 group 的判定细节）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..utils.css_parser import CSSParser


# ---------------------------------------------------------------------------
# 配置阈值
# ---------------------------------------------------------------------------

@dataclass
class SiblingGroupConfig:
    # 至少 N 个连续兄弟才算列表
    min_count: int = 3
    # bbox 宽 / 高相对误差容忍（5%）
    size_tolerance: float = 0.05
    # 同列 / 同行的 left / top 像素误差
    grid_position_tolerance_px: float = 2.0
    # gap 最大允许值：超过 200px 通常说明这不是网格（设计师把卡分散到很远）
    max_gap_px: float = 200.0
    # gap 最小允许值（避免误把 0px 当 gap，重叠卡不应识别）
    min_gap_px: float = -2.0
    # 父容器若包含以下 class，detector 跳过（父已 flex 化）
    skip_parent_classes: Tuple[str, ...] = ('v-row', 'v-col')
    # P2a「CSS Grid 输出」开关：当 cols ≥ 2 且 rows ≥ 2 时优先用 ``display:grid``
    # 而非 ``display:flex; flex-wrap: wrap``。两者视觉等价，但 grid:
    #   - 语义更明确（设计师"N 列 M 行 排列"的意图直接体现在 CSS）
    #   - ``grid-template-columns: repeat(N, ...)`` 让"列宽"成为单一可改点
    #   - 调整 gap 不会带来 wrap 边界问题
    # 1×N（单行）/ N×1（单列）仍走 flex（grid 一维退化没必要）。
    enable_css_grid: bool = True
    grid_min_cols: int = 2
    grid_min_rows: int = 2
    # 调试日志
    debug: bool = False


# ---------------------------------------------------------------------------
# 内部数据结构
# ---------------------------------------------------------------------------

@dataclass
class SiblingItem:
    """候选兄弟节点的简化视图"""
    element: object   # bs4 Tag
    css_class: str    # 首类名（不含 .）
    class_root: str   # 词根（如 'prop'）
    data_name: str    # data-name
    left: float
    top: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class SiblingGroupDetector:
    """同质兄弟分组器

    入口：``run()``

    扫描所有容器的直接子节点，识别同质兄弟序列并包成 v-list。
    """

    # 词根提取：去掉末尾 ``__\d+`` 和 ``-\d+`` 序号
    # 例：prop__30 → prop ；prop-2__38 → prop ；prop-10__101 → prop
    _CLASS_ROOT_RE = re.compile(r'^(.+?)(?:-\d+)?(?:__\d+)?$')

    def __init__(
        self,
        soup,
        css_rules: Dict[str, Dict[str, str]],
        stats: Dict,
        config: Optional[SiblingGroupConfig] = None,
    ):
        self.soup = soup
        self.css_rules = css_rules
        self.stats = stats
        self.config = config or SiblingGroupConfig()
        self.parser = CSSParser()
        self._virtual_seq = 0
        # 本阶段统计
        self.stats.setdefault('sibling_lists_created', 0)
        self.stats.setdefault('sibling_items_wrapped', 0)
        self.stats.setdefault('grid_lists_created', 0)

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def run(self):
        print("\n  📋 步骤1.5：同质兄弟分组（v-list）...")

        # 采集所有可能作为父容器的元素：
        # - #canvas（根容器，最常见的"平铺列表"场景）
        # - 所有 layer-group（真实 group，里面也可能有同质兄弟序列）
        # 注意：从外到内遍历，且每次成功包裹后子结构变化（兄弟变成 v-list 单子），
        # 因此用 list snapshot 即可，不需要重复扫描父。
        containers = self._collect_target_containers()

        for container in containers:
            try:
                self._process_container(container)
            except Exception as exc:  # noqa: BLE001
                name = container.get('id') or container.get('data-name') or 'unknown'
                print(f"    ⚠️  {name} 同质兄弟分组失败: {exc}")
                if self.config.debug:
                    import traceback
                    traceback.print_exc()

        if self.stats['sibling_lists_created'] > 0:
            grid_n = self.stats.get('grid_lists_created', 0)
            extra = f"，其中 {grid_n} 个用 CSS Grid" if grid_n else ""
            print(
                f"    ✅ 同质兄弟分组：创建 {self.stats['sibling_lists_created']} 个 v-list，"
                f"包裹 {self.stats['sibling_items_wrapped']} 个节点{extra}"
            )
        else:
            print("    （无同质兄弟序列被识别）")

    # ------------------------------------------------------------------
    # 容器采集
    # ------------------------------------------------------------------

    def _collect_target_containers(self) -> List:
        """采集所有可能含同质兄弟序列的容器"""
        result = []
        canvas = self.soup.find(id='canvas')
        if canvas:
            result.append(canvas)
        # 真实 layer-group
        result.extend(
            self.soup.find_all(
                'div',
                class_=lambda x: x and 'layer-group' in (x if isinstance(x, list) else x.split()),
            )
        )
        # 去重
        seen = set()
        unique = []
        for el in result:
            if id(el) not in seen:
                seen.add(id(el))
                unique.append(el)
        return unique

    # ------------------------------------------------------------------
    # 核心：处理单个容器
    # ------------------------------------------------------------------

    def _process_container(self, container):
        """扫描容器的直接子节点，识别 + 包裹同质兄弟序列"""

        # 父容器若已是 v-row / v-col，跳过（父已 flex 化它们）
        parent_classes = container.get('class', []) or []
        if any(skip in parent_classes for skip in self.config.skip_parent_classes):
            return

        # 收集所有直接子 <div>（保持 DOM 顺序）
        children = list(container.find_all('div', recursive=False))
        if len(children) < self.config.min_count:
            return

        # 把每个子节点解析成 SiblingItem（无效的跳过）
        items: List[Optional[SiblingItem]] = [self._build_item(c) for c in children]

        # 仅按 class 词根分桶（已论证：不强求子结构同构）。
        # 设计选择：
        #   - 不要求兄弟在 DOM 中"严格连续"——很多设计稿里列表中间会插
        #     入装饰元素（如分隔线），但卡片本身是同质的。
        #   - 但要求至少 ``min_count`` 个同桶元素。
        buckets: Dict[str, List[SiblingItem]] = {}
        for item in items:
            if item is None:
                continue
            buckets.setdefault(item.class_root, []).append(item)

        for class_root, bucket in buckets.items():
            if len(bucket) < self.config.min_count:
                continue
            if not self._sizes_close(bucket):
                if self.config.debug:
                    print(f"    [debug] {class_root}: 尺寸差异 > {self.config.size_tolerance}, 跳过")
                continue
            grid = self._detect_grid(bucket)
            if grid is None:
                if self.config.debug:
                    print(f"    [debug] {class_root}: 不是规则网格, 跳过")
                continue

            # 通过所有判定，包一层 v-list
            self._wrap_as_list(container, bucket, class_root, grid)

    # ------------------------------------------------------------------
    # 单元素解析
    # ------------------------------------------------------------------

    def _build_item(self, element) -> Optional[SiblingItem]:
        """把一个 bs4 Tag 解析成 SiblingItem，无法解析返回 None"""
        classes = element.get('class') or []
        if not classes:
            return None
        # 跳过虚拟 wrapper（v-row / v-col / v-stack / v-list）—— 这些是
        # detector 之前的 pipeline 产物，不是设计师定义的"卡片"。
        if any(c.startswith('v-') for c in classes):
            return None

        css_class = classes[0]
        css = self.css_rules.get(f'.{css_class}', {})
        if not css:
            return None

        # 解析坐标尺寸；DOM 重构后某些容器没有 left/top（已被改成 flex item，
        # 用 margin 排版），这种情况不算同质兄弟（无法做绝对定位 wrap）。
        try:
            left = self.parser.parse_length(css.get('left'), default=None)
            top = self.parser.parse_length(css.get('top'), default=None)
            width = self.parser.parse_length(css.get('width'), default=None)
            height = self.parser.parse_length(css.get('height'), default=None)
        except ValueError:
            return None

        # position 必须是 absolute（detector 只处理"未被 flex 化的"绝对定位元素）
        if css.get('position') != 'absolute':
            return None

        # width / height 必须有效
        if width <= 0 or height <= 0:
            return None

        class_root = self._extract_class_root(css_class)
        data_name = element.get('data-name', '') or ''

        return SiblingItem(
            element=element,
            css_class=css_class,
            class_root=class_root,
            data_name=data_name,
            left=left,
            top=top,
            width=width,
            height=height,
        )

    @classmethod
    def _extract_class_root(cls, css_class: str) -> str:
        """从 'prop-2__38' 提取词根 'prop'

        规则：先去掉 ``__\\d+``，再去掉末尾的 ``-\\d+`` 序号。
        例：
          - prop__30 → prop
          - prop-2__38 → prop
          - prop-10__101 → prop
          - card-item__5 → card-item
          - card-item-2__7 → card-item
        """
        # 去掉 __ 后缀
        root = re.sub(r'__\d+$', '', css_class)
        # 去掉末尾 -数字
        root = re.sub(r'-\d+$', '', root)
        return root

    @staticmethod
    def _subtree_signature(element) -> str:
        """**已弃用**：保留接口避免外部依赖断裂，但 detector 已不调用它。

        历史原因：曾尝试用子结构签名分桶，但实际 PSD 中"同类卡内部结构"
        几乎总有差异（首张卡和复制后的卡，少一行字、按钮换图等），
        导致绝大多数现实场景识别失败。现已仅按 class 词根 + bbox 尺寸
        判定。
        """
        return ''

    # ------------------------------------------------------------------
    # 同质判定
    # ------------------------------------------------------------------

    def _sizes_close(self, items: List[SiblingItem]) -> bool:
        """判定 bbox 宽高是否近似（相对误差 ≤ size_tolerance）"""
        if not items:
            return False
        ws = [it.width for it in items]
        hs = [it.height for it in items]
        return (
            self._values_close(ws, self.config.size_tolerance)
            and self._values_close(hs, self.config.size_tolerance)
        )

    @staticmethod
    def _values_close(values: List[float], tol: float) -> bool:
        """所有值的极差 / 均值 ≤ tol"""
        if not values:
            return False
        avg = sum(values) / len(values)
        if avg <= 0:
            return False
        return (max(values) - min(values)) / avg <= tol

    # ------------------------------------------------------------------
    # 网格检测
    # ------------------------------------------------------------------

    def _detect_grid(self, items: List[SiblingItem]) -> Optional[Dict]:
        """判定 items 是否能排成规则网格

        Returns:
            None：不是规则网格
            {
              'cols': int,            # 列数
              'rows': int,            # 行数
              'col_gap': float,       # 相邻列 gap（first only 时为 0）
              'row_gap': float,       # 相邻行 gap
              'item_width': float,    # 单元宽（取均值）
              'item_height': float,   # 单元高（取均值）
              'origin_left': float,   # 网格左上角 x
              'origin_top': float,    # 网格左上角 y
              'sorted_items': [...],  # 按 row-major 排序的 items
            }
        """
        cfg = self.config
        n = len(items)
        avg_w = sum(it.width for it in items) / n
        avg_h = sum(it.height for it in items) / n
        tol = cfg.grid_position_tolerance_px

        # 1) 收集唯一的 left / top 簇（按 tol 容差合并）
        lefts = self._cluster_axis([it.left for it in items], tol)
        tops = self._cluster_axis([it.top for it in items], tol)
        cols = len(lefts)
        rows = len(tops)

        # 2) 必须 cols * rows == n（"满格"网格）
        # 这条要求保证视觉等效——如果某行少了一个，wrap 后视觉会错位。
        # 对"中间有缺口的列表"故意不识别，避免开发拿到错位的 flex-wrap。
        if cols * rows != n:
            if cfg.debug:
                print(f"    [debug] grid: cols*rows ({cols}*{rows}) != n ({n})")
            return None

        # 3) 计算 col_gap / row_gap
        col_gap = self._compute_gap(lefts, avg_w)
        row_gap = self._compute_gap(tops, avg_h)

        if col_gap is None or row_gap is None:
            return None
        if not (cfg.min_gap_px <= col_gap <= cfg.max_gap_px):
            if cfg.debug:
                print(f"    [debug] grid: col_gap {col_gap} out of range")
            return None
        if not (cfg.min_gap_px <= row_gap <= cfg.max_gap_px):
            if cfg.debug:
                print(f"    [debug] grid: row_gap {row_gap} out of range")
            return None

        # 4) 检查每个 item 的 (left, top) 都落在某个 (lefts[i], tops[j]) 簇上
        sorted_items: List[Tuple[int, int, SiblingItem]] = []
        for it in items:
            ci = self._find_cluster_idx(it.left, lefts, tol)
            ri = self._find_cluster_idx(it.top, tops, tol)
            if ci is None or ri is None:
                if cfg.debug:
                    print(f"    [debug] grid: item {it.css_class} not on grid cluster")
                return None
            sorted_items.append((ri, ci, it))

        # 5) 检查每个 (row, col) 槽位有且仅有一个 item
        slots = {(ri, ci) for ri, ci, _ in sorted_items}
        if len(slots) != n:
            if cfg.debug:
                print(f"    [debug] grid: duplicate slots ({len(slots)} unique vs {n} items)")
            return None

        # 6) 按 row-major 排序输出
        sorted_items.sort(key=lambda x: (x[0], x[1]))
        ordered = [it for _, _, it in sorted_items]

        return {
            'cols': cols,
            'rows': rows,
            'col_gap': col_gap,
            'row_gap': row_gap,
            'item_width': avg_w,
            'item_height': avg_h,
            'origin_left': lefts[0],
            'origin_top': tops[0],
            'sorted_items': ordered,
        }

    @staticmethod
    def _cluster_axis(values: List[float], tol: float) -> List[float]:
        """对一维坐标做容差聚类，返回排序后的簇中心列表"""
        if not values:
            return []
        sorted_vals = sorted(values)
        clusters: List[List[float]] = [[sorted_vals[0]]]
        for v in sorted_vals[1:]:
            if v - clusters[-1][-1] <= tol:
                clusters[-1].append(v)
            else:
                clusters.append([v])
        # 每簇用均值代表
        return [sum(c) / len(c) for c in clusters]

    @staticmethod
    def _find_cluster_idx(v: float, clusters: List[float], tol: float) -> Optional[int]:
        """返回 v 落入的簇索引，找不到返回 None"""
        for i, c in enumerate(clusters):
            if abs(v - c) <= tol:
                return i
        return None

    @staticmethod
    def _compute_gap(positions: List[float], item_size: float) -> Optional[float]:
        """从一维位置序列计算 gap

        positions = [0, 320]，item_size = 312 → gap = 320 - 0 - 312 = 8
        若只有 1 个 cluster，gap = 0（单行 / 单列）
        若有多个 cluster 但相邻间距不一致（误差 > 1px），返回 None
        """
        if len(positions) <= 1:
            return 0.0
        diffs = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
        # 检查 diffs 是否一致（±1px）
        if max(diffs) - min(diffs) > 1.5:
            return None
        avg_diff = sum(diffs) / len(diffs)
        return avg_diff - item_size

    # ------------------------------------------------------------------
    # 包裹为 v-list
    # ------------------------------------------------------------------

    def _wrap_as_list(
        self,
        container,
        items: List[SiblingItem],
        class_root: str,
        grid: Dict,
    ):
        """将 items 用一个 v-list wrapper 包起来

        步骤：
          1. 创建 wrapper <div class="v-list-N v-list" data-virtual="list"
             data-list-template="{class_root}" data-list-count="{n}"
             data-name="{class_root}-list">
          2. 把 wrapper 插入到第一个 item 在 container 中的位置
          3. 把所有 item 节点剪下来 append 到 wrapper
          4. 给 wrapper 写 CSS：position:absolute + 原 bbox + flex wrap
          5. 给每个 item 改 CSS：去掉 left/top/position，保留 width/height
        """
        # ---- 1. union bbox（wrapper 的 absolute 位置 + 尺寸）----
        min_left = min(it.left for it in items)
        min_top = min(it.top for it in items)
        max_right = max(it.right for it in items)
        max_bottom = max(it.bottom for it in items)
        union_w = max_right - min_left
        union_h = max_bottom - min_top

        # ---- 2. 创建 wrapper Tag ----
        vid = self._next_virtual_id('list')
        wrapper = self.soup.new_tag('div')
        wrapper['class'] = [vid, 'v-list']
        wrapper['data-virtual'] = 'list'
        wrapper['data-list-template'] = class_root
        wrapper['data-list-count'] = str(len(items))
        wrapper['data-name'] = f'{class_root}-list'

        # ---- 3. 插入到第一个 item 位置（按 DOM 顺序的第一个，不是 grid 顺序）----
        # 找出 items 中在 DOM 中位置最靠前的，wrapper 替它的位置；
        # 然后把所有 items 按 grid 排序（row-major）append 到 wrapper。
        dom_first = self._first_in_dom(container, items)
        dom_first.insert_before(wrapper)

        for it in grid['sorted_items']:
            it.element.extract()
            wrapper.append(it.element)

        # ---- 4. 写 wrapper CSS ----
        wrapper_css = self.css_rules.setdefault(f'.{vid}', {})
        wrapper_css['position'] = 'absolute'
        wrapper_css['left'] = f'{int(round(min_left))}px'
        wrapper_css['top'] = f'{int(round(min_top))}px'
        wrapper_css['width'] = f'{int(round(union_w))}px'
        wrapper_css['height'] = f'{int(round(union_h))}px'

        # ---- 4.1 z-index：取被包裹 items 中最大值 ----
        # wrapper 自身需要一个 z-index，否则它和容器内其它兄弟（如 bg-section、
        # 其它装饰背景）按 DOM 顺序叠序时会被高 z-index 兄弟盖住，导致整个
        # 列表"消失"。这里取所有 items 原 z-index 的最大值（保证 wrapper 至少
        # 和原视觉中最靠上的 item 同层）。items 内部的 z-index 已成为 flex/grid
        # 局部叠序，不影响这里。
        max_item_z = self._collect_max_zindex(items)
        if max_item_z is not None:
            wrapper_css['z-index'] = str(max_item_z)

        # P2a：cols≥2 且 rows≥2 时输出 CSS Grid（语义更明确、易调整）；
        # 单行/单列退化为原 flex-wrap（grid 一维没意义）。
        cfg = self.config
        cols, rows = grid['cols'], grid['rows']
        item_w = grid['item_width']
        item_h = grid['item_height']
        col_gap = grid['col_gap']
        row_gap = grid['row_gap']

        is_grid = (
            cfg.enable_css_grid
            and cols >= cfg.grid_min_cols
            and rows >= cfg.grid_min_rows
        )
        if is_grid:
            wrapper_css['display'] = 'grid'
            wrapper_css['grid-template-columns'] = (
                f'repeat({cols}, {int(round(item_w))}px)'
            )
            wrapper_css['grid-template-rows'] = (
                f'repeat({rows}, {int(round(item_h))}px)'
            )
            wrapper_css['gap'] = (
                f'{int(round(row_gap))}px {int(round(col_gap))}px'
            )
            self.stats['grid_lists_created'] = (
                self.stats.get('grid_lists_created', 0) + 1
            )
        else:
            wrapper_css['display'] = 'flex'
            wrapper_css['flex-wrap'] = 'wrap'
            wrapper_css['align-content'] = 'flex-start'
            wrapper_css['gap'] = (
                f'{int(round(row_gap))}px {int(round(col_gap))}px'
            )
        # box-sizing 与其他 wrapper 一致
        wrapper_css['box-sizing'] = 'border-box'

        # ---- 5. 改写每个 item 的 CSS ----
        # ⚠️ 关键：只删除定位属性（position/left/top），保留 width/height 和所有其他属性
        # （background/color/font-size 等必须保留，否则元素样式会丢失）
        _POSITION_ONLY_PROPS = {'position', 'left', 'top'}
        for it in items:
            item_css = self.css_rules.get(f'.{it.css_class}')
            if item_css is None:
                continue
            # 只删除绝对定位必需的三个属性
            for prop in _POSITION_ONLY_PROPS:
                item_css.pop(prop, None)
            # width / height / z-index / color / font-size 等所有其他属性原样保留
            # 这是注释第 46 行承诺的"其它属性原样保留"

        # ---- 6. 统计 ----
        self.stats['sibling_lists_created'] += 1
        self.stats['sibling_items_wrapped'] += len(items)

        layout_kw = "grid" if is_grid else "flex-wrap"
        print(
            f"    📋 v-list 创建: {class_root}-list "
            f"({grid['rows']}行×{grid['cols']}列, n={len(items)}, "
            f"row-gap={int(round(grid['row_gap']))}px, "
            f"col-gap={int(round(grid['col_gap']))}px, {layout_kw}) "
            f"-> {vid}"
        )

    @staticmethod
    def _first_in_dom(container, items: List[SiblingItem]):
        """返回 items 中在 container 直接子里位置最靠前的元素"""
        item_set = {id(it.element) for it in items}
        for child in container.find_all(recursive=False):
            if id(child) in item_set:
                return child
        # 兜底：找不到就返回第一个 item（不应发生）
        return items[0].element

    def _collect_max_zindex(self, items: List[SiblingItem]):
        """收集 items 各自 CSS 中的 z-index，返回最大值（int）。

        所有 item 都没有显式 z-index 时返回 None（表示 wrapper 也不需要写）。
        z-index 字符串无法解析为 int 时跳过该项。
        """
        max_z = None
        for it in items:
            css = self.css_rules.get(f'.{it.css_class}')
            if not css:
                continue
            raw = css.get('z-index')
            if raw is None:
                continue
            try:
                z = int(str(raw).strip())
            except (TypeError, ValueError):
                continue
            if max_z is None or z > max_z:
                max_z = z
        return max_z

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    def _next_virtual_id(self, kind: str) -> str:
        self._virtual_seq += 1
        return f'v-{kind}-{self._virtual_seq}'
