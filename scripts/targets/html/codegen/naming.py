# -*- coding: utf-8 -*-
"""类名生成器（Semantic Namer）。

生成格式：``"<semantic>[-<i>]__<id> <role>"``

举例：
  * ``"btn-receive__27 layer-group"``   —— group 类型、按钮语义，id=27
  * ``"bg__3 layer"``                   —— 非 group 叶子、背景
  * ``"prop-2__34 layer-group"``        —— 同级第 2 个 prop group
  * ``"group__101 layer-group"``        —— 语义抽取失败，用 ltype 兜底

设计约束：
  1. **返回单字符串、空格分隔多类**。第一个类（语义类）全局唯一，可直接用作
     ``.foo__123`` 这种单 class 的 CSS 选择器——现有 renderer 在生成 CSS 时就
     是用 ``.{class_name}`` 拼字面量，split()[0] 行为在 dom_restructure 里亦然，
     所以**语义类必须放首位**。
  2. 保留 ``layer-group`` / ``layer`` 作为角色（role）标识，满足 layout_optimizer
     的 ``'layer-group' in class`` 匹配约定（known-pitfalls #7）。
  3. **同一 layer 多次调用幂等**：用 ``layer.id`` 做缓存键；某些代码路径（如
     repeat/list 容器的临时 layer）不传 ``id``，这种情况下每次重新计算也 OK，
     因为它们的 class 只用一次。
  4. **同级兄弟去重**：用 ``parent.id + semantic`` 作为计数桶，按**兄弟索引顺序**
     给重名兄弟追加 ``-1/-2/-3``（首个省略）。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from common.semantic import extract_semantic_token  # noqa: F401  (兼容外部直 import)
from semantic import NameResolver
from semantic.layer2_role_inferer import DomContext
from semantic.name_resolver import get_default_resolver


# 兜底语义词 by ltype（当图层名无法产出 token 时使用）
_LTYPE_FALLBACK: dict[str, str] = {
    "group": "group",
    "image": "img",
    "text":  "text",
    "shape": "shape",
}

# PSD 解析器给出的 id 形如 ``"group-101"`` / ``"layer-13"``——前缀已经隐含
# 了 ltype，类名里再拼一次就会变成 ``btn__layer-13`` 这种读起来冗余的串。
# 本正则负责把 ``group-`` / ``layer-`` 前缀剥掉，只留数字后缀作为锚点。
_ID_PREFIX = re.compile(r"^(?:layer|group|repeat|list)-", re.IGNORECASE)


def _id_suffix(raw_id: Any) -> str:
    """把 layer.id 归一化为"尽量短"的锚点数字串。

    * ``"group-101"`` → ``"101"``
    * ``"layer-13"``  → ``"13"``
    * ``13``          → ``"13"``
    * 其他            → 原样转字符串
    """
    if raw_id is None:
        return "x"
    s = str(raw_id)
    return _ID_PREFIX.sub("", s) or s


class SimpleNamer:
    """Semantic 类名生成器。

    生成策略：
      semantic = extract_semantic_token(layer.name, layer.ltype)
               ?? _LTYPE_FALLBACK[ltype] ?? "node"
      sibling_index = 兄弟中相同 semantic 的序号（首个=1，省略；后续 -2/-3/...）
      role = "layer-group" if ltype=="group" else "layer"
      class = f"{semantic}[-{sibling_index}]__{layer.id} {role}"
    """

    # R5「父语义继承」用过的后缀集合——_resolve_parent_semantic 里识别"父已是
    # R5 派生产物"时拒绝继续传染（避免 prop-bg-text / prop-bg-grp 这种长链）。
    # 必须与 layer2_role_inferer.Layer2RoleInferer._INHERIT_SUFFIX_BY_LTYPE 的
    # 取值集合保持一致。
    _INHERITED_SUFFIXES = ("bg", "icon", "text")

    def __init__(self) -> None:
        # 已生成过的 class 缓存：layer_id → class string
        # 幂等保证：同一 layer 在 layout_optimizer 等下游多次被处理时，类名一致。
        self._cache: dict[Any, str] = {}

        # 同级兄弟去重：(parent_id, semantic) → 已占用的序号列表
        # key 中的 parent_id 用 id(parent dict) 而不是 layer["id"]，因为 parent
        # 可能是 repeat/list 的临时 dict，没带 id。临时父层每个调用点都是新对象，
        # 自然不冲突。
        self._sibling_seq: dict[tuple[int, str], dict[int, int]] = {}

        # 语义命名流水线：与 utils.make_image_filename 共用同一进程级 resolver
        # —— 1) 保证同一图层在 class 名 / 图片文件名里映射一致；
        #     2) report 集中在一处，便于在 codegen 末尾一次性写盘。
        # 缓存清理由 reset_image_counter() 统一负责。
        self._resolver: NameResolver = get_default_resolver()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def generate_class_name(
        self,
        layer: dict[str, Any],
        parent: Optional[dict[str, Any]] = None,
        siblings: Optional[list[dict[str, Any]]] = None,
    ) -> str:
        """生成类名字符串（多类空格分隔）。"""
        layer_id = layer.get("id")

        # 1) 缓存命中（仅当 layer 有稳定 id 时才缓存）
        if layer_id is not None and layer_id in self._cache:
            return self._cache[layer_id]

        # 2) 语义 token
        ltype = layer.get("type") or layer.get("ltype") or "group"
        name = layer.get("name", "") or ""
        dom_ctx = self._build_dom_context(layer, parent)
        semantic = self._resolver.resolve_token(
            name, ltype, layer_id=layer_id, dom_context=dom_ctx,
        ) or _LTYPE_FALLBACK.get(ltype, "node")

        # 3) 同级兄弟去重序号
        sibling_index = self._compute_sibling_index(layer, parent, siblings, semantic)

        # 4) role 类（保留 layer-group / layer 语义契约）
        role = "layer-group" if ltype == "group" else "layer"

        # 5) 组装 class
        id_suffix = _id_suffix(layer_id)
        if sibling_index <= 1:
            semantic_class = f"{semantic}__{id_suffix}"
        else:
            semantic_class = f"{semantic}-{sibling_index}__{id_suffix}"

        class_str = f"{semantic_class} {role}"
        if layer_id is not None:
            self._cache[layer_id] = class_str
        return class_str

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_dom_context(
        self,
        layer: dict[str, Any],
        parent: Optional[dict[str, Any]],
    ) -> DomContext:
        """从 legacy layer dict 抽取 Layer 2 需要的 DOM 上下文。

        所有字段都是只读快照，不持有 dict 引用——避免上游修改污染缓存。
        缺失字段交给 Layer 2 内部跳过相应规则即可。

        ``parent_semantic``（用于 R5 父语义继承）：
            * 仅当 parent 自己有强语义时才传——避免把"父也没语义"传染给子。
            * 关键：这里**只调** ``parent`` 的 resolve_token，不会引发递归
              （parent 在 NameResolver 内部会以"父父"为依据；我们不让 parent
              的 resolve 过程再去看自己的 children）。
            * 不能让"父查 child" 与"child 查父"互相递归：
              代码中的方向永远是 child → parent → grandparent → ...，单调向上。
        """
        # 自身尺寸
        w = layer.get("width")
        h = layer.get("height")

        # 父尺寸（用于 R3 大背景占比）
        pw = parent.get("width") if parent else None
        ph = parent.get("height") if parent else None

        # 直接子节点 type 列表（用于 R1 按钮误判 / R4 文本块）
        children = layer.get("children") or []
        child_types: tuple[str, ...] = tuple(
            (c.get("type") or c.get("ltype") or "group") for c in children
        )

        # 父语义（用于 R5 父语义继承）：递归 resolve parent，只取叶子语义。
        parent_sem = self._resolve_parent_semantic(parent)

        return DomContext(
            width=w,
            height=h,
            parent_width=pw,
            parent_height=ph,
            children_types=child_types,
            sibling_count=0,
            parent_semantic=parent_sem,
        )

    def _resolve_parent_semantic(
        self,
        parent: Optional[dict[str, Any]],
    ) -> Optional[str]:
        """递归算出 parent 自己的 semantic（用于 R5）。

        递归终止：parent 的 parent 信息我们当前**不传**——
        SimpleNamer 在调用方只持有"当前 layer + 直接 parent + siblings"的视图，
        没有祖父引用。所以这里给 parent 的 dom_context 是"无祖父信息"的版本：
        R3/R5 都不会触发，但 R1/R2/R4 + Layer 1/Fallback 仍能正常工作，
        足以拿到"父自己的 semantic"。
        """
        if parent is None:
            return None
        p_id = parent.get("id")
        p_name = parent.get("name", "") or ""
        p_type = parent.get("type") or parent.get("ltype") or "group"

        # 给 parent 用一个"无祖父"的 DomContext（不会触发 parent 自己的 R5），
        # 但保留 parent 自身的尺寸 / children 信息，让 R1/R3/R4 仍能工作。
        p_children = parent.get("children") or []
        p_child_types: tuple[str, ...] = tuple(
            (c.get("type") or c.get("ltype") or "group") for c in p_children
        )
        p_dom = DomContext(
            width=parent.get("width"),
            height=parent.get("height"),
            parent_width=None,
            parent_height=None,
            children_types=p_child_types,
            sibling_count=0,
            parent_semantic=None,   # 关键：祖父语义留空，避免递归
        )

        token = self._resolver.resolve_token(
            p_name, p_type, layer_id=p_id, dom_context=p_dom,
        )
        if not token:
            return None

        # 防链式继承：父名若已是 R5 风格的派生产物（以 -bg/-icon/-text 结尾），
        # 不再向下传染——否则会出现 ``prop-bg-text`` / ``prop-bg-grp`` 这种
        # 越拼越长的串。Layer 1 词典里直接定义了 ``btn-bg`` / ``card-icon`` 这类
        # 词的少量误伤可接受（这些场景子图层一般都自带词典命中，不依赖 R5）。
        for suffix in self._INHERITED_SUFFIXES:
            if token.endswith("-" + suffix):
                return None
        return token

    def _compute_sibling_index(
        self,
        layer: dict[str, Any],
        parent: Optional[dict[str, Any]],
        siblings: Optional[list[dict[str, Any]]],
        semantic: str,
    ) -> int:
        """计算当前 layer 在同 semantic 兄弟中的顺序（从 1 起）。

        必须基于 siblings 列表里的**位置**决定，而不是调用顺序——否则同一组
        被渲染多次（例如 repeat 检测会先扫描再渲染）时序号会乱。
        """
        parent_key = id(parent) if parent is not None else 0
        bucket_key = (parent_key, semantic)
        bucket = self._sibling_seq.setdefault(bucket_key, {})

        layer_id = layer.get("id")
        if layer_id is not None and layer_id in bucket:
            return bucket[layer_id]

        if not siblings:
            # 无兄弟信息 → 默认算第 1 个。若后续又来一个同 semantic 的，会在
            # 下次调用时分配 2/3/...
            index = len(bucket) + 1
            if layer_id is not None:
                bucket[layer_id] = index
            return index

        # 有 siblings：按 siblings 中出现顺序、相同 semantic 的顺位决定
        order = 0
        for sib in siblings:
            sib_type = sib.get("type") or sib.get("ltype") or "group"
            sib_id = sib.get("id")
            # 给 sibling 也带上 dom_context——避免 sibling 在被父调用之前先被
            # 缓存为"无 Layer 2"的结果（cache key 不含 dom_context，首次写入即定）。
            sib_dom = self._build_dom_context(sib, parent)
            sib_sem = self._resolver.resolve_token(
                sib.get("name", "") or "", sib_type,
                layer_id=sib_id, dom_context=sib_dom,
            ) or _LTYPE_FALLBACK.get(sib_type, "node")
            if sib_sem != semantic:
                continue
            order += 1
            if sib is layer or sib.get("id") == layer_id:
                if layer_id is not None:
                    bucket[layer_id] = order
                return order

        # layer 不在 siblings 中（理论不该发生）→ 追加到末尾
        index = len(bucket) + 1
        if layer_id is not None:
            bucket[layer_id] = index
        return index

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """重置命名器状态（每次转换开始前调用）。"""
        self._cache.clear()
        self._sibling_seq.clear()
        self._resolver.reset()

    def pop_block(self) -> None:
        """兼容 BEM 命名器的接口，实际不做任何操作。"""
        pass
