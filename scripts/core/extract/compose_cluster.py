# -*- coding: utf-8 -*-
"""PSD 原生合成簇 (Compose Cluster) 检测。

替代旧"三道闸门"（_is_button_group / _can_merge_group / _can_merge_group_non_text），
不再用启发式规则猜测"是否该合图"，而是基于 PSD 自身的硬性合成语义：

R1. 剪贴蒙版 (clipping=1)
    clipped 图层只能在 base 的 alpha/bbox 范围内显示，离开 base 没有意义。
    → clipped 与其下方最近的 non-clipping base 必须同簇。

R2. 非 NORMAL / 非 PASS_THROUGH 的混合模式
    SOFT_LIGHT / OVERLAY / MULTIPLY / SCREEN / COLOR_DODGE / ... 等图层
    通过混合公式修改下层结果，单独 composite 只能得到"假定底为黑/白/透明"
    的退化结果，与 PSD 实际视觉不符。
    → 该图层必须与其下方一起合成（同簇）。

R3. PASS_THROUGH 子组
    组本身不形成独立合成层，组内子图层与外部环境直接交互（特别是组内
    存在调整层或非 NORMAL 混合模式时）。
    → PASS_THROUGH 组若其内部存在"依赖外部上下文"的图层，必须与外部
      上下邻居一起合成（同簇）。

R4. 调整层 (adjustment layer)
    曲线/色阶/曝光等调整层修改下方所有图层的渲染结果，本身没有像素，
    单独 composite 没有任何视觉。
    → 必须与下方一起合成（同簇）。

R5. **浏览器 NORMAL 堆叠不可还原**（推论规则）
    上面 R1-R4 把组的直接子序列切成若干 cluster。如果存在任一 ≥2 元素的
    cluster（即 R1/R2/R3/R4 真的触发了同簇粘连），意味着"该 cluster 内
    的图层之间存在 PSD 原生的、非 NORMAL alpha 复合的视觉关系"。
    HTML/CSS 的 background/img alpha 堆叠**只能还原 NORMAL 模式**，因此
    这种组**整体**应作为单张 PNG 合成。
    → 整组用 PSD 自身 composite 渲染（保留 PSD 原生混合视觉）；其内部
      的文本图层若存在则单独保留可访问性。

算法把组的直接子序列分成若干 cluster，每个 cluster 是"必须一起 composite"
的最小单位。判定"是否合图"由这些 cluster 推导，**不再有"按钮关键词""文本
数量"等启发式**。

判定结果的语义（用于 GroupHandler / ClippingGroupHandler）：

- **整组形成 1 个 cluster** + cluster 含 ≥1 文本 + ≥1 非文本
    → 触发"非文本合并为背景图 + 文本独立"（visual_merge_with_text_kept）
- **整组形成 1 个 cluster** + cluster 全是非文本（含子组内的非文本）
    → 触发"整组合并为单图"（visual_merge_full）
- **整组形成 1 个 cluster** + cluster 全是文本
    → 不合图（每个文本独立）
- **整组形成 ≥2 个 cluster** 中**有**任一 ≥2 元素 cluster（R5 触发）
    → 整组合并：含递归文本 → merge_with_text_kept；不含 → merge_full
- **整组形成 ≥2 个 cluster** 但**全部**是单元素 cluster
    → 不合图（递归处理每个独立子项）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class LayerKind(Enum):
    """图层在合成意义上的种类。"""
    TEXT = "text"
    PIXEL = "pixel"           # 普通像素图层 / shape / smartobject
    GROUP = "group"           # 子组（NORMAL 等独立合成模式）
    PASS_THROUGH_GROUP = "pass_through_group"
    ADJUSTMENT = "adjustment"  # 调整层（曲线/色阶/曝光/...）


# -- PSD blend mode 字符串识别 ----------------------------------------------

#: psd-tools 的 BlendMode str() 形如 "BlendMode.NORMAL"
def _bm_str(layer: Any) -> str:
    return str(getattr(layer, 'blend_mode', '')).upper()


def _is_pass_through(layer: Any) -> bool:
    return 'PASS_THROUGH' in _bm_str(layer)


def _is_normal_blend(layer: Any) -> bool:
    """是否为 NORMAL 或 DISSOLVE（语义上"不依赖下层")。"""
    bm = _bm_str(layer)
    # PASS_THROUGH 单独走另一条规则
    if 'PASS_THROUGH' in bm:
        return False
    return ('NORMAL' in bm) or ('DISSOLVE' in bm) or bm.endswith('.NORMAL') or bm == ''


def _is_clipping(layer: Any) -> bool:
    return (
        hasattr(layer, '_record')
        and hasattr(layer._record, 'clipping')
        and layer._record.clipping == 1
    )


def _is_text(layer: Any) -> bool:
    kind = str(getattr(layer, 'kind', '') or '').lower()
    return 'type' in kind


def _is_adjustment(layer: Any) -> bool:
    """调整层判定。

    psd-tools 中调整层的 kind 字符串通常包含 'adjustment'，
    也可能是具体类型如 'curves' / 'levels' / 'brightness' / 'hue_saturation' /
    'color_balance' / 'exposure' / 'photo_filter' / 'channel_mixer' / ...
    用 bbox 是否为空 (0,0,0,0) 作辅助信号（调整层无像素）。
    """
    kind = str(getattr(layer, 'kind', '') or '').lower()
    if 'adjust' in kind:
        return True
    adjustment_keywords = (
        'curves', 'levels', 'brightnesscontrast', 'brightness_contrast',
        'huesaturation', 'hue_saturation', 'colorbalance', 'color_balance',
        'exposure', 'photofilter', 'photo_filter', 'channelmixer',
        'channel_mixer', 'invert', 'posterize', 'threshold', 'gradientmap',
        'gradient_map', 'selectivecolor', 'selective_color', 'vibrance',
        'blackandwhite', 'black_and_white', 'colorlookup', 'color_lookup',
    )
    if any(k in kind for k in adjustment_keywords):
        return True
    # 兜底：bbox 完全为零 + 不是 group 不是 text → 高度怀疑是调整层
    try:
        bbox = layer.bbox
        if (bbox[0] == bbox[2] == 0 and bbox[1] == bbox[3] == 0
                and not getattr(layer, 'is_group', lambda: False)()
                and not _is_text(layer)):
            return True
    except Exception:
        pass
    return False


def _is_group_recursively_empty(group_layer: Any) -> bool:
    """递归判断一个组是否完全没有可见像素贡献。

    "递归为空"定义：组内所有子层（递归展开）都不产生任何视觉像素。
    具体地，对每个子层：
      - 不可见 / opacity=0 → 无贡献
      - 调整层 → 自身无像素（仅修改下方颜色），视为无贡献
      - 文本层 / 像素层 → 有贡献（返回 False）
      - 子组 → 递归检查

    典型案例：空的 PASS_THROUGH 组 + opacity<255 会在 psd-tools composite 中
    因 shape_g=0 → divide-by-zero → clip(1.0) 产生白色污染。这类组在独立
    cluster 合成时应被排除。
    """
    try:
        children = list(group_layer)
    except Exception:
        return True

    for c in children:
        if not getattr(c, 'visible', True):
            continue
        if getattr(c, 'opacity', 255) == 0:
            continue
        if _is_adjustment(c):
            # 调整层自身无像素
            continue
        if getattr(c, 'is_group', lambda: False)():
            if not _is_group_recursively_empty(c):
                return False
        else:
            # 叶子图层（text / pixel / shape / smartobject）→ 有像素
            return False
    return True


def _classify_layer(layer: Any) -> LayerKind:
    """单图层在合成图谱里的分类。"""
    if getattr(layer, 'is_group', lambda: False)():
        if _is_pass_through(layer):
            return LayerKind.PASS_THROUGH_GROUP
        return LayerKind.GROUP
    if _is_adjustment(layer):
        return LayerKind.ADJUSTMENT
    if _is_text(layer):
        return LayerKind.TEXT
    return LayerKind.PIXEL


def _group_contains_context_dependent(group_layer: Any) -> bool:
    """递归判断组内是否含"依赖外部上下文"的图层。

    一个图层若其视觉贡献会"穿透"PASS_THROUGH 组边界、与组外像素发生混合，
    则它使所在 PT 组成为"上下文依赖"。

    判定（精细化版，2026-05-27 修订；基于两个真实 PSD 230 个 PT 组的实证）：

    NORMAL 组（非 PASS_THROUGH）形成独立合成层 → 内部依赖不外溢 → 跳过。
    PASS_THROUGH 子组 → 递归判定。

    对叶子图层，触发条件为以下任一：

      C1) 调整层（adjustment layer）
          调整层无 alpha 限制，作用于下方所有可见像素，必然依赖外部上下文。

      C2) 非 NORMAL blend 且非 clipping
          没有 alpha 限定，blend 会与同父 sibling 中下方所有可见像素混合，
          跨越组边界。

      C3) 非 NORMAL blend + clipping=1，但 clip base 是"组"（PT 或 NORMAL）
          base 是子组合成结果，子组本身可能含 PT/调整等复杂语义，保守认为
          有外溢。

      C4) 非 NORMAL blend + clipping=1，但找不到 clip base sibling
          跨组剪贴或剪贴链断裂，异常情况，保守。

      C5) 剪贴层（NORMAL 或非 NORMAL 皆可）且其下方没有 non-clipping sibling
          跨组剪贴异常（与旧逻辑等价，保留）。

    安全降级场景（**不**触发，即便是非 NORMAL blend）：
      非 NORMAL blend + clipping=1 + clip base 是同父的 pixel sibling。
      此时该 blend 的视觉贡献被 base.alpha 严格锁定在组内，不会外溢。
      典型反例：宝箱内 OVERLAY 高光层叠在圆角矩形 pixel base 上。
    """
    try:
        children = list(group_layer)
    except Exception:
        return False

    has_non_clip_below = False
    for i, c in enumerate(children):
        if not getattr(c, 'visible', True) or getattr(c, 'opacity', 255) == 0:
            continue
        if c.is_group():
            if _is_pass_through(c):
                # 嵌套 PASS_THROUGH 组：递归检查
                if _group_contains_context_dependent(c):
                    return True
            else:
                # NORMAL 等独立合成组：不向外暴露内部依赖
                continue
            # 组本身也作为 non-clip sibling 占位
            if not _is_clipping(c):
                has_non_clip_below = True
            continue

        # —— 叶子图层 —— #

        # C1: 调整层
        if _is_adjustment(c):
            return True

        clip = _is_clipping(c)
        normal = _is_normal_blend(c)

        if not normal:
            # 非 NORMAL blend 路径
            if not clip:
                # C2: 非 clipping → 无 alpha 限定 → 必然外溢
                return True
            # clipping=1：找下方第一个非 clipping、visible、opacity>0 的 sibling 作为 clip base
            base = None
            for j in range(i - 1, -1, -1):
                sib = children[j]
                if not getattr(sib, 'visible', True):
                    continue
                if getattr(sib, 'opacity', 255) == 0:
                    continue
                if _is_clipping(sib):
                    continue
                base = sib
                break
            if base is None:
                # C4: 找不到 base
                return True
            if base.is_group():
                # C3: base 是组（PT/NORMAL 都保守拒绝）
                return True
            # 安全降级：base 是 pixel sibling → 视觉贡献被锁在组内 → 不触发
            # 不更新 has_non_clip_below（clipping 层本身不是 non-clip base）
            continue

        # NORMAL blend 路径
        # C5: NORMAL 剪贴层 + 下方无 non-clip sibling → 跨组剪贴异常
        if clip and not has_non_clip_below:
            return True
        if not clip:
            has_non_clip_below = True
    return False


# -- Cluster 数据结构 -------------------------------------------------------


@dataclass
class ComposeCluster:
    """一个"必须一起 composite"的图层簇。

    members 严格按 PSD 子图层顺序（自底向上，与 PSD 内部顺序一致：
    PSDImage 迭代是 bottom-first；psd-tools `for c in group` 给出的是
    自底向上的顺序）。
    """
    members: list[Any] = field(default_factory=list)
    #: 触发同簇粘连的原因汇总，调试日志用
    reasons: list[str] = field(default_factory=list)

    def add(self, layer: Any, reason: str = "") -> None:
        self.members.append(layer)
        if reason:
            self.reasons.append(reason)

    def is_singleton(self) -> bool:
        return len(self.members) == 1

    def text_count(self) -> int:
        return sum(1 for m in self.members if _is_text(m))

    def non_text_count(self) -> int:
        """非文本成员数（含组、调整层；调整层视作非视觉但同簇粘连过来的）。"""
        return sum(1 for m in self.members if not _is_text(m))

    def visual_non_text_count(self) -> int:
        """有视觉像素的非文本成员（排除调整层 / 空 group）。"""
        n = 0
        for m in self.members:
            if _is_text(m):
                continue
            if _is_adjustment(m):
                continue
            try:
                bbox = m.bbox
                if bbox[2] - bbox[0] <= 0 or bbox[3] - bbox[1] <= 0:
                    continue
            except Exception:
                pass
            n += 1
        return n


# -- 核心算法：把直接子序列切成 cluster -------------------------------------


def detect_compose_clusters(group_layer: Any) -> list[ComposeCluster]:
    """对组的"直接子图层"（按 PSD 自底向上顺序）做合成簇检测。

    返回的 cluster 列表保持 PSD 顺序（cluster[0] 在最底）。

    隐藏 / opacity=0 子图层被过滤（不参与 cluster 划分；它们既不渲染也
    不影响合成关系）。
    """
    children: list[Any] = []
    try:
        for c in group_layer:
            if not getattr(c, 'visible', True):
                continue
            if getattr(c, 'opacity', 255) == 0:
                continue
            # B方案：排除递归为空的组——它们不产生任何可见像素，
            # 但在独立 cluster 合成时会因 psd-tools 的 divide-by-zero
            # （shape_g=0 → clip(1.0)）产生白色污染。
            if (getattr(c, 'is_group', lambda: False)()
                    and _is_group_recursively_empty(c)):
                continue
            children.append(c)
    except Exception:
        return []

    if not children:
        return []

    clusters: list[ComposeCluster] = []
    current: Optional[ComposeCluster] = None

    def open_new_cluster() -> ComposeCluster:
        c = ComposeCluster()
        clusters.append(c)
        return c

    for layer in children:
        kind = _classify_layer(layer)

        # R1: 剪贴层 → 必须与下方最近的 non-clipping 同簇
        if _is_clipping(layer):
            if current is None:
                # 孤立剪贴（没有下方 base）→ 单独成簇兜底
                current = open_new_cluster()
            current.add(layer, "R1: clipping")
            continue

        # 否则：先看是否要"扩展进上一簇"还是"开新簇"
        merge_with_prev = False
        merge_reason = ""
        merge_absorb_all_below = False  # 是否需要回吸所有此前开过的 cluster

        # R2: 非 NORMAL 非 PASS_THROUGH 混合模式 → 必须与下方一起合成
        if kind != LayerKind.PASS_THROUGH_GROUP and not _is_normal_blend(layer):
            merge_with_prev = True
            merge_reason = f"R2: blend={_bm_str(layer)}"
            # 关键：非 clipping 的非 NORMAL blend（如 LINEAR_DODGE/OVERLAY 等
            # 处于 PT 组直接子层、不 clip）其合成对象是"PT 组在它下方的全部
            # 已合成像素"——必须回吸所有此前开过的 cluster，否则浏览器 NORMAL
            # 堆叠时下方独立 cluster 没参与 blend，渲染偏离 PSD 真值。
            # 抽奖活动 PSD "人物" 组的 图层 647 (LINEAR_DODGE) 即此类案例。
            # clipping 的非 NORMAL blend 由 R1 处理（已锁在下方 base 上）。
            if not _is_clipping(layer):
                merge_absorb_all_below = True

        # R4: 调整层 → 仅当下方 cluster 已被 R1/R2/R3 锁定要合图时才合并；
        # 否则下方是 independent 簇（浏览器能 NORMAL 堆叠还原），不下扑，
        # 让调整层自己单独成簇（后续渲染时若无可作用对象会被丢弃）。
        if kind == LayerKind.ADJUSTMENT:
            prev_locked = (
                current is not None
                and any(r != "independent" for r in current.reasons)
            )
            if prev_locked:
                merge_with_prev = True
                merge_reason = "R4: adjustment"
                # 调整层语义同 R2 非 clip：作用于"下方所有像素"
                merge_absorb_all_below = True

        # R3: PASS_THROUGH 组且组内"内含上下文依赖" → 与下方/上方同簇
        if kind == LayerKind.PASS_THROUGH_GROUP:
            if _group_contains_context_dependent(layer):
                merge_with_prev = True
                merge_reason = "R3: pass_through_group_with_dependency"

        if merge_with_prev and current is not None:
            if merge_absorb_all_below and len(clusters) > 1:
                # 回吸：把此前所有 cluster 合到 current
                absorbed = ComposeCluster()
                for prev_cl in clusters:
                    for m in prev_cl.members:
                        absorbed.members.append(m)
                    for r in prev_cl.reasons:
                        if r not in absorbed.reasons:
                            absorbed.reasons.append(r)
                absorbed.add(layer, merge_reason)
                if "R2-absorb-below" not in absorbed.reasons:
                    absorbed.reasons.append("R2-absorb-below")
                clusters.clear()
                clusters.append(absorbed)
                current = absorbed
            else:
                current.add(layer, merge_reason)
            continue

        # 新开一个 cluster
        current = open_new_cluster()
        current.add(layer, "independent")

    return clusters


# -- 高层判定（供 LayerExporter 调用） ---------------------------------------


@dataclass
class GroupComposeDecision:
    """对一个 group 的合图决策结果。

    Fields:
        action: "merge_full" / "merge_with_text_kept" / "merge_partial" / "no_merge"
        clusters: 该组的直接子 cluster 列表
        cluster: 若 action ∈ {merge_full, merge_with_text_kept}（单 cluster 路径），
            这是该唯一 cluster
        text_layers: merge_with_text_kept 时要单独保留的文本图层（来自整组递归）
        merged_clusters: 仅 action="merge_partial" 使用。**每个 glued cluster
            一个 list**，每个 list 内成员是该 cluster 的直接子图层（按 PSD 顺序）。
            调用方应**为每个 list 单独合成一张背景图**——不同 cluster 之间是
            NORMAL alpha 堆叠（浏览器可还原），不能合到同一张图里。
        merged_layers: 仅 action="merge_partial" 使用。**扁平化**的所有
            glued cluster 成员（= 所有 merged_clusters 的拼接），仅供"判断
            某直接子是否属于合图集合"等需要 O(1) 查询的下游使用；**不要**
            用它去合成（会把多个独立 glued cluster 错合成一张大图）。
        independent_layers: 仅 action="merge_partial" 使用。保留独立递归处理
            的"直接子图层"集合（=所有 singleton independent cluster 的成员，
            按 PSD 顺序）。
    """
    action: str
    clusters: list[ComposeCluster]
    cluster: Optional[ComposeCluster] = None
    text_layers: list[Any] = field(default_factory=list)
    merged_clusters: list[list[Any]] = field(default_factory=list)
    merged_layers: list[Any] = field(default_factory=list)
    independent_layers: list[Any] = field(default_factory=list)


def _has_recursive_text(layer: Any) -> bool:
    """递归检查图层（含子组）是否含可见文本图层。"""
    if not getattr(layer, 'visible', True):
        return False
    if getattr(layer, 'opacity', 255) == 0:
        return False
    if _is_text(layer):
        return True
    if getattr(layer, 'is_group', lambda: False)():
        try:
            for c in layer:
                if _has_recursive_text(c):
                    return True
        except Exception:
            pass
    return False


def _collect_recursive_text(layer: Any, out: list[Any]) -> None:
    """递归收集所有可见文本图层。"""
    if not getattr(layer, 'visible', True):
        return
    if getattr(layer, 'opacity', 255) == 0:
        return
    if _is_text(layer):
        out.append(layer)
        return
    if getattr(layer, 'is_group', lambda: False)():
        try:
            for c in layer:
                _collect_recursive_text(c, out)
        except Exception:
            pass


def _has_clipping_over_text_base(group_layer: Any) -> bool:
    """递归检查组内是否存在"clipping layer 剪贴到含文本（递归）base"的情形。

    PSD clipping 规则：在同一父组的 sibling 序列里（psd-tools 迭代顺序 = 从底
    到顶），连续的 clip=1 图层共享同一个剪贴 base = 它们下方第一个 clip=0 的
    sibling。当该 base 自身是组、且组内（递归）含可见文本时：剪贴效果作用
    于"组合成结果（含文字 alpha）"上，文字的视觉与剪贴层的颜色/辉光像素直接
    耦合，DOM 中再独立渲染该文本就会与背景图叠加错位（典型：黄色辉光的
    文字部分 + 纯白 DOM 文本 = 视觉不一致）。

    满足该结构 → 调用方应升级为 merge_full（文本一并烧进合成图）。

    递归检查整组所有子组（嵌套结构里任一层级触发即返回 True）。
    """
    if not getattr(group_layer, 'is_group', lambda: False)():
        return False
    try:
        children = list(group_layer)
    except Exception:
        return False

    # 遍历当前组的直接 sibling 序列（从底到顶）
    # 依次找 clip=1 段，回溯下方第一个 clip=0 sibling 作为 base
    for i, ch in enumerate(children):
        if not getattr(ch, 'visible', True):
            continue
        if getattr(ch, 'opacity', 255) == 0:
            continue
        if not _is_clipping(ch):
            continue
        # 向下（更早的 sibling）找第一个 clip=0 的 base
        base: Any = None
        for j in range(i - 1, -1, -1):
            cand = children[j]
            if not getattr(cand, 'visible', True):
                continue
            if getattr(cand, 'opacity', 255) == 0:
                continue
            if _is_clipping(cand):
                continue
            base = cand
            break
        if base is None:
            continue
        # base 必须是组，并且组内（递归）含可见文本
        if not getattr(base, 'is_group', lambda: False)():
            continue
        if _has_recursive_text(base):
            return True

    # 递归子组
    for ch in children:
        if not getattr(ch, 'visible', True):
            continue
        if getattr(ch, 'opacity', 255) == 0:
            continue
        if getattr(ch, 'is_group', lambda: False)():
            if _has_clipping_over_text_base(ch):
                return True
    return False


def decide_group_merge(group_layer: Any) -> GroupComposeDecision:
    """根据 PSD 原生合成簇决定一个组的合图策略。

    输入：PSD 中一个"组"对象（layer.is_group() == True）。

    输出：
      - action="merge_full"
            (a) 整组只有一个 cluster，且不含文本；或
            (b) 单 cluster + 含文本 但触发了"clipping over text base"升级
            → 整组 composite 为单图。
      - action="merge_with_text_kept"
            整组只有一个 cluster，且含 ≥1 文本 + ≥1 非文本视觉成员，
            且未触发 "clipping over text base" 升级
            → 非文本部分 composite 为背景图，文本独立保留。
      - action="merge_partial"
            ≥2 cluster 且存在 ≥2 元素的 glued cluster（R5 触发）。
            **仅** glued cluster 内的成员需要合成（这些成员之间存在 PSD
            原生非 NORMAL 合成关系，浏览器无法 NORMAL 堆叠还原）；
            singleton independent cluster 之间是 NORMAL 堆叠，浏览器可还原，
            **不被卷入合图**，保留独立递归处理。
            **每个 glued cluster 独立合成一张图**——多个 glued cluster 之间
            （被 singleton independent cluster 隔开）也是 NORMAL 堆叠，
            浏览器可还原，不应合到同一张大图里。
            特殊：若 glued cluster 触发 "clipping over text base" 升级，
            退化为 merge_full（保守起见，把整组合一张包含文本，避免脱节）。
      - action="no_merge"
            其他情况（唯一 cluster 全文本；唯一单元素 cluster；多个全单元素
            cluster；空组）→ 不合图，常规递归 / 逐层导出。
    """
    clusters = detect_compose_clusters(group_layer)

    if not clusters:
        return GroupComposeDecision(action="no_merge", clusters=[])

    # ── R5：有 ≥2 cluster 时，检查是否存在 ≥2 元素 cluster（粘连关系）──
    # 只要存在任意一个 ≥2 元素 cluster，意味着 R1/R2/R3/R4 触发了同簇粘连，
    # 浏览器 NORMAL 堆叠无法还原视觉，**该 cluster** 应合一张图。
    # **重要**：singleton independent cluster 之间本来就是 NORMAL alpha 堆叠，
    # 浏览器完美还原，**不应**被卷入合图（这是 partial 路径的核心动机）。
    if len(clusters) >= 2:
        glued_clusters = [
            c for c in clusters
            if (not c.is_singleton()) and (c.visual_non_text_count() >= 1)
        ]
        if glued_clusters:
            # 注：旧版在此曾有"clipping over text base → 升级为 merge_full"
            # 的全组级保守升级，已移除（2026-05-27）。
            # 原因：clipping-over-text-base 是一个**局部**视觉耦合现象——
            #   (a) 若触发位置在某 glued cluster 的成员范围内（典型：调整层
            #       剪贴到含文本子组，如"色相/饱和度 5" → "按钮-置灰" 组），
            #       该 cluster 自身就会合成一张含文本的图，局部内部一致，
            #       partial 路径已自然处理；
            #   (b) 若触发位置在某 singleton independent sibling 的嵌套子组
            #       内部，由该子组被递归调用 GroupHandler 时由内层
            #       decide_group_merge 自行升级 merge_full，外层无需关心。
            # 因此在 partial 路径全组级升级 merge_full，会错误地把无关的、
            # 完全可以独立 DOM 化的 N 个 independent sibling（含其文本/按钮）
            # 一起烧成 PNG，损失大量可访问语义（典型案例：魔界人幸运签
            # "4 拷贝 2" 顶部信息卡片整组被错误烧成单张 img-8019d2.png）。

            # 收集要合成的层（按 glued cluster 分组，**每个 cluster 单独合一张**）
            # 和要保留独立的层（singleton independent cluster 的唯一成员）。
            # 顺序按 PSD 自底向上（即原 cluster 列表顺序 + 簇内成员顺序）。
            #
            # ⚠️ 关键设计：merged_clusters 是 list[list]，**不是**扁平 list。
            # 多个 glued cluster 之间被 singleton independent cluster 隔开，
            # 它们彼此之间是 NORMAL alpha 堆叠（浏览器可还原），如果一起合
            # 成一张大图会跨越非合图区域，导致 z 序错乱 / 中间独立子被吃掉
            # 而无法独立 DOM 化。
            merged_clusters: list[list[Any]] = []
            merged_layers: list[Any] = []
            independent_layers: list[Any] = []
            for c in clusters:
                if c in glued_clusters:
                    merged_clusters.append(list(c.members))
                    merged_layers.extend(c.members)
                else:
                    # singleton independent → 独立递归
                    independent_layers.extend(c.members)

            return GroupComposeDecision(
                action="merge_partial", clusters=clusters, cluster=None,
                merged_clusters=merged_clusters,
                merged_layers=merged_layers,
                independent_layers=independent_layers,
            )

        # 全是单元素 cluster（独立非文本子项）→ 各自递归处理
        return GroupComposeDecision(action="no_merge", clusters=clusters)

    only = clusters[0]

    # 单元素 cluster：交给上层正常处理（叶图层 / 子组各自走 handler）
    if only.is_singleton():
        return GroupComposeDecision(
            action="no_merge", clusters=clusters, cluster=only
        )

    text_layers = [m for m in only.members if _is_text(m)]
    visual_non_text = only.visual_non_text_count()

    # 全文本：不合图（文本各自独立导出）
    if visual_non_text == 0:
        return GroupComposeDecision(
            action="no_merge", clusters=clusters, cluster=only,
            text_layers=text_layers,
        )

    # 有文本 + 有非文本视觉 → 文本独立，非文本合成
    if text_layers:
        # ⭐ 升级规则：组内存在 "clipping 层剪贴到含文本 base"
        # → 文本必须烧进合成图，否则 DOM 文本与剪贴效果脱节
        if _has_clipping_over_text_base(group_layer):
            return GroupComposeDecision(
                action="merge_full", clusters=clusters, cluster=only,
            )
        return GroupComposeDecision(
            action="merge_with_text_kept", clusters=clusters,
            cluster=only, text_layers=text_layers,
        )

    # 单 cluster 直接成员里没有 TypeLayer，但成员组里**递归**可能藏着文本
    # （典型：根 group 的直接子是 NORMAL 模式的 wrapper group，wrapper 内
    # 才有 TypeLayer）。此时若降级 merge_full 会把文字一起烧进 PNG，DOM
    # 失去可访问文本。改为递归收集文本，按 merge_with_text_kept 保留。
    recursive_texts: list[Any] = []
    for m in only.members:
        if _is_text(m):
            # 已在上面 text_layers 分支收集，保险起见
            recursive_texts.append(m)
        elif getattr(m, 'is_group', lambda: False)():
            _collect_recursive_text(m, recursive_texts)
    if recursive_texts:
        # 同样需要 clipping-over-text-base 升级保护
        if _has_clipping_over_text_base(group_layer):
            return GroupComposeDecision(
                action="merge_full", clusters=clusters, cluster=only,
            )
        return GroupComposeDecision(
            action="merge_with_text_kept", clusters=clusters,
            cluster=only, text_layers=recursive_texts,
        )

    # 全是非文本视觉 → 整组合一张图
    return GroupComposeDecision(
        action="merge_full", clusters=clusters, cluster=only,
    )


def describe_decision(decision: GroupComposeDecision) -> str:
    """生成可打印的描述字符串，便于调试日志。"""
    n = len(decision.clusters)
    parts = [f"clusters={n}"]
    for i, c in enumerate(decision.clusters):
        parts.append(
            f"#{i}[len={len(c.members)},text={c.text_count()},"
            f"vnt={c.visual_non_text_count()}]"
        )
    if decision.action == "merge_partial":
        parts.append(
            f"merged={len(decision.merged_layers)}"
            f"({len(decision.merged_clusters)}组),"
            f"indep={len(decision.independent_layers)}"
        )
    return f"{decision.action} ({', '.join(parts)})"
