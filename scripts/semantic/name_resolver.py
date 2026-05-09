# -*- coding: utf-8 -*-
"""NameResolver：语义命名流水线的统一仲裁入口。

PR-1 当前阶段：**完全透传**到 ``common.semantic.extract_semantic_token``，
不做任何额外处理。目标是先把 ``utils.py`` / ``naming.py`` 散落两处的直接调用
收敛到这一个入口，让后续多层升级有统一的修改面。

仲裁原则（后续 PR 启用时使用，PR-1 暂未触发）：
    1. 各 Layer 产出 ``NameCandidate(name, confidence, source, reason)``
    2. 按 ``confidence`` 由高到低排序，相同时按 source 优先级
       （vision < layer4 < layer3 < layer2 < layer1）排，倾向"更结构化"的层
    3. 取首个有效（非空、合法 kebab）candidate 的 ``name`` 为最终结果
    4. 全部为空 / 失败 → 返回 ``""``（与现有 ``extract_semantic_token`` 行为一致，
       让调用方继续用 ltype 兜底）

缓存：
    * NameResolver 自带 ``(layer_id, layer_name) → token`` 的 LRU 缓存，避免
      同一图层在 layout_optimizer / codegen 多个阶段被多次调用时重复计算。
    * 缓存 key 必须包含 ``name``——同 id 但 name 改了（理论不该发生但
      LayoutOptimizer 的 transformer 有时会重命名节点）也能正确刷新。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from common.semantic import extract_semantic_token

# Layer 1：扩展词典 + 清洗（PR-2 引入）。延迟到模块尾部 import，避免循环依赖
# （layer1_cleaner 反向 import NameCandidate）。


# ---------------------------------------------------------------------------
# 数据契约
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NameCandidate:
    """一个候选命名（由某一层产出）。

    Attributes:
        name:       kebab-case 短词（如 ``"btn-receive"``），空串表示该层无产出
        confidence: 0~1，1 表示"强信号"（如词典精确命中），0 表示"无信号"
        source:     ``"layer1"`` / ``"layer2"`` / ``"layer3"`` / ``"vision"``
                    / ``"fallback"``——用于仲裁排序和调试
        reason:     人类可读的理由，仅用于 ``_naming_report.md`` 与日志
    """
    name: str
    confidence: float = 0.0
    source: str = "fallback"
    reason: str = ""


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class NameResolver:
    """语义命名流水线主入口。

    用法：
        resolver = NameResolver()
        token = resolver.resolve_token(layer_name, ltype)
        # token == "" 表示无语义，调用方用 ltype 兜底

    PR-1 阶段实现：**只调用 Layer 1 (extract_semantic_token)**，行为与升级前
    100% 等价。后续 PR 在 ``_collect_candidates`` 中追加更多层。
    """

    # 各 source 的优先级（数字越大越优先）。同 confidence 时用此排序。
    _SOURCE_PRIORITY = {
        "layer1":   10,   # 词典精确命中
        "layer2":   8,    # DOM 角色
        "layer3":   6,    # 文本辅助
        "vision":   4,    # 视觉兜底
        "fallback": 0,    # extract_semantic_token 拼音/PS 默认名兜底
    }

    def __init__(self) -> None:
        # (layer_id_or_None, name, ltype, has_dom_context) → token
        # has_dom_context 维度避免 utils.make_image_filename（无 dom）和
        # SimpleNamer（带 dom）共用同一 cache 槽位时互相覆盖。
        self._cache: dict[tuple[Any, str, str, bool], str] = {}

        # Layer 1 实例（懒加载词典）。线程安全：内部无可变状态。
        # 延迟 import 避免与本模块的循环依赖
        from semantic.layer1_cleaner import Layer1Cleaner
        self._layer1 = Layer1Cleaner()

        # Layer 2 实例（DOM 角色推断；PR-3 引入）
        from semantic.layer2_role_inferer import Layer2RoleInferer
        self._layer2 = Layer2RoleInferer()

        # Naming report：(raw_name, ltype, final_token, picked_source, all_candidates)
        # 仅在 record_report=True 时累积，由调用方在转换结束时取出写盘。
        self._record_report: bool = False
        self._report_rows: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 主 API
    # ------------------------------------------------------------------

    def resolve_token(
        self,
        name: str,
        ltype: str = "",
        *,
        layer_id: Any = None,
        dom_context: Any = None,
    ) -> str:
        """从图层信息抽取语义 token（kebab-case，可能为空字符串）。

        Args:
            name:        原始 PSD 图层名
            ltype:       图层类型（``"group"`` / ``"image"`` / ``"text"`` / ``"shape"``）
            layer_id:    图层 id（用于缓存）；不传也能工作，只是失去缓存收益
            dom_context: 可选 ``DomContext``（``semantic.layer2_role_inferer``），
                         传入后 Layer 2 才能做 DOM 角色推断（按钮误判降级 / 大背景
                         补全 / 纯文本容器补全 / shape 按钮强化）。不传则跳过 Layer 2。

        Returns:
            kebab-case 语义 token（如 ``"btn-receive"``），或空串（调用方应用
            ltype 兜底，如 ``"img"`` / ``"text"`` / ``"group"``）。

        缓存策略：
            * dom_context **不进缓存 key**——同一 layer 在不同 DOM 状态下被多次
              query 的场景几乎不会出现（codegen 阶段树结构是固定的）；如果出现
              也总是返回首次结果，可接受。
            * 这样可保留高速命中率（关键：layout_optimizer 多次重复调用同一 layer）。
        """
        # cache key 多带一个 ``has_dom`` 标志：让"带/不带 DomContext"两条调用
        # 路径互不串扰。layer_exporter 调 make_image_filename（无 dom_context）
        # 早于 HtmlCodegenStage 调 SimpleNamer（带 dom_context），如果共用一个
        # cache 槽位，Layer 2 会被先写入的"无 dom"结果遮蔽。
        cache_key = (layer_id, name or "", ltype or "", dom_context is not None)
        if cache_key in self._cache:
            return self._cache[cache_key]

        candidates = self._collect_candidates(name or "", ltype or "", dom_context)
        token, picked = self._arbitrate_with_pick(candidates)

        self._cache[cache_key] = token

        # 报告记录（仅在 enable_report() 后）
        if self._record_report:
            self._report_rows.append({
                "layer_id": layer_id,
                "raw_name": name or "",
                "ltype": ltype or "",
                "token": token,
                "source": picked.source if picked else "none",
                "reason": picked.reason if picked else "no candidate",
                "all": [(c.source, c.name, round(c.confidence, 2)) for c in candidates],
            })

        return token

    # ------------------------------------------------------------------
    # 候选收集（后续 PR 在此追加各层）
    # ------------------------------------------------------------------

    def _collect_candidates(
        self,
        name: str,
        ltype: str,
        dom_context: Any = None,
    ) -> list[NameCandidate]:
        """按层级收集候选命名。

        当前实现：
            * Layer 1: ``Layer1Cleaner`` —— 清洗 + 扩展词典命中（confidence=0.85）
            * Layer 2: ``Layer2RoleInferer`` —— DOM 角色推断（confidence=0.6~0.95）
              仅在调用方传入 ``dom_context`` 时启用。
            * Fallback: ``extract_semantic_token`` —— 现有关键词表 + 拼音兜底
              （confidence=0.5）

        后续 PR 会在此处追加：
            * Layer 3: 文本辅助
            * Layer 5: 视觉兜底
        """
        candidates: list[NameCandidate] = []

        # —— Layer 1：扩展词典 ——
        layer1_cand = self._layer1.analyze(name, ltype)
        if layer1_cand is not None:
            candidates.append(layer1_cand)

        # —— Fallback 层：现有 extract_semantic_token，行为完全保留 ——
        legacy_token = extract_semantic_token(name, ltype)
        if legacy_token:
            candidates.append(NameCandidate(
                name=legacy_token,
                confidence=0.5,           # 给一个"中等"置信度，让未来更高置信度的层能压过它
                source="fallback",
                reason="extract_semantic_token (legacy keyword/pinyin)",
            ))

        # —— Layer 2：DOM 角色推断（需要 dom_context） ——
        # 放在 Layer 1/Fallback 之后，因为 R1 (按钮误判降级) 需要看到已有 candidates
        if dom_context is not None:
            layer2_cand = self._layer2.analyze(ltype, dom_context, candidates)
            if layer2_cand is not None:
                candidates.append(layer2_cand)

        return candidates

    # ------------------------------------------------------------------
    # 仲裁
    # ------------------------------------------------------------------

    def _arbitrate(self, candidates: list[NameCandidate]) -> str:
        """从候选集挑出最终 token；空候选 → 返回空串。"""
        token, _ = self._arbitrate_with_pick(candidates)
        return token

    def _arbitrate_with_pick(
        self, candidates: list[NameCandidate],
    ) -> tuple[str, Optional[NameCandidate]]:
        """同 _arbitrate，但额外返回被选中的 candidate（供 report 记录）。"""
        if not candidates:
            return "", None

        # 先按 confidence 降序，再按 source 优先级降序
        ranked = sorted(
            candidates,
            key=lambda c: (
                -c.confidence,
                -self._SOURCE_PRIORITY.get(c.source, 0),
            ),
        )
        for cand in ranked:
            if cand.name:
                return cand.name, cand
        return "", None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """清缓存（每次转换开始前调用）。"""
        self._cache.clear()
        self._report_rows.clear()

    # ------------------------------------------------------------------
    # 命名报告（_naming_report.md）
    # ------------------------------------------------------------------

    def enable_report(self) -> None:
        """开启报告记录。开启后每次 ``resolve_token`` 都会累积一条记录。

        典型用法：CodegenContext 在 build_html 开始时调用，结束时通过
        ``dump_report_md(out_path)`` 写盘。
        """
        self._record_report = True

    def dump_report_md(self) -> str:
        """把累积的命名记录序列化为 Markdown 表格。

        如果 ``enable_report`` 未启用或没有数据，返回带说明的空报告。
        """
        if not self._report_rows:
            return ("# Naming Report\n\n"
                    "_未启用 enable_report() 或本次转换没有图层名记录。_\n")

        # 按 source 分组统计
        from collections import Counter
        source_counter: Counter[str] = Counter(r["source"] for r in self._report_rows)
        total = len(self._report_rows)

        lines: list[str] = []
        lines.append("# Naming Report")
        lines.append("")
        lines.append(f"- 总图层数（含重复 resolve 调用）：**{total}**")
        for src, n in source_counter.most_common():
            pct = n * 100.0 / total if total else 0
            lines.append(f"- `{src}`: {n} ({pct:.1f}%)")
        lines.append("")
        lines.append("## 明细")
        lines.append("")
        lines.append("| layer_id | raw_name | ltype | token | source |")
        lines.append("| --- | --- | --- | --- | --- |")

        # 去重：同一 (layer_id, raw_name) 多次 resolve 只展示首次
        seen: set[tuple[Any, str]] = set()
        for row in self._report_rows:
            key = (row["layer_id"], row["raw_name"])
            if key in seen:
                continue
            seen.add(key)
            raw = (row["raw_name"] or "").replace("|", "\\|").replace("\n", " ")
            token = row["token"] or "(empty → ltype fallback)"
            lines.append(
                f"| `{row['layer_id']}` | {raw} | {row['ltype']} | "
                f"`{token}` | {row['source']} |"
            )
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 模块级默认实例（方便函数式调用，避免每次 new）
# ---------------------------------------------------------------------------

_default_resolver: Optional[NameResolver] = None


def get_default_resolver() -> NameResolver:
    """获取进程级共享 NameResolver。

    适用于 ``utils.make_image_filename`` 这类无状态调用点——它们不持有
    Codegen 上下文，无法自己 new 一个 resolver；用全局单例即可，反正
    Layer 1 是无副作用的。

    注意：CodegenContext 内部的 SimpleNamer 仍然用**自己的** NameResolver
    实例，避免不同 PSD 转换之间通过共享缓存互相污染。
    """
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = NameResolver()
    return _default_resolver


def reset_default_resolver() -> None:
    """重置默认 resolver 缓存（每次转换前调用）。"""
    if _default_resolver is not None:
        _default_resolver.reset()


__all__ = [
    "NameResolver",
    "NameCandidate",
    "get_default_resolver",
    "reset_default_resolver",
]
