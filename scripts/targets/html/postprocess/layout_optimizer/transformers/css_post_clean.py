"""结构感知型 CSS 后处理清理（CssPostClean）— Step 3.9

为什么需要这一步
================

CssDedup（Step 3）是纯属性层面的操作，无需读 DOM 结构。但有一类冗余
属性必须结合 DOM 上下文才能安全判断是否可删——即 flex 子项上的"三件套"：

  1. ``position: relative``（无任何偏移量）
  2. ``z-index: NNN``（来自 PSD 全局导出序号，不代表堆叠意图）
  3. ``flex-shrink: 0``（已有固定 width，不会被压缩）

这三个属性在以下场景下同时存在，且视觉上完全无效：

  - 父容器是 ``display: flex``（flex 子项）
  - 元素自身 ``position: relative`` 但无 ``left / top / right / bottom``
  - 元素没有绝对定位的直接子元素（所以不需要提供包含块）
  - ``z-index`` 值是 PSD 导出序号（10~500 之间的大整数）

由于需要读 DOM 判断父子关系，这一步必须放在所有改名/合并完成之后、
CssPretty 渲染之前运行。

时序约束
========

- **必须在 SemanticRename（Step 3.7）之后**：类名已是最终态，DOM 与
  css_rules 的类名映射稳定。
- **必须在 FlexApplier（Step 2）之后**：FlexApplier 才写入 ``display: flex``，
  之前无法判断哪些是 flex 容器。
- **必须在 CssPretty（Step 4）之前**：CssPretty 读 css_rules 生成最终 CSS，
  需要先清理。

Pass 说明
=========

**Pass A —— flex 子项三件套清理（核心）**
    1. 找到所有 ``display: flex`` 的容器 class
    2. 遍历其 DOM 直接子元素
    3. 若子元素满足以下全部条件：
       a. ``position: relative``
       b. 无 ``left / top / right / bottom`` 偏移量
       c. 无 ``position: absolute`` 的直接子元素（不作为绝对定位包含块使用）
    则删除：``position``、``z-index``、``flex-shrink``

**Pass B —— z-index: 0 清理**
    v-stack / v-row 等虚拟 wrapper 在生成时被赋予 ``z-index: 0``（来自
    LayoutOptimizer 的 stacking context 初始值），配合 ``position: relative``。
    这些 wrapper 作为 flex 布局的结构节点，不参与任何视觉层叠，z-index:0
    完全无效。判断条件：``position: relative`` + ``z-index: 0``（不含偏移量）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Set


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class CssPostCleanConfig:
    """CssPostClean 的功能开关。"""
    enabled: bool = True
    # Pass A：flex 子项三件套清理
    clean_flex_child_triple: bool = True
    # Pass B：z-index:0 清理
    clean_zero_z_index: bool = True


# ---------------------------------------------------------------------------
# 辅助常量
# ---------------------------------------------------------------------------

# 定位偏移属性，任意一个存在则说明 position 有实际用途
_OFFSET_PROPS: Set[str] = {'left', 'top', 'right', 'bottom'}


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class CssPostClean:
    """结构感知型 CSS 后处理清理器。

    使用方式::

        cleaner = CssPostClean(soup, css_rules, stats)
        cleaner.run()
    """

    def __init__(self, soup, css_rules: Dict[str, Dict[str, str]], stats: Dict,
                 config: Optional[CssPostCleanConfig] = None):
        self.soup = soup
        self.css_rules = css_rules
        self.stats = stats
        self.config = config or CssPostCleanConfig()
        self.stats.setdefault('post_clean_flex_triple_removed', 0)
        self.stats.setdefault('post_clean_zero_z_removed', 0)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _first_class(self, element) -> Optional[str]:
        """取元素 class 列表的第一个值（语义类）。"""
        cls = element.get('class') if hasattr(element, 'get') else None
        return cls[0] if cls else None

    def _selector(self, cls_name: str) -> str:
        return f'.{cls_name}'

    def _props(self, cls_name: str) -> Optional[Dict[str, str]]:
        return self.css_rules.get(self._selector(cls_name))

    def _has_absolute_child(self, element) -> bool:
        """检查元素是否有 position:absolute 的直接子元素。"""
        for child in element.find_all(recursive=False):
            child_cls = self._first_class(child)
            if not child_cls:
                continue
            child_props = self._props(child_cls)
            if child_props and child_props.get('position') == 'absolute':
                return True
        return False

    # ------------------------------------------------------------------
    # Pass A: flex 子项三件套清理
    # ------------------------------------------------------------------

    def _clean_flex_child_triple(self) -> None:
        """删除 flex 子项上无效的 position/z-index/flex-shrink 三件套。

        安全条件（同时满足才删）：
          1. 父容器是 display:flex
          2. 子元素有 position:relative
          3. 子元素无偏移属性（left/top/right/bottom）
          4. 子元素无 position:absolute 的直接子元素
             （若有，position:relative 是必须的包含块）
        """
        removed = 0

        for container in self.soup.find_all(True):
            cls = self._first_class(container)
            if not cls:
                continue
            props = self._props(cls)
            if not props:
                continue
            # 只处理 flex 容器
            if props.get('display') != 'flex':
                continue

            for child in container.find_all(recursive=False):
                child_cls = self._first_class(child)
                if not child_cls:
                    continue
                child_props = self._props(child_cls)
                if not child_props:
                    continue

                # 条件检查
                if child_props.get('position') != 'relative':
                    continue
                if any(k in child_props for k in _OFFSET_PROPS):
                    continue  # 有偏移，position 有实际用途
                if self._has_absolute_child(child):
                    continue  # 有绝对定位子，position 作为包含块不可删

                # 满足所有条件，删除三件套
                if 'position' in child_props:
                    del child_props['position']
                    removed += 1
                if 'z-index' in child_props:
                    del child_props['z-index']
                    removed += 1
                if 'flex-shrink' in child_props:
                    del child_props['flex-shrink']
                    removed += 1

        self.stats['post_clean_flex_triple_removed'] = removed

    # ------------------------------------------------------------------
    # Pass B: z-index:0 清理
    # ------------------------------------------------------------------

    def _clean_zero_z_index(self) -> None:
        """删除 position:relative + z-index:0 + 无偏移量的规则中的两者。

        这些是 v-stack / v-row wrapper 的初始 stacking context 标记，
        作为 flex 结构节点不参与任何视觉层叠，z-index:0 完全无效。
        同时由于无偏移，position:relative 也可安全删除。
        """
        removed = 0
        for sel, props in self.css_rules.items():
            if props.get('position') != 'relative':
                continue
            if props.get('z-index') != '0':
                continue
            if any(k in props for k in _OFFSET_PROPS):
                continue  # 有偏移，保留

            # 需要额外确认无绝对定位子元素（通过 DOM 查找）
            # 找到该 selector 对应的所有 DOM 元素
            cls_name = sel.lstrip('.')
            has_abs_child = False
            for el in self.soup.find_all(class_=cls_name):
                if self._has_absolute_child(el):
                    has_abs_child = True
                    break
            if has_abs_child:
                continue

            del props['z-index']
            del props['position']
            removed += 2

        self.stats['post_clean_zero_z_removed'] = removed // 2  # 按"条"计

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    def run(self) -> None:
        if not self.config.enabled:
            return
        if self.config.clean_flex_child_triple:
            self._clean_flex_child_triple()
        if self.config.clean_zero_z_index:
            self._clean_zero_z_index()


__all__ = ['CssPostClean', 'CssPostCleanConfig']
