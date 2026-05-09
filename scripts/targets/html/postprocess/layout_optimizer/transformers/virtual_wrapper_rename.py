"""虚拟 wrapper 类名语义化（VirtualWrapperRename）。

LayoutOptimizer 的 DOM 重构 / FlexApplier / SiblingGroupDetector 在插入
虚拟容器时，使用了全局递增序号命名：``v-stack-1``、``v-row-2``、
``v-col-33``、``v-grid-row-5``。这种纯编号类名对开发者有两大问题：

1. **无语义**：从 ``.v-stack-7 { ... }`` 看不出它是哪个版块的；
2. **diff 不稳定**：插入一个新容器就让下游所有 wrapper 序号整体偏移，
   手工修改容易串味。

本 transformer 在 SemanticClassRename 之后、CssPretty 之前运行，遍历 DOM，
为每个编号 wrapper 挑一个"语义前缀"，重命名为
``<前缀>-stack / <前缀>-row / <前缀>-col / <前缀>-grid-row``。

命名策略
========

对每个命中的 wrapper 元素，按以下优先级寻找前缀：

1. **子孙语义类**：wrapper 通常只是排版用，视觉身份由内容决定。
   深度优先找第 1 个有"语义 class"的后代（非 ``layer`` / ``layer-group``
   / 虚拟 wrapper / 纯通用名）；
2. **祖先语义类**：若后代里没有（如空 wrapper 或全是图片叶子），
   就近向上找第 1 个有语义 class 的祖先；
3. **降级**：都找不到就保留原名。

"语义 class" 定义：class 字符串里**第一个**非虚拟、非 marker
（``layer`` / ``layer-group``）、非纯数字的 token。做完 SemanticClassRename
后，class 通常是 ``nickname``、``youshuju`` 这类干净的名字。

冲突处理
--------

``<prefix>-<kind>`` 可能重名（例如两个 wrapper 都从 ``img`` 提取前缀）：
用 ``-2 / -3 / ...`` 递增后缀区分，与 SemanticClassRename 一致。已存在于
``css_rules`` 中的其它类名也作为 reserved 跳过，避免撞车。

产出
----

- 改写 ``css_rules`` 键；
- 改写 HTML 元素 class 列表；
- 同步 ``stats['_css_merge_groups']``；
- 把映射并入 ``stats['_class_alias_map']`` 以便 ``class_alias_map.json`` 记录；
- ``stats['virtual_wrapper_renamed']`` 计数。

失败不阻断流水线；整组失败跳过。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class VirtualWrapperRenameConfig:
    """VirtualWrapperRename 的开关。"""

    enabled: bool = True
    # 子孙搜索深度上限（防极深嵌套浪费）
    max_descendant_depth: int = 6
    # 是否把"CssDedup 识别为等价 + 语义前缀相同"的多个编号 wrapper
    # 合并到同一个新 class（从而 HTML/CSS 都只剩单一类）。
    # 关闭会退化到"逐 v-stack-N 独立分配 -2/-3 序号"的旧行为。
    coalesce_equivalent_wrappers: bool = True


# ---------------------------------------------------------------------------
# 识别
# ---------------------------------------------------------------------------

# 命中"带编号的虚拟 wrapper 类"：
#   v-stack-7、v-row-2、v-col-33、v-grid-row-5
# 说明：
#   - grid-row 是 FlexApplier::_make_grid_row_div 产的 class；同时还有
#     个 kind="v-grid-row" 的伴生 class。我们把编号那一个当主名。
_NUMBERED_WRAPPER_RE = re.compile(
    r"^(?P<kind>v-stack|v-row|v-col|v-grid-row|grid-row)-(?P<id>\d+)$"
)

# 伴生 marker（同一元素上可能同时有 `v-stack-7` 和 `v-stack`）。我们只改
# 编号那一个，marker 保持原样（CSS 规则也挂在编号类上）。
_WRAPPER_MARKER_CLASSES: Set[str] = {
    "v-stack", "v-row", "v-col", "v-grid-row", "v-list",
}

# 工艺 / 标记类，不作语义候选。
_NON_SEMANTIC_CLASSES: Set[str] = {
    "layer", "layer-group",
} | _WRAPPER_MARKER_CLASSES


def _parse_numbered_wrapper(cls: str) -> Optional[Tuple[str, str]]:
    """解析 ``v-stack-7`` → ``('v-stack', '7')``；否则 None。"""
    m = _NUMBERED_WRAPPER_RE.match(cls)
    if not m:
        return None
    return m.group("kind"), m.group("id")


def _is_semantic_class(cls: str) -> bool:
    """判断一个 class token 是否可做语义前缀。"""
    if not cls:
        return False
    if cls in _NON_SEMANTIC_CLASSES:
        return False
    if _parse_numbered_wrapper(cls) is not None:
        return False
    # 纯数字 / 纯短横开头的奇异 class 不要
    if cls[0].isdigit() or cls.startswith("-"):
        return False
    return True


def _pick_semantic_from_classes(classes: List[str]) -> Optional[str]:
    """从一个元素的 class 列表里取第 1 个语义 token。"""
    for c in classes:
        if _is_semantic_class(c):
            return c
    return None


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class VirtualWrapperRename:
    """虚拟 wrapper 重命名 transformer。"""

    def __init__(
        self,
        soup,
        css_rules: Dict[str, Dict[str, str]],
        stats: Dict,
        config: Optional[VirtualWrapperRenameConfig] = None,
    ):
        self.soup = soup
        self.css_rules = css_rules
        self.stats = stats
        self.config = config or VirtualWrapperRenameConfig()
        self.stats.setdefault("virtual_wrapper_renamed", 0)
        self.stats.setdefault("_class_alias_map", {})

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    def run(self) -> None:
        if not self.config.enabled:
            return

        # 1) 收集所有命中的 wrapper 元素，按 DOM 顺序。
        #    每个元素至多 1 个编号 wrapper class。
        candidates: List[Tuple[object, str, str]] = []  # (el, old_class, kind)
        for el in self.soup.find_all(True):
            classes = el.get("class") or []
            for c in classes:
                parsed = _parse_numbered_wrapper(c)
                if parsed is None:
                    continue
                # 只处理 css_rules 中真实存在的类
                if f".{c}" not in self.css_rules:
                    continue
                candidates.append((el, c, parsed[0]))
                break  # 一个元素最多改一个 numbered wrapper

        if not candidates:
            return

        # 2) 为每个 wrapper 挑一个语义前缀。
        reserved: Set[str] = {
            sel[1:] for sel in self.css_rules.keys() if sel.startswith(".")
        }

        alias_map: Dict[str, str] = {}  # old_class → new_class
        fallback_counts: Dict[str, int] = {}  # 找不到前缀时按 kind 兜底

        # 2a) 预先为每个 candidate 计算语义前缀。
        el_prefix: List[Tuple[object, str, str, str]] = []
        for el, old_class, kind in candidates:
            prefix = self._find_semantic_prefix(el)
            if prefix is None:
                fallback_counts[kind] = fallback_counts.get(kind, 0) + 1
                prefix = "wrapper"
            el_prefix.append((el, old_class, kind, prefix))

        # 2b) 基于 CssDedup 产出的 ``_css_merge_groups``，把"同一等价组 +
        #     同 kind + 同语义前缀"的多个编号 wrapper 合流到同一个新类名。
        #
        #     ``_css_merge_groups`` 是 CssDedup 识别的"属性逐字相等"的选择
        #     器组；对编号 wrapper 而言，这意味着它们宽高/margin/背景完全
        #     一致，只是 DOM 位置不同 —— 是典型的"复用同一容器样式的多个
        #     实例"。若它们的语义前缀也相同（落在同一业务单元内），就合
        #     流为单一类名，HTML 直接复用、CSS 只剩一条规则。
        #
        #     关键约束：prefix 不同的成员 **不能** 合流（会破坏"语义类能
        #     定位业务"的契约）；这些成员仍走独立命名，靠 CssPretty 的
        #     逗号列表共享规则。
        merge_groups_input: List[List[str]] = list(
            self.stats.get("_css_merge_groups") or []
        )
        class_to_group: Dict[str, int] = {}
        for gi, group in enumerate(merge_groups_input):
            for sel in group:
                if sel.startswith("."):
                    class_to_group[sel[1:]] = gi

        coalesce_alias: Dict[Tuple, str] = {}

        for el, old_class, kind, prefix in el_prefix:
            gi = class_to_group.get(old_class)
            if self.config.coalesce_equivalent_wrappers and gi is not None:
                key: Tuple = (gi, prefix, kind)
            else:
                # 非等价组成员（或开关关闭）：各自独立命名。
                key = (f"__solo__{old_class}", prefix, kind)

            if key in coalesce_alias:
                new_name = coalesce_alias[key]
            else:
                new_name = self._allocate_name(prefix, kind, reserved)
                coalesce_alias[key] = new_name
                reserved.add(new_name)

            alias_map[old_class] = new_name

        # 3) 改写 css_rules / HTML / merge_groups。
        #    css_rules：合流时多个旧 key 映射到同一新 key，后写覆盖先写
        #    （属性逐字相等，不丢样式）。
        self._rewrite_css_rules(alias_map)
        elements_changed = self._rewrite_html_classes(alias_map)
        self._rewrite_merge_groups(alias_map)

        # 3a) 剔除"去重后只剩 ≤1 个成员"的等价组：合流后这些组已折叠为
        #     单一规则，不需要 CssPretty 再渲染为逗号列表。
        groups_after = list(self.stats.get("_css_merge_groups") or [])
        remaining: List[List[str]] = []
        for new_group in groups_after:
            uniq: List[str] = []
            seen: Set[str] = set()
            for sel in new_group:
                if sel not in seen:
                    seen.add(sel)
                    uniq.append(sel)
            if len(uniq) >= 2:
                remaining.append(uniq)
        self.stats["_css_merge_groups"] = remaining

        # 4) 统计。
        unique_new = len(set(alias_map.values()))
        classes_collapsed = len(alias_map) - unique_new
        self.stats["virtual_wrapper_renamed"] += len(alias_map)
        self.stats["virtual_wrapper_coalesced"] = (
            self.stats.get("virtual_wrapper_coalesced", 0) + classes_collapsed
        )
        merged = dict(self.stats.get("_class_alias_map") or {})
        merged.update(alias_map)
        self.stats["_class_alias_map"] = merged

        msg = (
            f"   - 虚拟 wrapper 命名: 重写 {len(alias_map)} 个类名"
            f"（影响 {elements_changed} 个元素）"
        )
        if classes_collapsed:
            msg += f"，等价合流 {classes_collapsed} 个类"
        print(msg)

    # ------------------------------------------------------------------
    # 语义前缀提取
    # ------------------------------------------------------------------

    def _find_semantic_prefix(self, el) -> Optional[str]:
        """优先从后代找，再从祖先找。"""
        # 1) 先尝试元素自身 class 里其他非虚拟 token（罕见但可能：
        #    例如已经带了 v-list marker 还有业务类）
        own = _pick_semantic_from_classes(
            [c for c in (el.get("class") or []) if _parse_numbered_wrapper(c) is None]
        )
        if own is not None:
            return own

        # 2) 后代 DFS（保 DOM 顺序 = 深度优先前序）
        found = self._dfs_find(el, depth=0)
        if found is not None:
            return found

        # 3) 祖先就近
        p = el.parent
        while p is not None:
            if getattr(p, "name", None) is None:
                p = p.parent
                continue
            prefix = _pick_semantic_from_classes(p.get("class") or [])
            if prefix is not None:
                return prefix
            p = p.parent

        return None

    def _dfs_find(self, el, depth: int) -> Optional[str]:
        if depth > self.config.max_descendant_depth:
            return None
        for child in getattr(el, "children", []) or []:
            if getattr(child, "name", None) is None:
                continue  # 跳过 NavigableString
            prefix = _pick_semantic_from_classes(child.get("class") or [])
            if prefix is not None:
                return prefix
            sub = self._dfs_find(child, depth + 1)
            if sub is not None:
                return sub
        return None

    # ------------------------------------------------------------------
    # 命名分配
    # ------------------------------------------------------------------

    @staticmethod
    def _allocate_name(prefix: str, kind: str, reserved: Set[str]) -> str:
        """``<prefix>-<kind>``，撞车则加 ``-2 / -3``。

        kind 里的 ``grid-row`` 是复合词，照原样拼。
        """
        base = f"{prefix}-{kind.replace('v-', '')}"
        if base not in reserved:
            return base
        seq = 2
        while True:
            candidate = f"{base}-{seq}"
            if candidate not in reserved:
                return candidate
            seq += 1

    # ------------------------------------------------------------------
    # Rewrite helpers（与 SemanticClassRename 同构）
    # ------------------------------------------------------------------

    def _rewrite_css_rules(self, alias_map: Dict[str, str]) -> None:
        new_rules: Dict[str, Dict[str, str]] = {}
        for sel, props in self.css_rules.items():
            if sel.startswith(".") and sel[1:] in alias_map:
                new_sel = f".{alias_map[sel[1:]]}"
                new_rules[new_sel] = props
            else:
                new_rules[sel] = props
        self.css_rules.clear()
        self.css_rules.update(new_rules)

    def _rewrite_html_classes(self, alias_map: Dict[str, str]) -> int:
        changed = 0
        for el in self.soup.find_all(True):
            classes = el.get("class") or []
            if not classes:
                continue
            new_classes: List[str] = []
            hit = False
            for c in classes:
                if c in alias_map:
                    mapped = alias_map[c]
                    # 合流情形下同一元素可能多次映射到同一新类，去重保留首次。
                    if mapped not in new_classes:
                        new_classes.append(mapped)
                    hit = True
                else:
                    if c not in new_classes:
                        new_classes.append(c)
            if hit:
                el["class"] = new_classes
                changed += 1
        return changed

    def _rewrite_merge_groups(self, alias_map: Dict[str, str]) -> None:
        groups = self.stats.get("_css_merge_groups")
        if not groups:
            return
        new_groups: List[List[str]] = []
        for group in groups:
            new_group: List[str] = []
            for sel in group:
                if sel.startswith(".") and sel[1:] in alias_map:
                    new_group.append(f".{alias_map[sel[1:]]}")
                else:
                    new_group.append(sel)
            new_groups.append(new_group)
        self.stats["_css_merge_groups"] = new_groups


__all__ = ["VirtualWrapperRename", "VirtualWrapperRenameConfig"]
