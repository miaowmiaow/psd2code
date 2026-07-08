"""语义类去后缀（SemanticClassRename）— 删除 ``__<id>`` 后缀，仅保留纯语义名。

为什么需要这一步
================

LayoutOptimizer 的命名链路最终保留了 ``SimpleNamer`` 产物 ``<base>__<id>``
（例：``nickname__37``、``btn__42``、``rounded-2__40``）。``__<id>`` 是全局
layer_id（``layer_exporter._z_counter``），对开发者来说是**噪音**：

- 数字跳跃无规律（``nickname__37`` 和 ``nickname__102`` 之间毫无递进关系）；
- 同一视觉组件出现 N 次时，CSS 里出现 N 条 ``.nickname__X { ... }`` 规则，
  工程师难以识别"这是同一类节点"；
- 维护成本高：加 hover / active 修饰符要逐条同步，或退化到 ``[class^="nickname"]``。

``RepeatClassUnifier`` 已经处理了"同 base + 样式完全相同 + ≥3 个"的情况，把它们
合并成单一 ``.nickname``；但**样式各自不同的同名类**（11 个位置不同的
``nickname``）没被合并，依旧带着 ``__<id>`` 后缀。

本 transformer 的职责：**无论样式是否相同**，把所有 ``.<base>__<id>`` 形态的类名
重写为纯语义名 ``.<base>``；同名冲突时用 ``-2 / -3 / ...`` 递增后缀区分。

映射信息通过两个文件外部可查：
- ``layer_map.json.by_class``（首类名 → PSD 图层元数据）—— 新 class 作 key；
- ``class_alias_map.json``（原 ``__N`` 类名 → 新精简类名）—— 本 transformer 产出。

触发规则
========

**输入筛选**：所有形如 ``.<base>__<digits>`` 的选择器（SimpleNamer 格式）。

**分组与命名**：
1. 按 base 分组、组内按首次出现元素的 DOM 顺序排序；
2. 组内第 1 个成员 → ``.<base>``；第 2 个 → ``.<base>-2``；第 N 个 → ``.<base>-N``；
3. 若目标名已存在于 ``css_rules`` 中（如 ``.btn-2`` 已是独立类）→ 跳号到下一个可用的；
4. 派生类 ``v-stack-N`` / ``v-row-N`` / ``v-col-N`` / ``grid-row-N`` 不参与（不带 ``__id``）。

**改写动作**：
- ``css_rules`` 中 ``.<base>__<id>`` 键替换为 ``.<new>``；
- HTML 元素 class 列表里同样替换；
- ``stats['_css_merge_groups']`` 中若残留 ``__N`` 选择器，同步重写为新名；
- 累计写入 ``stats['semantic_class_renamed']`` 计数 + ``stats['_class_alias_map']``
  （``{"nickname__37": "nickname", "nickname__102": "nickname-2", ...}``）。

与 RepeatClassUnifier 的关系
============================

- ``RepeatClassUnifier`` 先跑：把 ≥3 个等价 hash 类折叠为单一 ``.<base>`` 规则
  （HTML 同时复用），此时 ``.<base>`` 已出现在 ``css_rules`` 中；
- ``SemanticClassRename`` 后跑：遇到这种 base 时，继续从 ``-2`` 开始分配，
  避免撞车。

回归保障
========

- 本 transformer 只改选择器名字，不改属性、不改 DOM 顺序、不合并规则 → W3C 等价；
- 失败单组跳过，不阻断流水线。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class SemanticRenameConfig:
    """SemanticClassRename 的开关。"""

    enabled: bool = True


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

# SimpleNamer 输出的类名格式：``<base>__<digits>``
#   base 内可含字母/数字/短横（含 sibling-index / 合并后缀）。
_NAMED_RE = re.compile(r"^(?P<base>[A-Za-z][A-Za-z0-9-]*?)__(?P<id>\d+)$")


def _parse_named_class(cls: str):
    """解析 ``nickname__37`` → ``('nickname', '37')``，否则 None。"""
    m = _NAMED_RE.match(cls)
    if not m:
        return None
    return m.group("base"), m.group("id")


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class SemanticClassRename:
    """语义类去后缀 transformer。

    使用方式::

        renamer = SemanticClassRename(soup, css_rules, stats, config)
        renamer.run()   # 修改 soup / css_rules / stats
    """

    def __init__(
        self,
        soup,
        css_rules: Dict[str, Dict[str, str]],
        stats: Dict,
        config: Optional[SemanticRenameConfig] = None,
    ):
        self.soup = soup
        self.css_rules = css_rules
        self.stats = stats
        self.config = config or SemanticRenameConfig()
        self.stats.setdefault("semantic_class_renamed", 0)
        self.stats.setdefault("_class_alias_map", {})

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    def run(self) -> None:
        if not self.config.enabled:
            return

        # 1) 按 DOM 顺序收集每个 ``__N`` 类的首次出现元素。
        #    同 base 可能出现在多个元素（每元素 1 个 __N 类）；按 DOM 序编号。
        base_to_old_classes: Dict[str, List[str]] = {}  # base → [nickname__37, nickname__102, ...]
        seen: Set[str] = set()

        for el in self.soup.find_all(True):
            classes = el.get("class") or []
            for c in classes:
                if c in seen:
                    continue
                parsed = _parse_named_class(c)
                if parsed is None:
                    continue
                # 只处理实际存在于 css_rules 中的类（避免改了孤立 class 没意义）
                if f".{c}" not in self.css_rules:
                    seen.add(c)
                    continue
                base = parsed[0]
                base_to_old_classes.setdefault(base, []).append(c)
                seen.add(c)

        if not base_to_old_classes:
            return

        # 2) 分配新名字。目标名集合由 css_rules 中非 __N 的已有选择器构成；
        #    每分配一个就加入集合，避免同一 base 组内再次撞车。
        reserved: Set[str] = set()
        for sel in self.css_rules.keys():
            if not sel.startswith("."):
                continue
            cls = sel[1:]
            if _parse_named_class(cls) is None:
                # 非 __N 类（含 RepeatClassUnifier 已合并的 .<base>、v-stack-N 等）
                reserved.add(cls)

        alias_map: Dict[str, str] = {}  # old_class → new_class

        for base, old_classes in base_to_old_classes.items():
            # ⚠️ 检查同 base 的多个类是否有不同的样式
            # 如果有不同的非定位属性，则分别分配不同的名字（否则会丢失样式）
            distinct_classes = self._group_by_properties(old_classes)
            
            # distinct_classes 是列表 [[ old_class, ... ], [ old_class, ... ], ...]
            # 每个子列表内的类具有相同的非定位属性
            idx = 0
            for group in distinct_classes:
                for old in group:
                    new_name = self._allocate_name(base, idx, reserved)
                    alias_map[old] = new_name
                    reserved.add(new_name)
                idx += 1

        # 3) 改写 css_rules（保持插入顺序 → CssPretty 的 DOM 序仍按元素顺序）。
        self._rewrite_css_rules(alias_map)

        # 4) 改写 HTML。
        elements_changed = self._rewrite_html_classes(alias_map)

        # 5) 同步 merge_groups（可能残留已被合并组外的 __N 选择器）。
        self._rewrite_merge_groups(alias_map)

        # 6) 统计。
        self.stats["semantic_class_renamed"] += len(alias_map)
        # stats 里累积（支持多次运行合并，虽然目前只跑 1 次）
        merged = dict(self.stats.get("_class_alias_map") or {})
        merged.update(alias_map)
        self.stats["_class_alias_map"] = merged

        print(
            f"   - 语义类去后缀: 重写 {len(alias_map)} 个类名（影响 {elements_changed} 个元素）"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    
    def _group_by_properties(self, old_classes: List[str]) -> List[List[str]]:
        """按非定位属性分组同 base 的多个类。
        
        目的：防止有不同样式（background、color、font-size 等）的类被映射到同一个新名
        （这样会导致样式丢失或混淆）。
        
        返回：[[old_class, ...], [old_class, ...], ...]
              同一子列表内的类有相同的非定位属性
        """
        _POSITION_PROPS = {'position', 'left', 'top', 'right', 'bottom', 'z-index', 
                          'width', 'height'}  # width/height 不算定位属性
        
        # 构建每个类的非定位属性签名
        class_signatures = {}
        for old_cls in old_classes:
            sel = f".{old_cls}"
            props = self.css_rules.get(sel, {})
            
            # 提取非定位属性，生成不可变签名（用于分组）
            non_pos_props = {
                k: v for k, v in props.items()
                if k not in _POSITION_PROPS
            }
            # 将字典转换为有序的元组以支持 set/dict 操作
            sig = tuple(sorted((k, v) for k, v in non_pos_props.items()))
            class_signatures[old_cls] = sig
        
        # 按签名分组
        sig_to_group = {}
        result_order = []  # 保持原始顺序
        for old_cls in old_classes:
            sig = class_signatures[old_cls]
            if sig not in sig_to_group:
                sig_to_group[sig] = []
                result_order.append(sig)
            sig_to_group[sig].append(old_cls)
        
        # 按原始顺序返回分组
        result = [sig_to_group[sig] for sig in result_order]
        return result

    @staticmethod
    def _allocate_name(base: str, idx: int, reserved: Set[str]) -> str:
        """为 base 组第 idx 个成员分配新名：base / base-2 / base-3 / ...

        遇到 reserved 撞车就跳号（不跳过 idx，只跳过具体候选值）。
        """
        # 约定：第 1 个用裸 base；之后 -2, -3, -4 ...
        seq = idx + 1  # 1-based
        while True:
            candidate = base if seq == 1 else f"{base}-{seq}"
            if candidate not in reserved:
                return candidate
            seq += 1

    def _rewrite_css_rules(self, alias_map: Dict[str, str]) -> None:
        """按原 css_rules 的键顺序重建，命中就替换选择器。"""
        new_rules: Dict[str, Dict[str, str]] = {}
        for sel, props in self.css_rules.items():
            if sel.startswith(".") and sel[1:] in alias_map:
                new_sel = f".{alias_map[sel[1:]]}"
                new_rules[new_sel] = props
            else:
                new_rules[sel] = props
        # 原地替换（保持引用；优化器其他模块可能持有 ref）
        self.css_rules.clear()
        self.css_rules.update(new_rules)

    def _rewrite_html_classes(self, alias_map: Dict[str, str]) -> int:
        """把 HTML 每个元素 class 列表里命中的 ``__N`` 类替换为新名。

        Returns:
            被改写的元素数量（每元素至多计 1 次）。
        """
        changed = 0
        for el in self.soup.find_all(True):
            classes = el.get("class") or []
            if not classes:
                continue
            new_classes: List[str] = []
            hit = False
            for c in classes:
                if c in alias_map:
                    new_classes.append(alias_map[c])
                    hit = True
                else:
                    new_classes.append(c)
            if hit:
                el["class"] = new_classes
                changed += 1
        return changed

    def _rewrite_merge_groups(self, alias_map: Dict[str, str]) -> None:
        """把 ``stats['_css_merge_groups']`` 中残留的 ``__N`` 选择器重写为新名。"""
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


__all__ = ["SemanticClassRename", "SemanticRenameConfig"]
