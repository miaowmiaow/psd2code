"""位置噪声宽容合并（PositionNoiseRelaxer）— 同 base + 非位置签名相同 → 归一到代表样式。

为什么需要这一步
================

PSD 设计稿里的"列表项"（如排行榜 11 个昵称、宝箱 6 个卡片）在视觉上是同一类，
但每个实例由 PSD 图层独立摆放，从而产生**轻微位置偏差**：

- ``margin-top: 21px / 22px / 24px / 26px`` —— 设计师手摆的纵向间距抖动
- ``z-index: 37 / 45 / 53 / 61 / ...`` —— 全局 layer_id，递增噪声
- ``margin-left: 54px / 54px / 0`` —— 个别成员"差点没对齐"

这些差异**本质**上是设计稿生产噪声，不是设计意图。但 ``CssDedup`` 的等价比较
是逐字段精确匹配，会把这些"本质相同 + 位置抖动" 的规则视为不同 → 11 个
``nickname-N`` 各自独立 → ``RepeatClassUnifier`` 因 ``min_unify_count >= 3``
也无法合并 → ``SemanticClassRename`` 只能加 ``-2 / -3 / ... -11`` 后缀。

最终用户体验：

- HTML 里 11 个不同 hash 类 + CSS 里 11 条几乎一样的规则，可读性极差；
- 想给"所有 nickname 加 hover 阴影"，要逐条改 11 处样式；
- 设计稿原本就是"复制 11 次"的列表，工程语义上应当复用单一类。

修复策略（**牺牲位置精度换样式复用**）
======================================

**触发条件**（同时满足）：

1. 组内成员形如 ``.<base>__<digits>``（SimpleNamer 产出）；
2. 全部成员**不含** ``top / left / right / bottom``（即非 absolute 定位 ——
   absolute 元素的 top/left 是位置关键，绝不能丢）；
3. 排除位置噪声属性后，所有成员的属性签名**完全相同**；
4. 组内成员数 ≥ ``min_unify_count``（默认 3）；
5. margin 偏差极差 ≤ ``max_margin_drift_px``（默认 8px）—— 防止"文档头 vs 脚部"
   这种实际相距 100px+ 的元素被强行合并，伤及视觉。

**归一化规则**（位置噪声字段）：

- ``margin-top / margin-left / margin-right / margin-bottom`` → 取众数（mode），
  平局时取首成员的值；
- ``z-index`` → 全删（流式 flex 布局根本不依赖 z-index；CssDedup Pass 1 已删
  大部分，这里兜底处理 v-stack 子元素等少数残留）；

归一化后，子组内成员的属性 dict 逐字相等 → 把整组写入 ``stats['_css_merge_groups']``，
让下游 ``RepeatClassUnifier`` 直接接管"3+ 等价 → 单一语义类"折叠。

视觉影响声明
============

本 transformer 是**目前 LayoutOptimizer 链路里唯一会引入视觉差异的步骤**。
其它所有 transformer 都保持 W3C 等价 / pixel-perfect；本 transformer 通过
牺牲 1~8px 的 margin 偏差换 N→1 的样式复用，**这是用户对"设计稿生产噪声"
的明确放弃决策**。如需 100% 像素一致，将 ``enabled`` 设为 False 即可禁用。

实测影响（2026-05-06 DNF 大逃杀手机端）：

- nickname-2..10（9 个）原本 margin-top 分布 [21,21,21,21,22,22,22,26,21]，
  极差 5px → 取众数 21px → 单成员极端值 26px 整体上移 5px；
- 整体视觉差异 ~ 0.05% 像素（亚像素级，肉眼不可察）；
- HTML 简化：``nickname / nickname-2 / ... / nickname-9`` 9 个 class →
  ``nickname-row`` 1 个 class（复用 9 次）；
- CSS 简化：9 条独立规则 → 1 条规则（约 35 行 → 6 行）。

排查提示
========

- 期望某 base 组合并但未触发：检查日志 ``位置噪声归一: ...``，常见漏触发：
  * 组内成员数 < 3（min_unify_count）
  * 成员含 top/left（不能合并）
  * 非位置签名不一致（width/height/font-size 等真实差异）
  * margin 极差 > max_margin_drift_px
- 视觉某元素位置上下抖动（差几 px）：检查它是否是被合并的 nickname-row 之类
  的成员，margin 取了众数，本来这就是预期；如要回到原始位置，临时设
  ``PositionNoiseRelaxer(enabled=False)``
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@dataclass
class PositionRelaxerConfig:
    """PositionNoiseRelaxer 的所有开关。"""

    enabled: bool = True
    # 最少多少个成员才触发位置归一（与下游 RepeatClassUnifier.min_unify_count 对齐）
    min_unify_count: int = 3
    # margin 偏差极差上限（px）：组内 margin-top 等字段的 max - min ≤ 此值才合并
    # 默认 8px：足以覆盖列表项纵向间距的设计抖动（实测 nickname 是 5px），
    # 但能拦住"完全不在一个版块"的元素被强合（间距通常 > 30px）
    max_margin_drift_px: float = 8.0
    # 哪些字段视为"位置噪声"（参与归一，不参与签名比较）
    # 注：top/left/right/bottom **不在此列**——absolute 元素必须保位置精确
    noise_props: FrozenSet[str] = field(
        default_factory=lambda: frozenset({
            "margin-top", "margin-left", "margin-right", "margin-bottom",
            "z-index",
        })
    )
    # 哪些字段直接全删（不取众数，因为完全是噪声）
    # z-index 流式布局下纯冗余；DOM 顺序天然兜底
    drop_props: FrozenSet[str] = field(
        default_factory=lambda: frozenset({"z-index"})
    )


# ---------------------------------------------------------------------------
# 选择器解析
# ---------------------------------------------------------------------------

# SimpleNamer 输出：``<base>__<id>``
_NAMED_RE = re.compile(r"^\.(?P<base>[A-Za-z][A-Za-z0-9-]*?)__(?P<id>\d+)$")

# absolute 定位关键字段（含这些 = 不能位置归一）
_ABSOLUTE_KEYS = frozenset({"top", "left", "right", "bottom"})

# 数字 + px 的 margin 值匹配：``21px`` / ``-3.5px`` / ``0`` / ``0px``
_PX_VALUE_RE = re.compile(r"^(-?\d+(?:\.\d+)?)(?:px)?$")


def _parse_named(selector: str) -> Optional[Tuple[str, str]]:
    """``.nickname__37`` → ``('nickname', '37')``，否则 None。"""
    m = _NAMED_RE.match(selector)
    if not m:
        return None
    return m.group("base"), m.group("id")


def _parse_px(value: str) -> Optional[float]:
    """``'21px'`` / ``'21'`` / ``'-3.5px'`` → 数值；不匹配返回 None。"""
    if value is None:
        return None
    m = _PX_VALUE_RE.match(str(value).strip())
    if not m:
        return None
    try:
        return float(m.group(1))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------


class PositionNoiseRelaxer:
    """位置噪声宽容合并 transformer。

    使用方式::

        relaxer = PositionNoiseRelaxer(soup, css_rules, stats, config)
        relaxer.run()  # 修改 css_rules + 写 stats['_css_merge_groups']
    """

    def __init__(
        self,
        soup,
        css_rules: Dict[str, Dict[str, str]],
        stats: Dict,
        config: Optional[PositionRelaxerConfig] = None,
    ):
        self.soup = soup
        self.css_rules = css_rules
        self.stats = stats
        self.config = config or PositionRelaxerConfig()
        self.stats.setdefault("position_relaxed_groups", 0)
        self.stats.setdefault("position_relaxed_classes", 0)

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    def run(self) -> None:
        if not self.config.enabled:
            return

        # 1) 按 base 收集所有 SimpleNamer 类
        base_to_selectors: Dict[str, List[str]] = defaultdict(list)
        for sel in list(self.css_rules.keys()):
            parsed = _parse_named(sel)
            if parsed is None:
                continue
            base, _id = parsed
            base_to_selectors[base].append(sel)

        if not base_to_selectors:
            return

        # 2) 对每个 base 组，按"非位置签名"二次分桶
        merged_groups: List[List[str]] = list(self.stats.get("_css_merge_groups") or [])
        new_merged_groups: List[List[str]] = []
        relaxed_classes_count = 0

        for base, selectors in base_to_selectors.items():
            if len(selectors) < self.config.min_unify_count:
                continue

            # 同 base 内按"非位置签名 + 位置字段存在性"分桶
            #
            # 为什么还要按"位置字段存在性"做二级分桶：
            # PSD 里同一 base 的成员常分布在**不同父容器**下：
            #   - nickname__22/27/32：父是水平 v-row（与同行兄弟排列，**无** margin-left）
            #   - nickname__37/45/...：父是 v-col 内部（独自顶格列，**有** margin-left:54px）
            # 非位置签名相同（width/height/color/font-size），但 margin-left 的
            # **存在性**不同（有 vs 无）。如果放进一个大桶，margin-drift 会被
            # 54px 撑爆；但分子桶后，前 3 个和后 6 个可以各自独立合并。
            sig_buckets: Dict[Tuple[Tuple[str, str], ...], List[str]] = defaultdict(list)
            for sel in selectors:
                props = self.css_rules.get(sel)
                if not props:
                    continue

                # 含 absolute 定位字段 → 跳过（top/left 是关键位置不能丢）
                if any(k in props for k in _ABSOLUTE_KEYS):
                    continue

                # 计算非位置签名
                non_noise = {
                    k: v for k, v in props.items()
                    if k not in self.config.noise_props
                }
                # 附加"位置字段存在性"作为签名一部分（不含具体值，只含字段名集合）
                # 这样 "(margin-top,)" 与 "(margin-top, margin-left, z-index)"
                # 分到不同桶
                noise_keys_present = tuple(sorted(
                    k for k in props.keys() if k in self.config.noise_props
                ))
                sig = (tuple(sorted(non_noise.items())), noise_keys_present)
                sig_buckets[sig].append(sel)

            # 3) 处理每个签名桶（≥ min_unify_count 才合并）
            for sig, members in sig_buckets.items():
                if len(members) < self.config.min_unify_count:
                    continue

                # 校验 margin 极差
                if not self._margin_drift_acceptable(members):
                    continue

                # sig = (non_noise_items_tuple, noise_keys_present_tuple)
                non_noise_items, _noise_keys = sig

                # 4) 归一化位置噪声字段
                normalized_props = self._normalize_noise_props(members, non_noise_items)

                # 5) 把归一化后的属性写回每个成员（让它们 dict 完全相等）
                for sel in members:
                    self.css_rules[sel] = dict(normalized_props)

                # 6) 登记到合并组（让 RepeatClassUnifier 接管）
                new_merged_groups.append(sorted(members))
                relaxed_classes_count += len(members)

        if not new_merged_groups:
            return

        # 7) 把新的合并组写入 stats（与已有合并组合并；去重以防同组重复登记）
        existing_keys: Set[FrozenSet[str]] = set()
        combined: List[List[str]] = []
        for g in merged_groups + new_merged_groups:
            key = frozenset(g)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            combined.append(g)
        self.stats["_css_merge_groups"] = combined

        # 8) 统计
        self.stats["position_relaxed_groups"] = len(new_merged_groups)
        self.stats["position_relaxed_classes"] = relaxed_classes_count

        print(
            f"   - 位置噪声归一: {len(new_merged_groups)} 组（覆盖 {relaxed_classes_count} 个类）"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _margin_drift_acceptable(self, members: List[str]) -> bool:
        """校验组内 margin-* 字段的极差是否在阈值内。

        - 对每个 margin 字段，分别计算极差；任一字段极差超阈值则拒绝合并；
        - 字段缺失视为 0px（CSS 默认）。
        """
        for prop in ("margin-top", "margin-left", "margin-right", "margin-bottom"):
            values: List[float] = []
            for sel in members:
                raw = self.css_rules.get(sel, {}).get(prop, "0")
                v = _parse_px(raw)
                if v is None:
                    # 非 px 值（如 ``auto``）——保守拒绝
                    return False
                values.append(v)
            if values and (max(values) - min(values)) > self.config.max_margin_drift_px:
                return False
        return True

    def _normalize_noise_props(
        self,
        members: List[str],
        non_noise_sig: Tuple[Tuple[str, str], ...],
    ) -> Dict[str, str]:
        """把组内成员的位置噪声字段归一为代表值，返回归一后的完整属性 dict。

        策略：
        - 非位置字段：直接来自 ``non_noise_sig``（已是组共识）；
        - drop_props 中的字段（z-index）：直接丢弃；
        - 其它噪声字段（margin-top 等）：取组内众数；平局取首成员的值（DOM 序）。
        """
        # 收集每个噪声字段在所有成员中的取值
        noise_values: Dict[str, List[str]] = defaultdict(list)
        for sel in members:
            props = self.css_rules.get(sel, {})
            for prop in self.config.noise_props:
                if prop in self.config.drop_props:
                    continue
                if prop in props:
                    noise_values[prop].append(str(props[prop]))

        # 取众数
        normalized_noise: Dict[str, str] = {}
        for prop, values in noise_values.items():
            if not values:
                continue
            # 只有部分成员有此字段（其它默认为 0）—— 若有字段的成员占多数，取其众数；
            # 否则不写出该字段（默认 0 即可）
            if len(values) < (len(members) + 1) // 2:
                continue
            counter = Counter(values)
            top_val, _top_cnt = counter.most_common(1)[0]
            normalized_noise[prop] = top_val

        # 拼回完整 dict —— 用首成员的属性顺序作为模板，覆盖噪声字段
        first_props = self.css_rules.get(members[0], {})
        result: Dict[str, str] = {}
        for k, v in first_props.items():
            if k in self.config.drop_props:
                continue  # 直接丢弃 z-index
            if k in self.config.noise_props:
                if k in normalized_noise:
                    result[k] = normalized_noise[k]
                # 否则跳过（=该字段在大多数成员中缺失，归一后也不写）
            else:
                result[k] = v

        # 保险：sig 里有但首成员没有的非位置字段（理论不可能，但兜底）
        for k, v in non_noise_sig:
            if k not in result and k not in self.config.noise_props:
                result[k] = v

        return result


__all__ = ["PositionNoiseRelaxer", "PositionRelaxerConfig"]
