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

4) **background 字段碎片化**。``background-image / -position / -repeat / -size``
   被拆成多行写，但绝大多数图层用法很简单，可以合并成一条 ``background:``
   shorthand 一行写完，省 2/3 行。

5) **layout 产生的结构性冗余**。LayoutOptimizer 生成 flex 容器时无条件写入
   ``align-items: flex-start``（flex 默认值）；生成 v-stack/v-row wrapper 时
   无条件写入 ``box-sizing: border-box``（全局 * 规则已覆盖）。这些属性
   完全多余，属于"生成侧噪声"，在 Pass 0a 统一清理最干净。

修复策略（保持视觉 1:1）
========================

**Pass 0a —— 默认值剔除**
    扫描 ``css_rules`` 每条规则，删掉等于 CSS 规范默认值的属性：
    ``opacity: 1``、``mix-blend-mode: normal``、
    ``background-position: left top``（与 CSS 默认 ``0% 0%`` 等价）、
    ``align-items: flex-start``（flex 默认对齐，无需显式写）、
    ``box-sizing: border-box``（全局 ``* { box-sizing: border-box }`` 已覆盖）。

    ⚠️ **注意：``background-repeat: no-repeat`` 不能删！** 它的 CSS 规范默认值
    是 ``repeat``，删除会让浏览器按 ``repeat`` 重复贴图（原图比容器小时
    平铺为多份），**视觉破坏**。这条字段保留，由 Pass 0b 的 shorthand 收纳。

    这一步**必须最先做**：让后面的 Pass 2 能识别出"删完默认值后变成完全等价"
    的更多规则组（合并组数会显著增加）。

**Pass 0b —— background shorthand 合并**
    当一条规则同时含 ``background-image`` 和经典的 image 周边字段时，合并
    成一行 ``background: <image> <position>/<size> <repeat>;``（缺省字段省略，
    与 W3C 等价）。现在支持将 ``background-size`` 也纳入 shorthand（使用
    W3C 标准的 ``<position>/<size>`` 语法）。``background-color`` 单独处理时
    不参与合并（语义独立）。

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
import hashlib


# ---------------------------------------------------------------------------
# Pass 0a：CSS 默认值常量
# ---------------------------------------------------------------------------

