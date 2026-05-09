# -*- coding: utf-8 -*-
"""Layer 2：DOM 角色推断（PR-3 引入）。

输入：单个图层 + 其在原 PSD 树中的 DOM 上下文（父 / 兄弟 / 自身子节点）。
输出：``NameCandidate(source="layer2", confidence=...)`` 或 ``None``。

设计原则：
    1. **只做高信号判断**：能 90%+ 把握时才出 candidate；模棱两可一律放弃。
    2. **不替代 Layer 1/Fallback**：Layer 2 是补全 + 纠偏，不重复词典工作。
    3. **跨阶段安全**：所有 context 都是只读的（dict / 数值 / list），不修改原图层树。

当前实现的 4 条规则（信号强度从高到低）：

    R1 - 按钮误判降级（confidence=0.95，仅在 layer1/fallback 给出 btn-* 时触发）
        现象：``做任务攒糖果 解锁惊喜宝箱`` 被 Layer 1 命中"做任务" → ``btn-task``，
        但它是个 slogan group（包含子文本/装饰图层），不是按钮。
        判定：layer 是 group + 子节点数 ≥ 3，或子节点中含其他 group → 不是按钮。
        输出：把 ``btn-xxx`` 改写成 ``slogan-xxx``（保留业务词），confidence=0.95
              压制 layer1 的 0.85。

    R2 - shape 按钮强化（confidence=0.7，仅在没有任何 layer1/fallback 候选时）
        现象：纯几何形状 + 长方形 + 中等大小 → 多半是按钮底框。
        判定：ltype=="shape" + 宽 30~400 + 高 20~120 + 宽高比 1.5~8。
        输出：``btn``，confidence=0.7 略高于 fallback 但不压 layer1。

    R3 - 大背景 group 补全（confidence=0.6）
        现象：占父容器面积 ≥ 80% 的 group/image，且没有任何业务语义 → 是背景。
        判定：(layer.area / parent.area) >= 0.8 + ltype == "group"。
        输出：``bg-section``，confidence=0.6 压 fallback、不压 layer1。
        注意：ltype=="image" 故意排除——image 的"占父 80%"通常是该 group 的
              主显示图（如 prop / 图标），不应被命名为 bg。

    R4 - 纯文本容器补全（confidence=0.6）
        现象：group 全部直接子节点都是 text，且 >= 2 个 → 是文字段落容器。
        判定：ltype=="group" + 所有 children 的 type/ltype == "text" + len >= 2。
        输出：``text-block``，confidence=0.6。

    R5 - 父语义继承（confidence=0.55）
        现象：子图层名是 fallback（``rect`` / ``rounded`` / ``shape`` / ``group`` / ``img``）
        或纯几何/形状词，但其父容器有强语义（如 ``slogan`` / ``btn-receive`` / ``prop``）。
        判定：existing_candidates 全为 confidence < 0.7 的弱信号 + dom.parent_semantic 非空非通用兜底。
        输出：``<parent_semantic>-<role>``，role 由 ltype 决定：
              - image → ``-icon``（少数情况是 ``-bg``，由面积比辅助判断）
              - shape/group → ``-bg``
              - text → ``-text``
        例：父 ``slogan`` + 子 image → ``slogan-icon``；父 ``slogan`` + 子 shape → ``slogan-bg``。
        confidence=0.55 介于 fallback (0.5) 与 layer1 (0.85) 之间，能压 fallback 不压词典。

注意：R1 是降级类（替代/改写），R2~R5 是补全类（仅在无强信号时介入）。
仲裁逻辑在 NameResolver 里完成（按 confidence + source priority）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from semantic.name_resolver import NameCandidate


# ---------------------------------------------------------------------------
# DOM Context 数据契约
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DomContext:
    """从调用方传入的 DOM 上下文快照。

    字段全部可选——上游能拿到多少传多少；缺失字段会让 Layer 2 跳过对应规则。
    """
    # 自身 bbox & 尺寸
    width:  Optional[float] = None
    height: Optional[float] = None

    # 父容器 bbox（用于占比判断）
    parent_width:  Optional[float] = None
    parent_height: Optional[float] = None

    # 直接子节点的 type 列表（仅类型字符串，避免循环引用）
    # 例：["text", "text", "image"]；空 list 表示"叶子"
    children_types: tuple[str, ...] = ()

    # 兄弟数量（用于判断是否在 list 中重复）
    sibling_count: int = 0

    # 父容器的最终语义 token（NameResolver 已仲裁、不含 sibling/-id 后缀）。
    # 用于 R5"父语义继承"：当本图层无任何强语义时，从父名派生
    # （父 slogan → 子 image=slogan-icon, 子 group/shape=slogan-bg, 子 text=slogan-text）。
    # 注意：上游应传剥过 hash 的纯语义（如 "slogan"）；空串 / None / 通用兜底
    # （group/img/text/shape/node）一律跳过，避免把"父也没有语义"的事实传染下去。
    parent_semantic: Optional[str] = None

    @property
    def area(self) -> Optional[float]:
        if self.width is None or self.height is None:
            return None
        return float(self.width) * float(self.height)

    @property
    def parent_area(self) -> Optional[float]:
        if self.parent_width is None or self.parent_height is None:
            return None
        return float(self.parent_width) * float(self.parent_height)


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

# btn-* 前缀正则：用于 R1 把 ``btn-task`` 改写为 ``slogan-task``
_BTN_PREFIX = re.compile(r"^btn(?:-|$)")


class Layer2RoleInferer:
    """根据 DOM 上下文推断结构角色。

    用法（在 NameResolver 内部）：

        inferer = Layer2RoleInferer()
        cand = inferer.analyze(
            ltype="group",
            dom=DomContext(width=720, height=80, parent_width=720,
                           parent_height=1280, children_types=("text",) * 3),
            existing_candidates=[NameCandidate("btn-task", 0.85, "layer1", "")],
        )
        # cand.name == "slogan-task"  (R1 触发)
    """

    # 阈值（集中放此，便于回归调参）
    BG_AREA_RATIO       = 0.8       # R3
    SHAPE_BTN_W_MIN     = 30.0      # R2
    SHAPE_BTN_W_MAX     = 400.0
    SHAPE_BTN_H_MIN     = 20.0
    SHAPE_BTN_H_MAX     = 120.0
    SHAPE_BTN_RATIO_MIN = 1.5
    SHAPE_BTN_RATIO_MAX = 8.0
    SLOGAN_MIN_CHILDREN = 5         # R1：子节点数 ≥ 此值视为非按钮容器
                                    # （按钮典型结构：底框+文字+icon ≤ 3-4 子节点；
                                    #  ≥ 5 子节点的"按钮"群更像 slogan/卡片）

    # "强语义"阈值：confidence < 此值的 candidate 不视为真有语义，
    # R2~R4 仍可介入（典型如 shapes_fallback 的 rect/circle 弱信号）。
    STRONG_SEMANTIC_CONF = 0.7

    def analyze(
        self,
        ltype: str,
        dom: Optional[DomContext],
        existing_candidates: list[NameCandidate],
    ) -> Optional[NameCandidate]:
        """对单个图层做 Layer 2 推断；返回 candidate 或 None。

        Args:
            ltype:                图层类型 ``"group" / "image" / "text" / "shape"``
            dom:                  DomContext；若为 None，所有需要 DOM 的规则跳过
            existing_candidates:  Layer 1 / Fallback 已经产出的候选——R1 的"按钮
                                  误判"判断要看是否已有 ``btn-*``，R2~R4 的"补全"
                                  判断要看是否**没有**其他 candidate。
        """
        # 各规则按"信号强度"顺序尝试，命中即返回（一个图层只出一条 layer2 candidate）
        cand = self._rule_button_demote(ltype, dom, existing_candidates)
        if cand is not None:
            return cand

        # R2~R4 都是"补全"：仅当没有"强"语义 candidate 时才介入
        # （弱 candidate 如 shapes_fallback 的 rect/circle 不算）
        has_strong = any(
            c.name and c.confidence >= self.STRONG_SEMANTIC_CONF
            for c in existing_candidates
        )

        if not has_strong:
            cand = self._rule_shape_button(ltype, dom)
            if cand is not None:
                return cand

            cand = self._rule_section_background(ltype, dom)
            if cand is not None:
                return cand

            cand = self._rule_text_block(ltype, dom)
            if cand is not None:
                return cand

            cand = self._rule_inherit_from_parent(ltype, dom, existing_candidates)
            if cand is not None:
                return cand

        return None

    # ------------------------------------------------------------------
    # R1 - 按钮误判降级
    # ------------------------------------------------------------------

    def _rule_button_demote(
        self,
        ltype: str,
        dom: Optional[DomContext],
        existing: list[NameCandidate],
    ) -> Optional[NameCandidate]:
        """检测 layer1/fallback 错把 slogan 命名成 btn-*。"""
        if ltype != "group" or dom is None:
            return None
        # 找已有 btn-* candidate
        btn_cand = next(
            (c for c in existing if c.name and _BTN_PREFIX.match(c.name)),
            None,
        )
        if btn_cand is None:
            return None

        # 判定"不像按钮"：
        #   - 子节点数 >= SLOGAN_MIN_CHILDREN (5)，且
        #   - 子节点中至少有 1 个 group（嵌套结构）
        # 这两个条件 AND，避免把 "底框+文字+icon" 三件套的真按钮误降级。
        n_children = len(dom.children_types)
        has_subgroup = "group" in dom.children_types
        if n_children < self.SLOGAN_MIN_CHILDREN or not has_subgroup:
            return None

        # 改写 btn-task → slogan-task；纯 btn → slogan
        new_name = _BTN_PREFIX.sub("slogan-", btn_cand.name).rstrip("-")
        if not new_name or new_name == "slogan":
            new_name = "slogan"

        return NameCandidate(
            name=new_name,
            confidence=0.95,   # 高于 layer1 的 0.85，会被仲裁优先选中
            source="layer2",
            reason=f"button demote: {btn_cand.name} → {new_name} "
                   f"(n_children={n_children}, has_subgroup={has_subgroup})",
        )

    # ------------------------------------------------------------------
    # R2 - shape 按钮强化
    # ------------------------------------------------------------------

    def _rule_shape_button(
        self,
        ltype: str,
        dom: Optional[DomContext],
    ) -> Optional[NameCandidate]:
        if ltype != "shape" or dom is None:
            return None
        if dom.width is None or dom.height is None:
            return None
        w, h = float(dom.width), float(dom.height)
        if h <= 0:
            return None
        ratio = w / h
        if not (self.SHAPE_BTN_W_MIN <= w <= self.SHAPE_BTN_W_MAX):
            return None
        if not (self.SHAPE_BTN_H_MIN <= h <= self.SHAPE_BTN_H_MAX):
            return None
        if not (self.SHAPE_BTN_RATIO_MIN <= ratio <= self.SHAPE_BTN_RATIO_MAX):
            return None
        return NameCandidate(
            name="btn",
            confidence=0.7,
            source="layer2",
            reason=f"shape button shape: {w:.0f}x{h:.0f} ratio={ratio:.2f}",
        )

    # ------------------------------------------------------------------
    # R3 - 大背景 group 补全
    # ------------------------------------------------------------------

    def _rule_section_background(
        self,
        ltype: str,
        dom: Optional[DomContext],
    ) -> Optional[NameCandidate]:
        # 只对 group 生效；image 占父 80% 通常是该 group 的主显示图，不是 bg
        if ltype != "group" or dom is None:
            return None
        a, pa = dom.area, dom.parent_area
        if a is None or pa is None or pa <= 0:
            return None
        ratio = a / pa
        if ratio < self.BG_AREA_RATIO:
            return None
        return NameCandidate(
            name="bg-section",
            confidence=0.6,
            source="layer2",
            reason=f"covers {ratio*100:.0f}% of parent",
        )

    # ------------------------------------------------------------------
    # R4 - 纯文本容器补全
    # ------------------------------------------------------------------

    def _rule_text_block(
        self,
        ltype: str,
        dom: Optional[DomContext],
    ) -> Optional[NameCandidate]:
        if ltype != "group" or dom is None:
            return None
        kids = dom.children_types
        if len(kids) < 2:
            return None
        if not all(k == "text" for k in kids):
            return None
        return NameCandidate(
            name="text-block",
            confidence=0.6,
            source="layer2",
            reason=f"all {len(kids)} children are text",
        )

    # ------------------------------------------------------------------
    # R5 - 父语义继承
    # ------------------------------------------------------------------

    # 通用兜底语义集合：父名是这些时不做继承（继承也没意义）。
    # 也包含 layer2 自己产出的"补全类语义"——避免链式继承下出现 ``bg-section-bg``。
    _GENERIC_PARENT_SEMANTICS = frozenset({
        "", "node",
        "group", "img", "image", "text", "shape", "rect", "rounded", "circle", "ellipse",
        "bg", "bg-section",
        "text-block",
    })

    # 后缀映射：根据子图层 ltype 决定继承时拼什么后缀
    _INHERIT_SUFFIX_BY_LTYPE = {
        "image": "icon",
        "shape": "bg",
        "group": "bg",
        "text":  "text",
    }

    def _rule_inherit_from_parent(
        self,
        ltype: str,
        dom: Optional[DomContext],
        existing: list[NameCandidate],
    ) -> Optional[NameCandidate]:
        """从父语义派生子图层名（消除 rect/rounded/group/img 等 fallback 命名）。

        触发：existing 中所有 candidate 都 < STRONG_SEMANTIC_CONF（即只有 fallback / 弱 layer2）
        + dom.parent_semantic 是有意义的业务语义。
        """
        if dom is None:
            return None
        parent_sem = (dom.parent_semantic or "").strip().lower()
        if not parent_sem:
            return None
        # 父语义必须"足够具体"——通用兜底/形状词不传染
        if parent_sem in self._GENERIC_PARENT_SEMANTICS:
            return None

        suffix = self._INHERIT_SUFFIX_BY_LTYPE.get(ltype)
        if not suffix:
            return None

        # 大背景启发：image 占父 ≥ 80% 时改用 -bg 而不是 -icon（更符合直觉）
        if ltype == "image":
            a, pa = dom.area, dom.parent_area
            if a is not None and pa is not None and pa > 0 and a / pa >= 0.8:
                suffix = "bg"

        # 避免重复（父叫 ``btn`` + 子要拼 ``btn-bg`` 是 OK 的，但父叫 ``slogan-bg``
        # 子如果要拼 ``slogan-bg-bg`` 就重复了 → 直接用父语义）。
        if parent_sem.endswith("-" + suffix):
            new_name = parent_sem
        else:
            new_name = f"{parent_sem}-{suffix}"

        return NameCandidate(
            name=new_name,
            confidence=0.55,   # 高于 fallback 0.5，低于 layer1 0.85
            source="layer2",
            reason=f"inherit from parent='{parent_sem}', ltype={ltype}, suffix={suffix}",
        )


__all__ = ["Layer2RoleInferer", "DomContext"]
