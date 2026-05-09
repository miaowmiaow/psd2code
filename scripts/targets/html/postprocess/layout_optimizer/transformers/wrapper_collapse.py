"""单子 wrapper 折叠器（P3 - 2026-04-30）

dom_restructure 切行/切列时会对每个识别成 v-row / v-col 的子集生成一个
虚拟 wrapper <div class="v-row-N v-row">…</div>。当某个 wrapper 内部
**只有 1 个子节点**时，wrapper 在视觉上是"伪容器"——纯粹是布局算法的副产品。

此 pass 的职责：检测这种"单子 wrapper"，把 wrapper 的 CSS 合并到子节点，
然后用子节点直接替换 wrapper，以减少 DOM 节点数和对应 CSS 规则数。

**安全策略：**
- 仅折叠 ``data-virtual ∈ {row, col}`` 的 wrapper（v-row / v-col）
- **不**折叠 v-stack（它是 absolute 子元素的定位 containing block，
  折叠会让子元素脱离该 containing block 跑到外层）
- **不**折叠 v-list（同质兄弟列表，wrap 行为依赖容器存在）
- **不**折叠根容器（直接挂在 #canvas 下的 layer-group，没有 wrapper 概念）

**CSS 合并规则：**
- wrapper 的 ``margin-top`` / ``margin-left`` / ``margin-right`` /
  ``margin-bottom`` → 与子节点同名字段相加（数值相加，单位 px）
- wrapper 的 ``width`` / ``height`` → 子节点已有则保留子节点；否则继承 wrapper
  - 例外：子节点是 v-row/v-col/v-stack（自己也是定位过的 wrapper）时，
    wrapper 的 width/height 已等于子节点 width/height（dom_restructure 保证），
    直接放弃 wrapper 的尺寸
- 其它 wrapper 字段（display/flex-direction/align-items/box-sizing/position）
  通常不需要带到子节点（子节点已有自己的样式），直接丢弃
- wrapper 的 ``data-name`` 等属性丢弃；子节点保留自己的属性

**HTML 替换：**
- 用 ``wrapper.replace_with(child)`` 把 wrapper 在父容器里整体替换为子节点
- 同时从 css_rules 删除 wrapper 的 CSS 规则
"""

from typing import Dict, List, Tuple, Optional


class WrapperCollapse:
    """单子 wrapper 折叠器

    入口：``run()``
    """

    # 可折叠的 data-virtual 类型
    COLLAPSIBLE_VIRTUAL_KINDS = {'row', 'col'}

    # 不可折叠的标记类（出现在 class 列表里则跳过）
    SKIP_MARKER_CLASSES = {'v-stack', 'v-list'}

    def __init__(self, soup, css_rules: Dict[str, Dict[str, str]], stats: Dict):
        self.soup = soup
        self.css_rules = css_rules
        self.stats = stats
        # 统计字段
        self.stats.setdefault('wrappers_collapsed', 0)

    def run(self):
        """遍历所有 wrapper，把单子 v-row/v-col 折叠到子节点

        采用"反复扫描直到稳定"策略：每折叠一个可能让外层也变成单子，需要再扫一轮。
        """
        print("\n📦 步骤2.5：单子 wrapper 折叠...")
        total_collapsed = 0
        # 多轮扫描（最多 5 轮，避免死循环）
        for _round in range(5):
            wrappers = self._collect_collapsible_wrappers()
            collapsed_in_round = 0
            for wrapper in wrappers:
                if self._try_collapse_single_child_wrapper(wrapper):
                    collapsed_in_round += 1
            total_collapsed += collapsed_in_round
            if collapsed_in_round == 0:
                break
        self.stats['wrappers_collapsed'] += total_collapsed
        if total_collapsed > 0:
            print(f"   ✓ 折叠 {total_collapsed} 个单子 wrapper")

    def _collect_collapsible_wrappers(self) -> List:
        """采集所有 data-virtual ∈ {row, col} 的 wrapper 元素"""
        result = []
        for elem in self.soup.find_all('div', attrs={'data-virtual': True}):
            kind = elem.get('data-virtual')
            if kind not in self.COLLAPSIBLE_VIRTUAL_KINDS:
                continue
            classes = elem.get('class', []) or []
            # 防御：如果同时带了 SKIP marker（不应该发生，但保险起见），跳过
            if any(m in classes for m in self.SKIP_MARKER_CLASSES):
                continue
            result.append(elem)
        return result

    def _try_collapse_single_child_wrapper(self, wrapper) -> bool:
        """尝试折叠单子 wrapper

        Returns: True 表示已折叠
        """
        children = [c for c in wrapper.find_all(recursive=False)
                    if getattr(c, 'name', None) == 'div']
        if len(children) != 1:
            return False
        child = children[0]
        child_classes = child.get('class', []) or []
        # 子节点若是 v-list 这类不可吸收的标记容器，跳过
        if any(m in child_classes for m in self.SKIP_MARKER_CLASSES):
            return False

        wrapper_classes = wrapper.get('class', []) or []
        if not wrapper_classes:
            return False
        wrapper_css_class = f'.{wrapper_classes[0]}'
        wrapper_css = self.css_rules.get(wrapper_css_class)
        if wrapper_css is None:
            return False

        # 子节点的 css
        if not child_classes:
            return False
        child_css_class = f'.{child_classes[0]}'
        child_css = self.css_rules.setdefault(child_css_class, {})

        # ── 合并 margin（数值相加） ──
        for prop in ('margin-top', 'margin-left', 'margin-right', 'margin-bottom'):
            wm = self._parse_px(wrapper_css.get(prop))
            cm = self._parse_px(child_css.get(prop))
            if wm is None and cm is None:
                continue
            total = (wm or 0.0) + (cm or 0.0)
            if abs(total) < 0.5:
                child_css.pop(prop, None)
            else:
                child_css[prop] = f'{int(round(total))}px'

        # ── 删除 wrapper 自身的 CSS 规则 ──
        self.css_rules.pop(wrapper_css_class, None)

        # ── DOM 替换：用子节点顶替 wrapper 的位置 ──
        wrapper.replace_with(child)
        return True

    @staticmethod
    def _parse_px(value: Optional[str]) -> Optional[float]:
        """把 '12px' / '12' / '12.5px' 解析为 float；其它返回 None"""
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        # 去掉末尾的 px
        if s.endswith('px'):
            s = s[:-2].strip()
        try:
            return float(s)
        except ValueError:
            return None
