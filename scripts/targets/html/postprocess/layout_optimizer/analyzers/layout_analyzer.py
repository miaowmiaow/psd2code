"""布局特征分析器"""

from typing import List, Dict, Set, Tuple
from ..utils.css_parser import CSSParser


class LayoutAnalyzer:
    """
    分析子元素的布局特征
    
    使用"最后一个趋势元素"作为比较基准，识别元素的排列趋势
    """

    # ------------------------------------------------------------------
    # 装饰剥离阈值（V10 - 2026-04-28）
    # 用于在 trend / V8 / V9 判定前，把"纯装饰子节点"（卡片大底图、半透
    # 明角块、彩带等）从内容判定子集中移出。装饰子节点不参与 flex 排版，
    # 仍以 absolute 浮在 flex 容器之上（保留原 left/top）。
    # ------------------------------------------------------------------
    # bg：image + opacity ≥ 此值 + 任一覆盖判据
    BG_OPACITY_MIN: float = 0.95
    # bg：面积 / envelope ≥ 此值（单条命中即可）
    BG_AREA_RATIO: float = 0.85
    # bg：双轴各方向 cover ≥ 此值（与 dom_restructure 的 dual_axis 阈值一致）
    BG_DUAL_AXIS_RATIO: float = 0.8
    # decor：image + opacity < 此值
    DECOR_OPACITY_MAX: float = 0.95
    # decor：面积 / envelope < 此值
    DECOR_AREA_RATIO_MAX: float = 0.3
    # decor：与任意非 image 子节点（text/group/leaf）的"重叠 / 自身面积"
    # 必须全部 < 此值（即装饰不能压在内容上）；只看非 image 子，避免装饰
    # 之间互相覆盖时被误踢。
    DECOR_VS_CONTENT_OVERLAP_MAX: float = 0.3

    def __init__(self, css_rules: Dict[str, Dict[str, str]]):
        """
        初始化布局分析器
        
        Args:
            css_rules: CSS规则字典
        """
        self.css_rules = css_rules
        self.parser = CSSParser()
    
    def analyze_children_layout(self, children) -> dict:
        """
        分析子元素的布局特征（基于相邻元素位置变化趋势）

        V10 (2026-04-28) 算法：
        - **装饰剥离**：先把子节点分为 bg / decor / content 三类，
          只在 content 子集上做趋势检测和 V8/V9 闸门判定。
        - bg / decor 子节点一并标记到 decor_classes 输出字段，
          下游 FlexApplier 在套 flex 时会跳过它们（保留 absolute + 原 left/top）。

        历史算法（保留兼容）：
        - V7：使用"最后一个趋势元素"作为比较基准
        - V8：堆叠装饰组安全闸门（互相重叠型）
        - V9：dominant_bg 安全闸门（"中心大背景 + 周围小元素"卡片式堆叠）

        注：2026-04-30 曾引入 V11/V12 二维网格识别（layout_type='grid'）+
        前置 dominant_bg/cross_row_decor 剥离，但触发场景误识别问题较多
        （如兑奖.psd 主区域被错误地全部剥成装饰），已整体撤销，回退到 V10。

        返回：
        {
            'layout_type': 'vertical' | 'horizontal' | 'none',
            'vertical_changes': 垂直变化次数,
            'horizontal_changes': 横向变化次数,
            'all_children': 所有子元素信息列表（包含 is_trend 标记，
                            decor / bg 子节点的 is_trend 永远为 False）,
            'decor_classes': set，bg + decor 的 class 名集合，
                              下游应保留它们的 absolute 与原 left/top,
        }
        """
        # 收集所有子元素的位置信息（保持文档顺序）
        children_info = []
        for child in children:
            class_name = child.get('class', [None])[0]
            if not class_name:
                continue
            
            css = self.css_rules.get(f'.{class_name}', {})
            left = self.parser.parse_size(css.get('left', '0'))
            top = self.parser.parse_size(css.get('top', '0'))
            width = self.parser.parse_size(css.get('width', '0'))
            height = self.parser.parse_size(css.get('height', '0'))
            data_type = child.get('data-type', '')
            
            children_info.append({
                'class': class_name,
                'classes': list(child.get('class', [])),  # 全部 class（用于
                # 识别 v-stack 等 wrapper 标记，避免 flex 化时误删 position）
                'element': child,
                'left': left,
                'top': top,
                'width': width,
                'height': height,
                'data_type': data_type,
                'has_bg_image': self._has_bg_image(css),
                'opacity': self._parse_opacity(css.get('opacity', '1')),
                'is_trend': False  # 初始化为非趋势元素
            })
        
        if len(children_info) < 2:
            return {
                'layout_type': 'none',
                'vertical_changes': 0,
                'horizontal_changes': 0,
                'all_children': children_info,
                'decor_classes': set(),
            }
        
        # 🔑 V6修复：按 top 坐标排序（确保垂直趋势检测正确）
        # DOM重构后元素顺序可能不是按坐标排列的，需要重新排序
        children_info_sorted = sorted(children_info, key=lambda x: (x['top'], x['left']))

        # 🔑 V10：装饰剥离 —— 把子节点分类，只在 content 子集上做趋势 / V8 / V9
        # 装饰子节点（bg / decor）保留 absolute，由下游 FlexApplier 维持原 left/top
        bg_set, decor_set, content_list = self._classify_children(children_info_sorted)
        decor_classes = {c['class'] for c in children_info_sorted
                         if c['class'] in bg_set or c['class'] in decor_set}

        # content 子集 < 2 → 没法 flex；走 'none'，但仍输出 decor_classes 让下游知道
        if len(content_list) < 2:
            return {
                'layout_type': 'none',
                'vertical_changes': 0,
                'horizontal_changes': 0,
                'all_children': children_info_sorted,
                'decor_classes': decor_classes,
            }

        # ───────────────────────────────────────────────────────────────
        # V10 trend 检测：在 content 子集上做相邻位置变化分析
        # ───────────────────────────────────────────────────────────────
        layout_type, vertical_changes, horizontal_changes = (
            self._detect_trend_layout(content_list)
        )

        # V8/V9 安全闸门：即使 trend 检测凑出了 >= 2 次变化，仍要做"堆叠装饰组"
        # 双重保护，防止把"卡片式堆叠"误判为 flex。
        if layout_type != 'none':
            trend_ratio = (
                sum(1 for c in content_list if c['is_trend'])
                / len(content_list)
            )
            # V9：支配背景层（中心一个大背景 + 外围多个小元素叠在它上面）
            if self._has_dominant_background_overlay(content_list):
                layout_type = 'none'
            # V8：互相重叠型装饰组（多个图层两两显著重叠）
            elif trend_ratio < 0.6 and self._is_stacked_cluster(content_list):
                layout_type = 'none'

        return {
            'layout_type': layout_type,
            'vertical_changes': vertical_changes,
            'horizontal_changes': horizontal_changes,
            'all_children': children_info_sorted,  # 返回排序后的全部子（含装饰）
            'decor_classes': decor_classes,
        }

    @staticmethod
    def _parse_opacity(raw: str) -> float:
        """解析 CSS opacity，缺失/异常返回 1.0"""
        try:
            return float(str(raw).strip())
        except (ValueError, TypeError):
            return 1.0

    @staticmethod
    def _has_bg_image(css: Dict[str, str]) -> bool:
        """当前节点是否声明了可见背景图。"""
        bg = str(css.get('background-image', '') or '').strip().lower()
        if not bg or bg == 'none':
            return False
        return 'url(' in bg

    # ------------------------------------------------------------------
    # V13 (2026-04-30) trend 算法（在 V7 基础上增加投影对齐门槛）
    # ------------------------------------------------------------------

    # 趋势链投影对齐阈值（V13 - 2026-04-30）
    # 用于约束 trend 检测：垂直链相邻元素的 X 投影必须显著重叠，
    # 否则只是"Y 上下错开但 X 错位"的散落元素，不构成真正的"竖排列"。
    # 反之亦然。
    TREND_AXIS_OVERLAP_RATIO: float = 0.5

    # R02: 居中容忍（像素）。当交叉轴投影重叠不足时，若中心线接近，
    # 仍可视为同一条文档流链路。
    CENTER_ALIGN_TOLERANCE_PX: float = 56.0

    # R03: 间距一致性（变异系数上限）。用于判定等间距/近等间距序列。
    GAP_CONSISTENCY_CV_MAX: float = 0.35

    # R28: 文档流优先时，允许的两两最大重叠（按较小元素面积归一化）。
    DOC_FLOW_MAX_PAIR_OVERLAP_RATIO: float = 0.08

    def _detect_trend_layout(
        self,
        content_list: List[dict],
    ) -> Tuple[str, int, int]:
        """V13 (2026-04-30) trend 算法（在 V7 基础上增加投影对齐门槛）。

        V13 修复：
          原 V7 算法只看"top/left 是否在 prev 末尾之后"，对"分散在容器
          各处但 Y 单调下降"的元素会错串成 vertical 链。典型场景：
          天选欧皇时刻容器（726×360）有 6 个元素：title-sub @(34,31)、
          btn-unlocked @(541,26)、btn-unlock @(36,73)、avatar @(35,113)、
          group__100 @(227,124)、btn @(227,237)。剥 avatar 后剩 5 个，
          [btn-unlocked → group__100 → btn__102] 在 X 上是 [541, 227, 227]
          完全错开，但 Y 上下跳了 2 次 → 被判 vertical_changes=2，
          整组被 flex column 化，导致布局完全错位。

          V13 在串链时增加判据：相邻趋势元素必须在交叉轴投影显著重叠
          （X 投影 / min(width) ≥ TREND_AXIS_OVERLAP_RATIO），否则不入链。
          这样保证 vertical 链是真正的"上下一列"、horizontal 链是真正的
          "左右一行"。

        会就地标记 content_list 元素的 'is_trend' 字段。

        Returns:
          (layout_type, vertical_changes, horizontal_changes)
        """
        vertical_changes = 0
        horizontal_changes = 0
        last_vertical_trend_idx = None
        last_horizontal_trend_idx = None
        ratio = self.TREND_AXIS_OVERLAP_RATIO

        for idx, curr in enumerate(content_list):
            if idx == 0:
                curr['is_trend'] = True
                last_vertical_trend_idx = idx
                last_horizontal_trend_idx = idx
                continue

            if last_vertical_trend_idx is not None:
                prev = content_list[last_vertical_trend_idx]
                expected_top = prev['top'] + prev['height']
                if curr['top'] >= expected_top and self._is_axis_aligned(
                        prev, curr, axis='x', ratio=ratio):
                    vertical_changes += 1
                    curr['is_trend'] = True
                    last_vertical_trend_idx = idx

            if last_horizontal_trend_idx is not None:
                prev = content_list[last_horizontal_trend_idx]
                expected_left = prev['left'] + prev['width']
                if curr['left'] >= expected_left and self._is_axis_aligned(
                        prev, curr, axis='y', ratio=ratio):
                    horizontal_changes += 1
                    curr['is_trend'] = True
                    last_horizontal_trend_idx = idx

        layout_type = 'none'
        if vertical_changes >= 2 and vertical_changes > horizontal_changes:
            layout_type = 'vertical'
        elif horizontal_changes >= 2 and horizontal_changes > vertical_changes:
            layout_type = 'horizontal'

        # V10 特例：content 子集只有 2 个 → 至少 1 次变化即可 flex
        if layout_type == 'none' and len(content_list) == 2:
            if vertical_changes >= 1 and vertical_changes > horizontal_changes:
                layout_type = 'vertical'
            elif horizontal_changes >= 1 and horizontal_changes > vertical_changes:
                layout_type = 'horizontal'

        # R28: 文档流优先（无显著重叠 + 轴向对齐 + 间距相对一致）
        # 仅在原趋势判定失败时兜底，避免放大历史误判面。
        if layout_type == 'none':
            flow_layout = self._detect_doc_flow_priority_layout(content_list)
            if flow_layout in ('vertical', 'horizontal'):
                layout_type = flow_layout
                if flow_layout == 'vertical':
                    vertical_changes = max(vertical_changes, max(1, len(content_list) - 1))
                else:
                    horizontal_changes = max(horizontal_changes, max(1, len(content_list) - 1))
                for c in content_list:
                    c['is_trend'] = True

        return layout_type, vertical_changes, horizontal_changes

    def _is_axis_aligned(
        self,
        prev: dict,
        curr: dict,
        axis: str,
        ratio: float,
    ) -> bool:
        """判断两元素在交叉轴是否可视为同一链。

        R02 强化：
        - 优先使用投影重叠率（历史规则）
        - 若重叠不足，但中心线偏差在容忍内，也视为对齐
        """
        if self._axis_overlap_ratio(prev, curr, axis=axis) >= ratio:
            return True
        return self._axis_center_delta(prev, curr, axis=axis) <= self.CENTER_ALIGN_TOLERANCE_PX

    @staticmethod
    def _axis_center_delta(a: dict, b: dict, axis: str) -> float:
        """返回两元素在指定轴上的中心点偏差绝对值。"""
        if axis == 'x':
            ca = a['left'] + a['width'] / 2.0
            cb = b['left'] + b['width'] / 2.0
        else:
            ca = a['top'] + a['height'] / 2.0
            cb = b['top'] + b['height'] / 2.0
        return abs(ca - cb)

    def _detect_doc_flow_priority_layout(self, content_list: List[dict]) -> str:
        """R28 文档流优先兜底判定。

        目标：识别"无重叠、单向排列、轴向对齐、间距相对一致"的容器，
        允许其进入 flex 文档流。
        """
        n = len(content_list)
        if n < 2:
            return 'none'

        if self._has_heavy_pair_overlap(
            content_list,
            threshold=self.DOC_FLOW_MAX_PAIR_OVERLAP_RATIO,
        ):
            return 'none'

        vertical_ok = self._is_doc_flow_candidate(content_list, direction='vertical')
        horizontal_ok = self._is_doc_flow_candidate(content_list, direction='horizontal')

        if vertical_ok and not horizontal_ok:
            return 'vertical'
        if horizontal_ok and not vertical_ok:
            return 'horizontal'
        return 'none'

    def _is_doc_flow_candidate(self, items: List[dict], direction: str) -> bool:
        """判断给定方向是否满足 R28 文档流条件。"""
        n = len(items)
        if n < 2:
            return False

        if direction == 'vertical':
            ordered = sorted(items, key=lambda x: (x['top'], x['left']))
            main_gaps = self._compute_main_gaps(ordered, direction='vertical')
            cross_deltas = [
                self._axis_center_delta(ordered[i - 1], ordered[i], axis='x')
                for i in range(1, n)
            ]
            avg_cross_size = max(
                1.0,
                sum(max(c['width'], 1.0) for c in ordered) / n,
            )
        else:
            ordered = sorted(items, key=lambda x: (x['left'], x['top']))
            main_gaps = self._compute_main_gaps(ordered, direction='horizontal')
            cross_deltas = [
                self._axis_center_delta(ordered[i - 1], ordered[i], axis='y')
                for i in range(1, n)
            ]
            avg_cross_size = max(
                1.0,
                sum(max(c['height'], 1.0) for c in ordered) / n,
            )

        if any(g < 0 for g in main_gaps):
            return False

        # R02：交叉轴中心对齐容忍（按元素尺度自适应）
        max_center_delta = max(cross_deltas) if cross_deltas else 0.0
        center_tolerance = max(self.CENTER_ALIGN_TOLERANCE_PX, avg_cross_size * 0.6)
        if max_center_delta > center_tolerance:
            return False

        # R03：间距一致性（n>=3 更有意义）
        if len(main_gaps) >= 2:
            cv = self._coefficient_of_variation(main_gaps)
            if cv > self.GAP_CONSISTENCY_CV_MAX:
                return False

        return True

    @staticmethod
    def _compute_main_gaps(ordered: List[dict], direction: str) -> List[float]:
        """计算主轴相邻元素间距。"""
        gaps: List[float] = []
        for i in range(1, len(ordered)):
            prev = ordered[i - 1]
            curr = ordered[i]
            if direction == 'vertical':
                gaps.append(curr['top'] - (prev['top'] + prev['height']))
            else:
                gaps.append(curr['left'] - (prev['left'] + prev['width']))
        return gaps

    @staticmethod
    def _coefficient_of_variation(values: List[float]) -> float:
        """变异系数 CV = std / mean；mean<=0 时返回 +inf。"""
        if not values:
            return 0.0
        mean = sum(values) / len(values)
        if mean <= 0:
            return float('inf')
        var = sum((v - mean) ** 2 for v in values) / len(values)
        std = var ** 0.5
        return std / mean

    def _has_heavy_pair_overlap(
        self,
        items: List[dict],
        threshold: float,
    ) -> bool:
        """是否存在显著重叠的元素对。"""
        n = len(items)
        for i in range(n):
            a = items[i]
            area_a = max(1.0, a['width'] * a['height'])
            for j in range(i + 1, n):
                b = items[j]
                area_b = max(1.0, b['width'] * b['height'])
                ov = self._bbox_overlap_area(a, b)
                if ov / min(area_a, area_b) > threshold:
                    return True
        return False

    def _classify_children(
        self,
        children_info_sorted: List[dict],
    ) -> Tuple[Set[str], Set[str], List[dict]]:
        """把子节点分为 bg / decor / content 三类（V10 - 2026-04-28）

        分类规则（用户确认 2026-04-28）：
            - bg：data_type='image' + opacity ≥ BG_OPACITY_MIN +
                  (面积/envelope ≥ BG_AREA_RATIO 或 双轴 cover ≥ BG_DUAL_AXIS_RATIO)
            - decor：data_type='image' + opacity < DECOR_OPACITY_MAX +
                     面积/envelope < DECOR_AREA_RATIO_MAX +
                     与所有非 image 子节点重叠/自身面积 < DECOR_VS_CONTENT_OVERLAP_MAX
            - content：其余（含所有 text、所有 group、剩下的 image）

        Returns:
            (bg_class_set, decor_class_set, content_list)
            content_list 保留 children_info_sorted 的相对顺序（top/left 排序后）
        """
        n = len(children_info_sorted)
        if n == 0:
            return set(), set(), []

        # 容器 envelope（与 V8/V9 共用同一定义）
        xs1 = [c['left'] for c in children_info_sorted]
        ys1 = [c['top'] for c in children_info_sorted]
        xs2 = [c['left'] + c['width'] for c in children_info_sorted]
        ys2 = [c['top'] + c['height'] for c in children_info_sorted]
        env_left, env_top = min(xs1), min(ys1)
        env_right, env_bottom = max(xs2), max(ys2)
        env_w = max(0.0, env_right - env_left)
        env_h = max(0.0, env_bottom - env_top)
        env_area = max(1.0, env_w * env_h)

        bg_set: Set[str] = set()
        decor_set: Set[str] = set()

        # 第一遍：识别 bg
        for c in children_info_sorted:
            # 主路径：image 子
            # 补充路径：group 但带背景图（常见于 root 下的大底框容器）
            is_bg_like = (c['data_type'] == 'image') or bool(c.get('has_bg_image'))
            if not is_bg_like:
                continue
            if c['opacity'] < self.BG_OPACITY_MIN:
                continue
            area = max(0.0, c['width'] * c['height'])
            if area / env_area >= self.BG_AREA_RATIO:
                bg_set.add(c['class'])
                continue
            # 双轴主导覆盖（与 dom_restructure 的 _bbox_dominates_both_axes 一致）
            if env_w > 0 and env_h > 0:
                cover_w = max(0.0, min(c['left'] + c['width'], env_right) -
                              max(c['left'], env_left)) / env_w
                cover_h = max(0.0, min(c['top'] + c['height'], env_bottom) -
                              max(c['top'], env_top)) / env_h
                if (cover_w >= self.BG_DUAL_AXIS_RATIO and
                        cover_h >= self.BG_DUAL_AXIS_RATIO):
                    bg_set.add(c['class'])

        # 第二遍：识别 decor（先收集"非 image 子节点"作为 content 候选）
        non_image_children = [c for c in children_info_sorted
                              if c['data_type'] != 'image']
        for c in children_info_sorted:
            if c['class'] in bg_set:
                continue
            if c['data_type'] != 'image':
                continue
            if c['opacity'] >= self.DECOR_OPACITY_MAX:
                continue
            area = max(0.0, c['width'] * c['height'])
            if area / env_area >= self.DECOR_AREA_RATIO_MAX:
                continue
            # 与所有非 image 子的重叠都不能高于阈值（不能压在文本/group 上）
            self_area = max(1.0, area)
            heavy_overlap = False
            for other in non_image_children:
                ov = self._bbox_overlap_area(c, other)
                if ov / self_area >= self.DECOR_VS_CONTENT_OVERLAP_MAX:
                    heavy_overlap = True
                    break
            if heavy_overlap:
                continue
            decor_set.add(c['class'])

        # content = 不在 bg/decor 中的所有子（保持 sorted 顺序）
        content_list = [c for c in children_info_sorted
                        if c['class'] not in bg_set and c['class'] not in decor_set]
        return bg_set, decor_set, content_list

    @staticmethod
    def _bbox_overlap_area(a: dict, b: dict) -> float:
        """计算两个子元素 bbox 的重叠面积。"""
        ax1, ay1 = a['left'], a['top']
        ax2, ay2 = ax1 + a['width'], ay1 + a['height']
        bx1, by1 = b['left'], b['top']
        bx2, by2 = bx1 + b['width'], by1 + b['height']
        dx = max(0.0, min(ax2, bx2) - max(ax1, bx1))
        dy = max(0.0, min(ay2, by2) - max(ay1, by1))
        return dx * dy

    @staticmethod
    def _axis_overlap_ratio(a: dict, b: dict, axis: str) -> float:
        """计算 a/b 在指定轴上的投影重叠率。

        返回 重叠长度 / max(两元素该轴长度)。
        用 max 而不是 min，是为了让"窄元素恰好落在宽元素内部"
        不会得到虚高的 1.0：要求两元素在该轴长度量级相当且对齐才算高重叠。
        典型 case：窄按钮@(541,154)与宽进度条@(227,463)在 X 上有 149px 重叠，
        若用 min(154,463)=154 → 0.97（误判为"X 对齐"）；
        用 max(154,463)=463 → 0.32（拦截）。

        axis='x' → 水平投影（用 left/width）
        axis='y' → 垂直投影（用 top/height）
        """
        if axis == 'x':
            a1, a2 = a['left'], a['left'] + a['width']
            b1, b2 = b['left'], b['left'] + b['width']
            la, lb = max(1.0, a['width']), max(1.0, b['width'])
        else:
            a1, a2 = a['top'], a['top'] + a['height']
            b1, b2 = b['top'], b['top'] + b['height']
            la, lb = max(1.0, a['height']), max(1.0, b['height'])
        ov = max(0.0, min(a2, b2) - max(a1, b1))
        return ov / max(la, lb)

    def _is_stacked_cluster(self, children_info_sorted: List[dict]) -> bool:
        """
        判断子元素集合是否为"堆叠装饰组"（大多数元素互相重叠）。

        启发式规则：
        - 统计所有 pair 中存在"显著重叠"（重叠面积 > 较小元素面积的 30%）的对数。
        - 若"显著重叠对数" >= 子元素数量，则认为这是一个堆叠装饰组，
          不适合做 flex 优化。

        为什么选这个阈值：
        - n 个一行/一列排列的元素，相邻 pair 重叠≈0，显著重叠对数≈0。
        - n 个互相堆叠的装饰，每个元素与 2~3 个邻居显著重叠，
          显著重叠对数 >= n。
        """
        n = len(children_info_sorted)
        if n < 3:
            return False

        heavy_overlap_pairs = 0
        for i in range(n):
            a = children_info_sorted[i]
            area_a = max(1.0, a['width'] * a['height'])
            for j in range(i + 1, n):
                b = children_info_sorted[j]
                area_b = max(1.0, b['width'] * b['height'])
                ov = self._bbox_overlap_area(a, b)
                smaller = min(area_a, area_b)
                if ov / smaller > 0.3:
                    heavy_overlap_pairs += 1

        return heavy_overlap_pairs >= n

    def _has_dominant_background_overlay(
        self,
        children_info_sorted: List[dict],
        bg_area_ratio: float = 0.8,
        overlap_ratio: float = 0.6,
        min_other_ratio: float = 0.6,
    ) -> bool:
        """
        判断子元素集合是否为"支配背景 + 内容堆叠"形态。

        典型场景：兑奖.psd 里的 group__26 ——
        - rect-2__20 是 287×296 的大底框，几乎覆盖容器 310×323（93%）
        - 其余 5 个子元素（小矩形、图标、文字）都堆叠在底框上方

        启发式规则：
        - 存在一个 **image 子元素** X，它的面积 / 容器 envelope 面积
          ≥ bg_area_ratio（默认 80%）
        - 且其他子元素中至少 min_other_ratio 比例（默认 60%）显著落在 X 内部
          （单方向重叠面积 / 自身面积 ≥ overlap_ratio）
        → 判定 X 是"支配背景层"，整组属于堆叠装饰

        为什么需要这条规则：
        - `_is_stacked_cluster` 的"互相重叠对数 >= n"对"中心一个大背景层 +
          外围多个不重叠的小元素"不敏感（背景独自贡献 n-1 对，但小元素之间
          重叠为 0，永远凑不到 n 对）。
        - 这条规则专门拦截这种"卡片式"堆叠。

        V10 (2026-04-28) 重要修正：candidate 必须是 image
        - 历史 bug：兑奖.psd 外层 prop__30 = [group__26 (310×323=98.6%env) +
          btn__29] —— group__26 是 `data_type='group'`（内容容器），
          原算法把它当 candidate，导致 V9 误触发，prop__30 被回退 'none'。
        - 与 `_classify_children` 的 bg 识别原则一致：bg 只接受 image，
          group/text 永不算 bg；group 即便覆盖率高也是"内容容器自带大背景"，
          属于子集排版的对象，不应整组拦截。
        """
        n = len(children_info_sorted)
        # 至少 2 个子才有"背景 + 内容"概念。
        # （V10 把 2 元素 flex 也放开了，V9 也要相应放开 n >= 2，否则
        #  prop__30 = [group__26 + btn__29] 这种"大底容器 + 按钮"无法被拦截/通过判定。）
        if n < 2:
            return False

        # 容器 envelope = 所有子元素 bbox 的并集外接矩形
        xs1 = [c['left'] for c in children_info_sorted]
        ys1 = [c['top'] for c in children_info_sorted]
        xs2 = [c['left'] + c['width'] for c in children_info_sorted]
        ys2 = [c['top'] + c['height'] for c in children_info_sorted]
        env_area = max(1.0, (max(xs2) - min(xs1)) * (max(ys2) - min(ys1)))

        for i, candidate in enumerate(children_info_sorted):
            # V10：candidate 必须是 image（与装饰剥离的 bg 判定原则一致，
            # 防止把"内容容器自带大背景图层"的内层 group 误当支配背景）
            if candidate.get('data_type') != 'image':
                continue
            cand_area = candidate['width'] * candidate['height']
            if cand_area / env_area < bg_area_ratio:
                continue
            # 统计其余子元素中"显著落在 candidate 内部"的比例
            inside_count = 0
            for j, other in enumerate(children_info_sorted):
                if i == j:
                    continue
                other_area = max(1.0, other['width'] * other['height'])
                ov = self._bbox_overlap_area(candidate, other)
                if ov / other_area >= overlap_ratio:
                    inside_count += 1
            if inside_count / (n - 1) >= min_other_ratio:
                return True
        return False
    
    def calculate_signature(self, element) -> str:
        """
        计算元素的结构签名（用于识别相似结构）
        
        Args:
            element: BeautifulSoup元素
        
        Returns:
            结构签名字符串
        """
        import re
        
        # 子元素数量
        children = element.find_all(recursive=False)
        child_count = len(children)
        
        # 子元素类型
        child_types = []
        for child in children:
            if child.get('data-type'):
                child_types.append(child.get('data-type'))
            elif child.name:
                child_types.append(child.name)
        
        # 背景图
        class_name = element.get('class', [None])[0]
        css = self.css_rules.get(f'.{class_name}', {})
        bg_image = css.get('background-image', '')
        
        # 提取背景图文件名
        if bg_image:
            match = re.search(r'([^/]+)\.(png|jpg|jpeg|gif)', bg_image)
            if match:
                bg_image = match.group(1)
        
        # 尺寸
        width = css.get('width', '')
        height = css.get('height', '')
        
        # 生成签名
        sig = f"{child_count}|{','.join(child_types)}|{bg_image}|{width}x{height}"
        return sig
