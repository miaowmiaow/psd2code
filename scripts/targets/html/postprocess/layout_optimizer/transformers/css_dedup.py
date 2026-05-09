"""CSS 去冗余转换器（默认值剔除 + background shorthand + z-index 精简 + 等价规则合并）

为什么需要这一步
================

原始抽取阶段（``core/extract/layer_exporter.py``）给每个图层无脑塞了一个
``z-index = self._z_counter``，本质上等价于"图层全局顺序号"。这导致两个
后果：

1) **z-index 大量冗余**。CSS 中 ``position: absolute`` 元素的视觉叠序，
   只在"父容器内出现 bbox 重叠的兄弟"时才依赖 z-index；而绝大多数父容器
   下的子元素遵循"DOM 源代码顺序 = z 序升序"（这也是 ``LayerRenderer`` /
   ``HTMLGenerator`` 的天然产出顺序）。在这种情况下，浏览器默认行为
   （后写覆盖先写）已经能正确实现叠序，z-index 完全是噪声。

2) **样式块大量重复**。同一个版块在版面里被复制多份（如南瓜大作战 6 个
   宝箱卡片、5 个排行榜行），它们的 PSD 子图层"位置/尺寸/opacity 完全相同"，
   除了那个根据 layer_id 递增的 z-index 之外。结果 ``style_optimized.css``
   里出现 20 个 ``.rounded__N``、15 个 ``.btn__N`` 块，**逐字相同只差 z-index**，
   导致体积膨胀、阅读困难、改样式时漏改风险。

3) **CSS 默认值噪声**。LayerRenderer 给每个图层都写了
   ``opacity: 1`` / ``mix-blend-mode: normal``，而这两个就是 CSS 规范默认值，
   完全不需要写出来。typical 一个 PSD 转 HTML 项目这两条字段会贡献 200~400 行。

4) **background 字段碎片化**。``background-image / -position / -repeat``
   被拆成三行写，但绝大多数图层用法很简单（``position: left top``、
   ``repeat: no-repeat``），可以合并成一条 ``background:`` shorthand 一行
   写完，省 2/3 行。

修复策略（保持视觉 1:1）
========================

**Pass 0a —— 默认值剔除**
    扫描 ``css_rules`` 每条规则，删掉等于 CSS 规范默认值的属性：
    ``opacity: 1``、``mix-blend-mode: normal``、
    ``background-position: left top``（与 CSS 默认 ``0% 0%`` 等价）。

    ⚠️ **注意：``background-repeat: no-repeat`` 不能删！** 它的 CSS 规范默认值
    是 ``repeat``，删除会让浏览器按 ``repeat`` 重复贴图（原图比容器小时
    平铺为多份），**视觉破坏**。这条字段保留，由 Pass 0b 的 shorthand 收纳。

    这一步**必须最先做**：让后面的 Pass 2 能识别出"删完默认值后变成完全等价"
    的更多规则组（合并组数会显著增加）。

**Pass 0b —— background shorthand 合并**
    当一条规则同时含 ``background-image`` 和经典的 image 周边字段时，合并
    成一行 ``background: <image> <position> <repeat>;``（位置/重复缺省即省略，
    与 W3C 等价）。``background-color`` / ``background-size`` 单独处理时
    不参与合并（它们语义独立，独占行更可读）。

**Pass 1 —— z-index 精简**
    遍历每个父容器：若其直接子元素的 z-index 序列**严格递增**（缺失视为
    通配，不打断递增），则视该容器为"DOM 顺序天然吻合 z 序"，删除这些子
    的 z-index 字段。否则（出现 z 数值倒挂——典型场景：``v-stack`` /
    ``v-list`` 重排过子元素），全部保留 z-index 以兜底视觉正确。

    极少数还残留 z-index 的元素，会在 Pass 2 阻止它们跟同等"无 z" 兄弟
    合并到同一签名——这是预期行为：z-index 不同 = 视觉职责不同 = 不能合并。

**Pass 2 —— 等价规则合并**
    扫描 ``css_rules``：把"属性 dict 完全相等"的多个选择器登记到同一个
    签名组。``dict_to_css`` 输出时，同组选择器写成 ``.a, .b, .c { ... }``
    单条规则。CSS 选择器分组在 W3C 标准里完全等价于多条独立规则，不会
    引入任何视觉差异。

    注意：仅对 ``parse_css_to_dict`` 已识别的"单 .class / #id"规则去重；
    全局 header（``* { ... }``、``body { ... }``、``@media``）由
    ``extract_global_css_header`` 原样保留，不参与合并。

注意事项
========
- **DOM 改动**：Pass 1 只读 DOM 结构（用 BeautifulSoup 遍历兄弟顺序），
  写入 ``css_rules``；不修改 HTML。
- **类名匹配**：HTML 中元素的 ``class="btn__27 layer-group"`` 多类，
  CSS 里只对首个语义类（``.btn__27``）做规则；判断时取 first token。
- **Pass 2 是按 dict_to_css 输出阶段做合并**：本 transformer 仅产出
  "selector → group_id" 的合并意图，由 ``dict_to_css(..., merge_groups=...)``
  实际渲染。这样 ``css_rules`` 字典语义保持"每个选择器独立"，便于
  下游（react/vue target）继续按选择器查样式。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Pass 0a：CSS 默认值常量
# ---------------------------------------------------------------------------

# (property, default_value) —— value 为 None 表示"任何值都删"（仅依赖 prop 名）
# 注意：这里只处理"100% 安全 = CSS 规范默认值"的属性，绝不能加任何会改变视觉的项。
_CSS_DEFAULT_VALUES: Tuple[Tuple[str, str], ...] = (
    ("opacity", "1"),
    ("opacity", "1.0"),
    ("mix-blend-mode", "normal"),
)

# 仅当"存在 background-image"时，``background-position`` 等同 ``0 0``（CSS 默认）
# 才能安全删除；否则单独存在就是噪声，必删。
#
# ⚠️ 重要：``background-repeat`` 的 CSS 规范默认是 ``repeat``（不是 no-repeat）！
# 删 ``background-repeat: no-repeat`` 会让浏览器按 repeat 重复贴图，原图比容器
# 小时就会平铺出多份，**视觉破坏**。所以这里**只删 position，绝不删 repeat**。
# repeat 字段会在 background shorthand 合并时被收纳成一行。
_BACKGROUND_NOISE_VALUES: Tuple[Tuple[str, str], ...] = (
    ("background-position", "left top"),
    ("background-position", "0 0"),
    ("background-position", "0% 0%"),
)


class CssDedup:
    """CSS 去冗余 transformer。

    使用方式::

        dedup = CssDedup(soup, css_rules, stats)
        dedup.run()  # 修改 css_rules，并把合并意图写入 stats['_merge_groups']
    """

    def __init__(self, soup, css_rules: Dict[str, Dict[str, str]], stats: Dict):
        self.soup = soup
        self.css_rules = css_rules
        self.stats = stats
        # Pass 0a 统计：被删掉的默认值属性条数
        self.stats.setdefault('css_defaults_stripped', 0)
        # Pass 0b 统计：被合并到 shorthand 的属性条数（每合并一条 = 节省一行）
        self.stats.setdefault('background_shorthand_merged', 0)
        # Pass 1 统计：被删掉的 z-index 数
        self.stats.setdefault('z_index_pruned', 0)
        # Pass 2 统计：被合并的规则条数（节省条数）
        self.stats.setdefault('css_rules_merged', 0)
        # Pass 2 输出：[[selector, ...], ...] 同组选择器列表（顺序内排序）
        # 由 LayoutOptimizer.optimize() 透传给 dict_to_css(merge_groups=...)
        self.stats.setdefault('_css_merge_groups', [])

    # ------------------------------------------------------------------
    # Pass 0a: 默认值剔除
    # ------------------------------------------------------------------

    def _strip_default_values(self) -> None:
        """删除等于 CSS 规范默认值的属性。

        - ``opacity: 1`` / ``mix-blend-mode: normal`` —— 完全无副作用
        - ``background-position: left top`` —— 与 W3C 默认值 ``0% 0%`` 等价
        - ⚠️ ``background-repeat: no-repeat`` **不删**！其 CSS 默认值是 ``repeat``
          （删了会让小图按 repeat 平铺破坏视觉）。
        """
        stripped = 0
        for sel, props in self.css_rules.items():
            for prop, default_val in _CSS_DEFAULT_VALUES:
                if prop in props and str(props[prop]).strip() == default_val:
                    del props[prop]
                    stripped += 1
            for prop, default_val in _BACKGROUND_NOISE_VALUES:
                if prop in props and str(props[prop]).strip() == default_val:
                    del props[prop]
                    stripped += 1
        self.stats['css_defaults_stripped'] = stripped

    # ------------------------------------------------------------------
    # Pass 0b: background shorthand 合并
    # ------------------------------------------------------------------

    def _collapse_background_shorthand(self) -> None:
        """把 ``background-image / -position / -repeat`` 合成一条 ``background:`` 行。

        规则：
          - 必须有 ``background-image``
          - 同时存在 ``background-color`` / ``background-size`` / ``background-attachment``
            等"复杂语义字段"时**不合并**（保持各自独占行的可读性，避免
            shorthand 行过长）
          - 合并时按 W3C 推荐顺序：``<color> <image> <position>/<size> <repeat>``
            本实现只合并最常见的子集 ``<image> <position> <repeat>``，其它
            字段维持独立行
          - 默认值省略：缺 position 默认 ``0% 0%``、缺 repeat 默认 ``repeat``
            （但 PSD 抽取产出几乎都是 no-repeat，此处主动写出避免歧义）

        合并后删除原 ``background-image / -position / -repeat`` 字段，
        新增 ``background`` 字段，**顺序保留在原 background-image 的位置**
        （便于 CssPretty 的属性分组识别）。
        """
        merged = 0
        for sel, props in self.css_rules.items():
            if 'background-image' not in props:
                continue
            # 如果存在更复杂的 background 子字段，跳过 shorthand
            if any(k in props for k in (
                'background-color', 'background-size',
                'background-attachment', 'background-clip',
                'background-origin',
            )):
                continue
            img = str(props['background-image']).strip()
            pos = str(props.pop('background-position', '')).strip()
            rep = str(props.pop('background-repeat', '')).strip()
            # 拼 shorthand
            tokens = [img]
            if pos:
                tokens.append(pos)
            if rep:
                tokens.append(rep)
            # 仅当有 ≥2 个 token 时合并才有意义（节省行数）；
            # 单 url 时保持 background-image 字段，让 dict 顺序更稳定。
            if len(tokens) >= 2:
                # 用 dict 重建，保证 background 替换原 background-image 的位置
                new_props: Dict[str, str] = {}
                for k, v in props.items():
                    if k == 'background-image':
                        new_props['background'] = ' '.join(tokens)
                    else:
                        new_props[k] = v
                # 替换原 dict 内容（保持引用，下游 css_rules[sel] 仍是同一对象）
                props.clear()
                props.update(new_props)
                # 节省的字段数 = 被合掉的（image + 已 pop 的 pos / rep）- 1（新增 background）
                merged += (1 + (1 if pos else 0) + (1 if rep else 0)) - 1
            else:
                # 没合并：把已经 pop 的 pos/rep 还回去（实际上前面 strip default 后 pop 拿到空串，无需还原）
                pass
        self.stats['background_shorthand_merged'] = merged

    # ------------------------------------------------------------------
    # Pass 1: z-index 精简
    # ------------------------------------------------------------------

    def _first_class(self, element) -> Optional[str]:
        cls = element.get('class') if hasattr(element, 'get') else None
        if not cls:
            return None
        return cls[0] if cls else None

    def _read_z(self, selector: str) -> Optional[int]:
        rule = self.css_rules.get(selector)
        if not rule:
            return None
        v = rule.get('z-index')
        if v is None:
            return None
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return None

    def _prune_z_index(self) -> None:
        """删除"DOM 顺序与 z 序天然一致"的容器内全部子 z-index。

        策略（仅在 100% 安全时删除）：
          收集容器内所有直接子元素的 (selector, z) 序列：
            - 全部子都有显式 z-index（无 None）且严格递增（按 DOM 顺序）
              → DOM 顺序天然实现叠序 → 全删
            - 否则（任何一个子没有 z-index / 出现倒挂 / 仅有部分子带 z）
              → 全部保留

        为什么必须"全部子都有 z 才能删"
        --------------------------------
        CSS 层叠规则下，positioned 元素带数字 ``z-index`` 与带 ``auto``
        的兄弟**不在同一栈层**：带数字 z（即便 z=1）的兄弟会**始终**绘制
        在 ``z-index: auto`` 兄弟之上，与 DOM 顺序无关。因此只要兄弟里有
        一个 z 是 None（=> CSS 输出后为 auto），就**不能**删另一个兄弟
        的 z-index：

        典型反例（南瓜大作战 H5 canvas 直接子）：
          ``[bg__1 z=1, img__2 z=2, slogan__18 z=18, ...]``
          上游 transformer 把 bg__1 之外的 z-index 都擦成了 None
          （None 经 dict_to_css 输出会缺 z-index，浏览器按 auto 处理）。
          若再把 bg__1 的 z=1 也删掉，看起来"无差异"，但若**保留** bg__1
          的 z=1 而其他兄弟为 auto，bg__1 就会盖住所有兄弟。这种情况下
          唯一安全做法是要么所有兄弟都带 z 且 DOM 单调（可全删），要么
          都不动（保留 bg__1 的 z=1 是错的，但删它也是错的——根因在上游
          擦 z 的 transformer，本 Pass 不应擅自补救）。
        """
        pruned = 0
        for parent in self.soup.find_all():
            children = list(parent.find_all(recursive=False))
            if not children:
                continue

            # 收集每个子的 (selector, z)
            seq: List[Tuple[Optional[str], Optional[int]]] = []
            for c in children:
                cls = self._first_class(c)
                sel = f'.{cls}' if cls else None
                z = self._read_z(sel) if sel else None
                seq.append((sel, z))

            # 必须全部子都有 z-index，否则删任何一个都会破坏栈层
            # （带数字 z 的兄弟总是在 z-index:auto 兄弟之上）
            if any(z is None for _, z in seq):
                continue

            # 全部带 z，再检查是否严格递增
            monotonic = True
            prev: Optional[int] = None
            for _, z in seq:
                if prev is not None and z <= prev:
                    monotonic = False
                    break
                prev = z
            if not monotonic:
                # 出现倒挂（典型：v-stack / v-list 重排），保留 z-index
                continue

            # 全部子带 z 且 DOM 顺序严格递增 → 删全部 z-index
            # 例外：v-* 虚拟 wrapper（v-list / v-stack / v-row / v-col）
            # 是 LayoutOptimizer 把原本散布在多个 z 层级的兄弟聚合到单一
            # DOM 位置形成的，它们的 z-index 是聚合后的整体叠序锚点，
            # 删除会让 wrapper 被同容器内带数字 z 的其它兄弟（或后续被
            # 删 z 的兄弟）的 DOM 顺序覆盖，造成整块视觉消失。
            # 因此 v-* wrapper 的 z-index 必须保留。
            for sel, _ in seq:
                if sel is None:
                    continue
                if self._is_virtual_wrapper_selector(sel):
                    continue
                rule = self.css_rules.get(sel)
                if rule and 'z-index' in rule:
                    del rule['z-index']
                    pruned += 1
        self.stats['z_index_pruned'] = pruned

    @staticmethod
    def _is_virtual_wrapper_selector(sel: str) -> bool:
        """判定选择器是否是 LayoutOptimizer 产出的虚拟 wrapper。

        virtual id 格式：``v-{kind}-{N}``，所以选择器形如 ``.v-list-1`` /
        ``.v-stack-3`` / ``.v-row-2`` / ``.v-col-5``；首类名以 ``.v-`` 开头
        即视为虚拟 wrapper，其 z-index 不参与 prune。
        """
        return sel.startswith('.v-')

    # ------------------------------------------------------------------
    # Pass 2: 等价规则合并
    # ------------------------------------------------------------------

    def _merge_equivalent_rules(self) -> None:
        """把"属性完全相同"的选择器分到同组。

        - 仅 ``parse_css_to_dict`` 已识别的 .xxx / #xxx 规则参与（``css_rules`` 的 key 已经过滤）
        - 同组内 selector 按字母排序，便于稳定输出
        """
        # 签名 = 排序后的 (key, value) 元组
        sig_to_selectors: Dict[Tuple[Tuple[str, str], ...], List[str]] = defaultdict(list)
        for sel, props in self.css_rules.items():
            if not props:
                continue
            sig = tuple(sorted(props.items()))
            sig_to_selectors[sig].append(sel)

        groups: List[List[str]] = []
        merged_count = 0
        for sig, selectors in sig_to_selectors.items():
            if len(selectors) < 2:
                continue
            selectors_sorted = sorted(selectors)
            groups.append(selectors_sorted)
            merged_count += len(selectors_sorted) - 1

        # 输出顺序：按"组内首个选择器"字母升序，方便阅读
        groups.sort(key=lambda g: g[0])
        self.stats['css_rules_merged'] = merged_count
        self.stats['_css_merge_groups'] = groups

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    def run(self) -> None:
        # Pass 0a/0b 必须最先做：让 Pass 2 看到"扣掉默认值/合并 shorthand 后"
        # 真正语义等价的规则，合并组数能显著增加
        self._strip_default_values()
        self._collapse_background_shorthand()
        self._prune_z_index()
        self._merge_equivalent_rules()