# (property, default_value) —— value 为 None 表示"任何值都删"（仅依赖 prop 名）
# 注意：这里只处理"100% 安全 = CSS 规范默认值"的属性，绝不能加任何会改变视觉的项。
_CSS_DEFAULT_VALUES: Tuple[Tuple[str, str], ...] = (
    ("opacity", "1"),
    ("opacity", "1.0"),
    ("mix-blend-mode", "normal"),
    # flex 布局默认值：align-items 默认值为 stretch，但 flex-start 是 LayoutOptimizer
    # 生成 flex 容器时无条件写入的值。在绝大多数 PSD 转 HTML 场景中，flex 容器的
    # 子元素都是固定尺寸的，align-items 实际上不影响布局，可安全删除。
    # ⚠️ 注意：这里删的是 flex-start（不是 stretch），逻辑是"LayoutOptimizer 统一
    # 写的 flex-start 是噪声"；如果有容器需要 stretch，它根本不会有此属性。
    ("align-items", "flex-start"),
    # box-sizing: border-box 由全局 * { box-sizing: border-box } 已覆盖，
    # v-stack/v-row 等 wrapper 上单独写一遍是完全多余的。
    ("box-sizing", "border-box"),
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
        # Pass 1.5 统计：混合状态下被补/改写的 z-index 数
        
        # 优化2-Day6：签名缓存，避免重复计算规则属性签名
        self._signature_cache: Dict[int, str] = {}  # key=id(props_dict), value=hash签名
        self.stats.setdefault('z_index_filled', 0)
        # Pass 2 统计：被合并的规则条数（节省条数）
        self.stats.setdefault('css_rules_merged', 0)
        # Pass 2 输出：[[selector, ...], ...] 同组选择器列表（顺序内排序）
        # 由 LayoutOptimizer.optimize() 透传给 dict_to_css(merge_groups=...)
        self.stats.setdefault('_css_merge_groups', [])

    # ------------------------------------------------------------------
    # Pass 0a: 默认值剔除
    # ------------------------------------------------------------------

    def _strip_default_values(self) -> None:
        """删除等于 CSS 规范默认值或全局已覆盖的属性。

        - ``opacity: 1`` / ``mix-blend-mode: normal`` —— CSS 规范默认值
        - ``background-position: left top`` —— 与 W3C 默认值 ``0% 0%`` 等价
        - ``align-items: flex-start`` —— LayoutOptimizer 生成 flex 容器时无条件
          写入，但实际是噪声（绝大多数场景下子元素固定尺寸，对齐方式无影响）
        - ``box-sizing: border-box`` —— 全局 ``* { box-sizing: border-box }``
          已覆盖，单条规则里重复写完全多余
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
        """把 ``background-image / -position / -repeat / -size`` 合成一条 ``background:`` 行。

        规则：
          - 必须有 ``background-image``
          - 同时存在 ``background-color`` / ``background-attachment`` /
            ``background-clip`` / ``background-origin`` 等字段时**不合并**
          - 合并时按 W3C 推荐顺序：``<image> <position>/<size> <repeat>``
            当有 ``background-size`` 时使用 ``<position>/<size>`` 语法（W3C 标准，
            position 和 size 之间用 ``/`` 分隔）
          - 默认值省略：缺 position 默认 ``0% 0%``、缺 repeat 默认 ``repeat``
            （但 PSD 抽取产出几乎都是 no-repeat，此处主动写出避免歧义）

        合并后删除原子字段，新增 ``background`` 字段，**顺序保留在原
        background-image 的位置**（便于 CssPretty 的属性分组识别）。
        """
        merged = 0
        for sel, props in self.css_rules.items():
            if 'background-image' not in props:
                continue
            # 存在这些复杂字段时跳过 shorthand
            if any(k in props for k in (
                'background-color',
                'background-attachment', 'background-clip',
                'background-origin',
            )):
                continue
            # 多背景（含逗号）时不合并，保持分散写可读
            img = str(props['background-image']).strip()
            if img.count('url(') > 1:
                continue

            pos = str(props.pop('background-position', '')).strip()
            rep = str(props.pop('background-repeat', '')).strip()
            size = str(props.pop('background-size', '')).strip()

            # 拼 shorthand
            # W3C 规范：background: <image> <position>/<size> <repeat>
            # 若有 size 则必须写 position（哪怕为空串也用 0% 0% 占位），
            # 否则浏览器无法区分 position 与 size。
            tokens = [img]
            if size:
                # 有 size：必须提供 position，用 / 分隔
                pos_part = pos if pos else '0% 0%'
                tokens.append(f'{pos_part}/{size}')
            elif pos:
                tokens.append(pos)
            if rep:
                tokens.append(rep)

            # 仅当有 ≥2 个 token 时合并才有意义
            if len(tokens) >= 2:
                new_props: Dict[str, str] = {}
                for k, v in props.items():
                    if k == 'background-image':
                        new_props['background'] = ' '.join(tokens)
                    else:
                        new_props[k] = v
                props.clear()
                props.update(new_props)
                # 统计节省的行数
                merged += (1
                           + (1 if pos else 0)
                           + (1 if rep else 0)
                           + (1 if size else 0)) - 1
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

    def _has_z_descendant(self, element) -> bool:
        """递归检查 element 的子树是否包含任意带数字 z-index 的后代。

        ⚠️ 用于探测"祖先穿透"风险：当某个 ``z-index: auto`` 子的内部存在
        带数字 z 的后代时，由于 auto 不建立 stacking context，该后代的
        z 数字会"穿透"到再上层与外层兄弟比较。
        典型反例（魔界人幸运签 H5 canvas）：
          ``.bg`` (v-stack, auto) 内含 ``.img-2-2-col`` (z=10)
          → 10 穿透到 #canvas 层级，反盖兄弟 ``.group-4-3`` (auto)。
        """
        for desc in element.find_all(recursive=True):
            cls = self._first_class(desc)
            if not cls:
                continue
            sel = f'.{cls}'
            if self._read_z(sel) is not None:
                return True
        return False

    def _prune_z_index(self) -> None:
        """删除"DOM 顺序与 z 序天然一致"的容器内全部子 z-index；
        或在出现"混合状态"（部分子带数字 z + 部分子 auto，或某 auto 子的
        后代含带 z 元素）时，给 auto 兄弟补 z-index 兜底，保证视觉与
        DOM 顺序一致。

        三种处理：
          A. 全部子都有显式 z-index 且按 DOM 顺序严格递增
             → DOM 顺序天然实现叠序 → 全删（v-* wrapper 除外）
          B. 全部子都 auto（无 z）且 **无任何后代带 z**
             → 无需处理（浏览器按 DOM 顺序绘制，天然正确）
          C. 混合状态 —— ⚠️ 危险区，触发兜底补 z：
             c1. 直接子部分带 z + 部分 auto
             c2. 直接子全 auto，但某个子的**后代**含带数字 z 元素
                 （后代 z 穿透 auto 祖先，与外层兄弟错位比较）

        为什么混合状态必须补 z（Pass 1.5 兜底）
        ----------------------------------------
        CSS 层叠规则下，positioned 元素带数字 ``z-index`` 与带 ``auto``
        的兄弟**不在同一栈层**：带数字 z（即便 z=1）的兄弟会**始终**绘制
        在 ``z-index: auto`` 兄弟之上，与 DOM 顺序无关。

        典型反例（魔界人幸运签 H5 canvas 直接子）：
          DOM 序：[img-2 auto, bg z=10, group z=41, group-2-2 auto,
                   group-3-3 auto, group-4-3 auto, ding auto, group-5 auto]
          原 PSD：[img__1 z=1, bg__11 z=11, group__17 z=17, group-2__52 z=52,
                   group-3__60 z=60, group-4__87 z=87, ding__97 z=97, group-5__100 z=100]
          单调 ✓ 本应全删。但上游 cluster bg 修正循环把 bg/group 的 z 改成 10/41
          且未同步擦除，导致它们成为"残留带 z"的兄弟。其余兄弟在某处被擦成 auto
          → 浏览器中 .bg(z=10) 反盖到 .group-4-3(auto) 之上 → 大背景图盖住卡片。
          正确做法：检测到混合后，按 DOM 顺序为所有兄弟补 z-index，保留原 z 顺序
          关系，且使序列严格递增。
        """
        # 两轮扫描的必要性
        # ------------------
        # c1（直接子混合）会给 auto 子补 z 数值；这些新 z 会让外层祖先的
        # ``_has_z_descendant`` 探测返回 True。因此 c2（直接子全 auto + 后代
        # 有 z）必须在 c1 全部完成后再扫一遍，才能发现"穿透"风险。
        pruned = 0
        filled = 0

        # ----- Pass 1：处理直接子带 z 的容器（Case A / Case C c1）-----
        for parent in self.soup.find_all():
            children = list(parent.find_all(recursive=False))
            if not children:
                continue

            seq: List[Tuple[Optional[str], Optional[int]]] = []
            for c in children:
                cls = self._first_class(c)
                sel = f'.{cls}' if cls else None
                z = self._read_z(sel) if sel else None
                seq.append((sel, z))

            n_with_z = sum(1 for _, z in seq if z is not None)
            n_total = len(seq)

            # Pass 1 跳过：直接子全 auto（留给 Pass 2 处理 c2）
            if n_with_z == 0:
                continue

            # Case C c1: 混合状态（部分带 z + 部分 auto）—— 兜底补 z
            if n_with_z < n_total:
                filled += self._fill_z_index_mixed(seq)
                continue

            # Case A: 全部带 z，检查是否严格递增
            monotonic = True
            prev: Optional[int] = None
            for _, z in seq:
                assert z is not None
                if prev is not None and z <= prev:
                    monotonic = False
                    break
                prev = z
            if not monotonic:
                # 出现倒挂（典型：v-stack / v-list 重排），保留 z-index
                continue

            # 全部子带 z 且 DOM 顺序严格递增 → 删全部 z-index
            # 例外 1：v-* 虚拟 wrapper（v-list / v-stack / v-row / v-col）
            # 是 LayoutOptimizer 把原本散布在多个 z 层级的兄弟聚合到单一
            # DOM 位置形成的，它们的 z-index 是聚合后的整体叠序锚点，
            # 删除会让 wrapper 被同容器内带数字 z 的其它兄弟（或后续被
            # 删 z 的兄弟）的 DOM 顺序覆盖，造成整块视觉消失。
            # 因此 v-* wrapper 的 z-index 必须保留。
            # 例外 2：有数字后缀的类（如 .candy__40、.text__126）可能是重复类的
            # 一部分，后续 RepeatClassUnifier 会把它们合并。这些类之间的
            # z-index 差异用来确定合并后的相对叠序，不能删除。
            for sel, _ in seq:
                if sel is None:
                    continue
                if self._is_virtual_wrapper_selector(sel):
                    continue
                # ✅ 修复：不删除有数字后缀的类的 z-index
                if sel and '__' in sel and sel.split('__')[-1].isdigit():
                    # 这是个重复类 (.base__N 形式)，可能被后续合并，保留 z-index
                    continue
                rule = self.css_rules.get(sel)
                if rule and 'z-index' in rule:
                    del rule['z-index']
                    pruned += 1

        # ----- Pass 2：处理直接子全 auto 但后代含 z 的容器（Case C c2）-----
        # 必须在 Pass 1 完成后做：Pass 1 给子补的新 z 会让外层
        # _has_z_descendant 返回 True，从而发现穿透风险。
        for parent in self.soup.find_all():
            children = list(parent.find_all(recursive=False))
            if not children:
                continue

            seq2: List[Tuple[Optional[str], Optional[int]]] = []
            for c in children:
                cls = self._first_class(c)
                sel = f'.{cls}' if cls else None
                z = self._read_z(sel) if sel else None
                seq2.append((sel, z))

            n_with_z2 = sum(1 for _, z in seq2 if z is not None)
            # 只关心"直接子全 auto"的容器（其它形态在 Pass 1 已处理）
            if n_with_z2 != 0:
                continue
            # 探测后代是否含 z（穿透风险）
            if not any(self._has_z_descendant(c) for c in children):
                continue
            filled += self._fill_z_index_mixed(seq2)

        self.stats['z_index_pruned'] = pruned
        self.stats['z_index_filled'] = filled

    def _fill_z_index_mixed(self, seq: List[Tuple[Optional[str], Optional[int]]]) -> int:
        """混合状态下按 DOM 顺序为所有兄弟补/调整 z-index。

        ⚠️ **核心目标：消除"数字 z 兄弟 + auto 兄弟"的混合状态。**
        positioned 元素带数字 ``z-index`` 始终在 ``z-index: auto`` 兄弟之上
        （与 DOM 顺序无关），所以一旦发现混合，**所有**兄弟（含 v-* wrapper）
        都必须显式有 z-index，才能让 DOM 顺序兜底视觉叠序。

        策略：
          沿 DOM 顺序遍历，维护 cursor（严格递增）：
            - 已有 z 且 z > cursor → 保留原 z，cursor = z
            - 已有 z 但 z <= cursor → **保留原 z**（v-* / 普通子均如此）；
              cursor 取 max(cursor, z)，确保后续兄弟 z 都更大
            - 无 z（auto） → 查找子元素z-index：
              * 若有子元素带z-index，取最小值；
              * 否则分配 cursor + 1；
              写回 css_rules（含 v-*）

        为什么 v-* 也必须写入 z：
          v-* wrapper（v-stack/v-col/v-row）若保持 auto，会被同容器里
          带数字 z 的普通兄弟反盖到下方。典型反例：
          parent.children = [v-stack(auto), bg(z=10)] → bg 永远在 v-stack 上。
          但 DOM 顺序上 bg 在 v-stack 之后，本意是"bg 在 v-stack 之上"——
          DOM 顺序与 z 数字恰巧吻合，所以视觉无碍。
          反例：parent.children = [v-stack(auto), bg(z=10), card(auto)]
          → bg 永远在 card 之上，但 DOM 上 card 在 bg 之后，本意"card 在 bg 之上"
          → 视觉错位。补 z 后变成 [v-stack(z=0), bg(z=10), card(z=11)] → 正确。

        为什么已有 z 都保留原值不下调：
          上游 LayoutOptimizer / cluster bg 修正循环故意分配的 z 表达了
          视觉叠序关系；若下调会破坏跨"非直接兄弟"的视觉一致性。

        返回值：本次新增/修改的字段数。
        """
        modified = 0
        cursor = -1
        for sel, z in seq:
            if sel is None:
                cursor += 1
                continue
            if z is not None:
                # 保留原 z；cursor 跟随推进
                cursor = max(cursor, z)
                continue
            # 无 z（auto）→ 先查找子元素 z-index，然后补 z 值（含 v-* wrapper）
            rule = self.css_rules.get(sel)
            if rule is None:
                continue
            
            # 尝试从子元素找最小 z-index
            child_min_z = self._find_children_min_z_index(sel)
            if child_min_z is not None:
                target = child_min_z
            else:
                target = cursor + 1
            
            cursor = max(cursor, target)
            rule['z-index'] = str(target)
            modified += 1
        return modified

    def _find_children_min_z_index(self, parent_sel: str) -> Optional[int]:
        """通过遍历 DOM 找出父元素的直接子元素中的最小 z-index。
        
        原理：使用 BeautifulSoup 遍历 DOM 树，找到与 parent_sel 匹配的元素，
        然后遍历其所有直接子元素，查找带 z-index 的最小值。
        
        Args:
            parent_sel: 父选择器，如 '.wuzi-bg-5-stack'（必须以 . 开头）
        
        Returns:
            子元素中的最小 z-index（整数）；若无子元素或无 z-index 则返回 None
        """
        if not parent_sel or not parent_sel.startswith('.') or not self.soup:
            return None
        
        parent_class = parent_sel[1:]  # 去掉 '.'
        
        try:
            from bs4 import NavigableString
        except ImportError:
            return None
        
        # 在 DOM 中查找所有具有该类名的元素
        parent_elements = self.soup.find_all(class_=parent_class)
        if not parent_elements:
            return None
        
        min_z = None
        
        # 遍历每个匹配的父元素
        for parent_elem in parent_elements:
            # 遍历该父元素的直接子元素（children，不是 descendants）
            for child_elem in parent_elem.children:
                # 跳过文本节点
                if isinstance(child_elem, NavigableString):
                    continue
                
                # 获取子元素的类名
                child_classes = child_elem.get('class', [])
                if not child_classes:
                    continue
                
                # 取第一个类名作为 CSS 选择器
                child_class = child_classes[0]
                child_sel = f'.{child_class}'
                
                # 查找该类名的 CSS 规则，看是否有 z-index
                css_props = self.css_rules.get(child_sel)
                if css_props is None:
                    continue
                
                z_str = css_props.get('z-index')
                if z_str is None or z_str == 'auto':
                    continue
                
                try:
                    z_val = int(str(z_str).strip())
                    if min_z is None or z_val < min_z:
                        min_z = z_val
                except (ValueError, TypeError):
                    continue
        
        return min_z

    @staticmethod
    def _is_virtual_wrapper_selector(sel: str) -> bool:
        """判定选择器是否是 LayoutOptimizer 产出的虚拟 wrapper。

        virtual id 格式：``v-{kind}-{N}``，所以选择器形如 ``.v-list-1`` /
        ``.v-stack-3`` / ``.v-row-2`` / ``.v-col-5``；首类名以 ``.v-`` 开头
        即视为虚拟 wrapper，其 z-index 不参与 prune。
        """
        return sel.startswith('.v-')

    # ------------------------------------------------------------------
    # Pass 2: 等价规则合并（含签名缓存优化）
    # ------------------------------------------------------------------

    def _compute_props_signature(self, props: Dict[str, str]) -> str:
        """计算规则属性的签名哈希，用于快速比较。
        
        优化2-Day6：使用 MD5 哈希替代 tuple 比较，减少大规模规则集合的内存占用。
        对于超大型 CSS（1000+ 规则），tuple 比较会产生大量临时对象；
        哈希方式可以显著降低内存峰值和比较时间。
        """
        props_id = id(props)
        if props_id in self._signature_cache:
            return self._signature_cache[props_id]
        
        # 构建规范化的签名字符串：按 key 排序
        items = sorted(props.items())
        sig_str = '|'.join(f'{k}={v}' for k, v in items)
        sig_hash = hashlib.md5(sig_str.encode()).hexdigest()
        
        self._signature_cache[props_id] = sig_hash
        return sig_hash

    def _merge_equivalent_rules(self) -> None:
        """把"属性完全相同"的选择器分到同组。

        - 仅 ``parse_css_to_dict`` 已识别的 .xxx / #xxx 规则参与（``css_rules`` 的 key 已经过滤）
        - 同组内 selector 按字母排序，便于稳定输出
        - ✅ z-index 现在参与签名比较：防止不同 z-index 的规则被错误合并
          （例如 .img__50 z=50 / .img__44 z=44 不应被识别为"等价"而合并）
        - ✅ 优化2-Day6：使用哈希签名替代 tuple，加速大规模规则合并
        """
        # 优化：使用哈希签名而不是 tuple，减少内存占用
        sig_to_selectors: Dict[str, List[str]] = defaultdict(list)
        for sel, props in self.css_rules.items():
            if not props:
                continue
            # 计算规范化签名（已包含所有属性包括 z-index）
            sig = self._compute_props_signature(props)
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
        
        # 缓存清理（Pass 2 完成后不再需要）
        self._signature_cache.clear()
        
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
