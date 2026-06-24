"""重复元素抽取（RepeatClassUnifier）— 把 ≥3 个等价 hash 类合并为单一语义类。

为什么需要这一步
================

CssDedup 已经把"属性完全相同的多个选择器"识别成 ``_css_merge_groups``，
CssPretty 也会把它们渲染成 ``.a, .b, .c { ... }`` 的合并块；但 **HTML 中
依然写了 N 个不同的 hash 类**（``.prop__68 / .prop__105 / .prop__142 / ...``），
带来两个问题：

1. **可读性差** —— 工程师看 HTML 时无法立刻意识到 "这是同一类卡片，重复 5 次"，
   只能比对 hash 后缀；
2. **复用代价高** —— 想给"所有 prop 卡片"加交互/动画/状态修饰符（active/done），
   要同步改 N 个 class（``.prop__68--done``、``.prop__105--done`` …），
   或者退化到属性选择器（``[class^="prop__"]``，又脆又乱）。

figma-to-frontend 的产物里这种 5×4 网格只有一个 ``.sec-grid-item`` 类被复用
20 次，可读性、可维护性、可状态扩展都远胜 hash 类方案。

修复策略（保持视觉 1:1）
========================

**输入**：``stats['_css_merge_groups']``（CssDedup 产出，每组 ≥2 个等价选择器）。

**触发条件**（同时满足）：
1. 组内成员数 ≥ ``min_unify_count``（默认 3）——避免对偶发 2 个相似类强行抽象；
2. 所有成员选择器形如 ``.<base>__<digits>`` 或 ``.<base>-<n>__<digits>``——
   即 SimpleNamer 产出的"语义 + sibling_index + id 后缀"格式；
3. 公共 base 段非空（``.v-stack-7`` / ``.v-stack-8`` ⇒ base = ``v-stack``，
   注意：自动派生类如 ``v-stack-N`` / ``v-row-N`` / ``v-col-N`` 整体作为 base，
   不是 ``v-stack-7__38`` 这种 SimpleNamer 类）。

**改写动作**：
- 在 HTML soup 里：把每个成员 class（如 ``prop__68``）从元素 class 列表中移除，
  替换为统一的 unified class（如 ``prop`` —— 详见命名规则）；
- 在 ``css_rules`` dict 里：删除原 N 个选择器条目，新增单一 ``.<unified>`` 条目，
  属性 dict 取首个成员的属性（CssDedup 已保证组内属性逐字相等）；
- 把这一组从 ``_css_merge_groups`` 里移除（已被合并，无需 CssPretty 再渲染合并组）；
- 在 ``stats`` 里累加 ``classes_unified`` / ``elements_unified`` 计数。

命名规则（unified class）
=========================

- 取组内成员"剥掉 ``__\\d+`` 后的前缀"作为 base；
- 若 base 唯一（所有成员前缀都是 ``prop`` 或都是 ``btn-receive``）→ 直接用 base；
- 若 base 不唯一（极少见，组成员混用 ``rounded`` / ``rounded-2``，CssDedup 通常不
  会把这种放一组，但兜底防御）→ 跳过这组，不做合并；
- 命名空间冲突（base 在 ``css_rules`` 中已存在为某个具体选择器）→ 用 ``-N`` 序列
  （``.base-2`` / ``.base-3`` ...），与 ``SemanticClassRename`` 保持同套序号
  规则，便于开发者阅读时不用在 ``.nickname-grp`` / ``.nickname-2`` 等异构
  名字间来回切换。

边界与不变量
============

1. **不动 ``layer-group`` / ``layer`` 角色类**：HTML class 列表里这两个类是 layout_optimizer
   契约的一部分（known-pitfalls #7），保留。
2. **不动 v-stack-* / v-row-* / v-col-* 等"自动派生类"**：这些是 dom_restructure /
   sibling_group_detector 写出的容器类，本身就有"逐序号唯一"语义（不是 SimpleNamer
   的 ``__id`` 后缀模式），不需要再合并；只对带 ``__\\d+`` 后缀的 SimpleNamer 类做处理。
3. **HTML id 不动**：原来的 ``id="layer-N"`` 在 strip_dev_metadata 中会被剥离，
   本 transformer 不碰 id。
4. **CssPretty 兼容**：本 transformer 修改 ``_css_merge_groups``（移除已被合并的组），
   CssPretty 仍按剩余组渲染合并块；新建的 unified 选择器走"普通规则"路径，按 DOM 序输出。

回归保障
========

- 视觉等价性来自 CssDedup 的承诺（同组属性逐字相等，CSS 选择器分组在 W3C 等价于多条独立规则）；
- 本 transformer 只是把"3 条独立规则 + 3 个 class"折叠为"1 条规则 + 1 个 class"；
- 失败（如成员名解析异常）单组跳过，不阻断流水线。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class RepeatUnifyConfig:
    """RepeatClassUnifier 的所有开关。"""

    enabled: bool = True
    # 至少多少个成员才合并；2 太少容易把"碰巧相同"的两个类强行抽象
    min_unify_count: int = 3
    # 是否在 HTML 里给被合并的元素加一个 ``data-repeat-index`` 属性（位序，1 起）
    # 方便后续 :nth-child / JS data-* 选择
    annotate_index: bool = True


# ---------------------------------------------------------------------------
# 选择器解析
# ---------------------------------------------------------------------------

# SimpleNamer 输出的类名格式：``<base>__<digits>``，base 内可含字母/数字/短横（含 sibling 序号）。
# 例：``prop__68`` / ``btn-receive__74`` / ``rounded-2__40`` / ``btn-invite-3__215``
_NAMED_RE = re.compile(r"^\.(?P<base>[A-Za-z][A-Za-z0-9-]*?)__(?P<id>\d+)$")

# 自动派生类：``v-stack-7`` / ``v-row-29`` / ``v-col-43`` —— 不带 ``__id`` 后缀，
# 这种合并组（CssDedup 也会算等价）我们故意 **不合并**：序号本身就是它们的"复用维度"，
# 替换成单一类反而会破坏 dom_restructure / flex_applier 的"按 v-stack-N 选位置"假设。
_DERIVED_RE = re.compile(r"^\.(?:v-stack|v-row|v-col)-\d+$")


def _parse_named(selector: str) -> Optional[Tuple[str, str]]:
    """解析 SimpleNamer 形式的选择器，返回 (base, id) 或 None。"""
    m = _NAMED_RE.match(selector)
    if not m:
        return None
    return m.group("base"), m.group("id")


def _is_derived(selector: str) -> bool:
    """是否属于 v-stack/v-row/v-col 自动派生类（不参与合并）。"""
    return bool(_DERIVED_RE.match(selector))


def _common_base_for_group(selectors: List[str]) -> Optional[str]:
    """组内所有成员若都是 SimpleNamer 类、且 base 唯一 → 返回 base，否则 None。"""
    bases: Set[str] = set()
    for sel in selectors:
        parsed = _parse_named(sel)
        if parsed is None:
            return None
        bases.add(parsed[0])
    if len(bases) != 1:
        return None
    return next(iter(bases))


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class RepeatClassUnifier:
    """重复 hash 类合并 transformer。

    使用方式::

        unifier = RepeatClassUnifier(soup, css_rules, stats, config)
        unifier.run()  # 修改 soup / css_rules / stats['_css_merge_groups']
    """

    def __init__(
        self,
        soup,
        css_rules: Dict[str, Dict[str, str]],
        stats: Dict,
        config: Optional[RepeatUnifyConfig] = None,
    ):
        self.soup = soup
        self.css_rules = css_rules
        self.stats = stats
        self.config = config or RepeatUnifyConfig()
        # 累计统计
        self.stats.setdefault("classes_unified", 0)        # 被合并掉的类总数（净减少）
        self.stats.setdefault("elements_unified", 0)       # HTML 中被改写的元素总数
        self.stats.setdefault("repeat_groups_unified", 0)  # 实际成功合并的组数

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    def run(self) -> None:
        if not self.config.enabled:
            return

        groups: List[List[str]] = list(self.stats.get("_css_merge_groups") or [])
        if not groups:
            return

        # 已使用的 unified 名（避免不同合并组产出同名）
        existing_selectors: Set[str] = set(self.css_rules.keys())
        used_unified: Set[str] = set()

        # 保留的合并组（未被本 transformer 合并的，仍交给 CssPretty 渲染合并块）
        remaining_groups: List[List[str]] = []

        for group in groups:
            members = [s for s in group if s in self.css_rules and self.css_rules[s]]
            if len(members) < self.config.min_unify_count:
                remaining_groups.append(group)
                continue
            # 自动派生类（v-stack-N 等）整组跳过
            if any(_is_derived(s) for s in members):
                remaining_groups.append(group)
                continue
            base = _common_base_for_group(members)
            if not base:
                remaining_groups.append(group)
                continue

            unified_sel = self._allocate_unified(base, existing_selectors, used_unified)

            # 1) 改写 HTML：把成员类替换成 unified（首位）+ 移除其他 hash 类
            elements_changed = self._rewrite_html_classes(set(m[1:] for m in members), unified_sel[1:])

            # 2) 改写 CSS：取首成员属性，新增 unified；删除原成员
            # ✅ 修复：检查成员间是否有不同的 z-index，若有则保留最小值
            unified_props = dict(self.css_rules[members[0]])
            z_values = []
            for sel in members:
                z_str = self.css_rules[sel].get('z-index')
                if z_str is not None:
                    try:
                        z_values.append(int(float(z_str)))
                    except (ValueError, TypeError):
                        pass
            
            # ⚠️ 如果成员间有不同的 z-index，保留最小值（最保守策略）
            # 这样可以防止高z-index的元素被压在下方
            if z_values and len(set(z_values)) > 1:
                # 成员 z-index 不一致，保留最小值
                unified_props['z-index'] = str(min(z_values))
            
            self.css_rules[unified_sel] = unified_props
            for sel in members:
                self.css_rules.pop(sel, None)

            existing_selectors.add(unified_sel)
            used_unified.add(unified_sel)

            # 统计
            self.stats["classes_unified"] += len(members) - 1
            self.stats["elements_unified"] += elements_changed
            self.stats["repeat_groups_unified"] += 1

        # 写回精简后的合并组（已合并的组从此剔除）
        self.stats["_css_merge_groups"] = remaining_groups

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _allocate_unified(
        self,
        base: str,
        existing_selectors: Set[str],
        used_unified: Set[str],
    ) -> str:
        """为 base 分配一个新选择器。

        命名策略（与 SemanticClassRename 的 ``-N`` 后缀序列保持一致）：
          - 优先裸 ``.base``；
          - 冲突时依次 ``.base-2`` / ``.base-3`` / ... 直到找到空位；
          - 这样所有同 base 的类（不管是单独的还是合并组的）都遵循
            ``base / base-2 / base-3 / ...`` 一套统一序列，开发者
            阅读 CSS 时不用在 ``.nickname-grp`` / ``.nickname-2`` 等
            异构名字间来回切换。
        """
        candidate = f".{base}"
        if candidate not in existing_selectors and candidate not in used_unified:
            return candidate
        for i in range(2, 1000):
            cand = f".{base}-{i}"
            if cand not in existing_selectors and cand not in used_unified:
                return cand
        # 兜底（理论不可达）
        return f".{base}-x"

    def _rewrite_html_classes(self, member_classes: Set[str], unified_class: str) -> int:
        """把所有 HTML 元素 class 列表中"含成员类"的位置，改为 unified_class。

        替换策略：
        - 找到的成员类整个移除；
        - 在 class 列表头部插入 unified_class（保证语义类仍在首位，
          满足 SimpleNamer / CssDedup / strip_dev_metadata 对"首类即语义类"的契约）；
        - 已经存在 unified_class 的元素不重复添加。

        Returns:
            被改写的元素总数（每个元素只计 1 次）。
        """
        changed = 0
        idx_in_group = 0
        for el in self.soup.find_all(True):
            classes = el.get("class") or []
            if not classes:
                continue
            hit_pos = -1
            new_classes: List[str] = []
            for c in classes:
                if c in member_classes:
                    hit_pos = len(new_classes) if hit_pos < 0 else hit_pos
                    # 跳过该成员 hash 类
                    continue
                new_classes.append(c)
            if hit_pos < 0:
                continue
            # 如果元素本来已有 unified_class（罕见），不重复加
            if unified_class not in new_classes:
                new_classes.insert(0, unified_class)
            el["class"] = new_classes
            if self.config.annotate_index:
                idx_in_group += 1
                el["data-repeat-index"] = str(idx_in_group)
            changed += 1
        return changed


__all__ = ["RepeatClassUnifier", "RepeatUnifyConfig"]
