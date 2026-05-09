"""
布局优化器 - 模块化架构

目录结构（与实际文件一一对应）：
- optimizer.py: 主优化器（协调器：DOMRestructure → SiblingGroupDetector
  → FlexApplier → CssDedup → CssPretty 五段）
- analyzers/
  - layout_analyzer.py: 行/列趋势识别 + V8/V9 堆叠装饰组闸门 + V10 装饰剥离
- transformers/
  - dom_restructure.py: DOM 重构（背景剥离、切行、stack/row/col 切分、容器
    背景吸收 pass、Stack→Col 反向升级、高瘦跨行装饰剥离）
  - sibling_group_detector.py: 同质兄弟分组（识别"平铺的同质卡片"包成
    v-list，让下游开发可写 v-for）
  - flex_applier.py: 趋势元素 → flex margin / 非趋势元素 → absolute；
    保留 v-stack/v-list/v-row/v-col wrapper 的 position 上下文
  - css_dedup.py: z-index 精简 + 等价规则合并（输出 merge_groups）
  - css_pretty.py: CSS 美化渲染（DOM 序排序 + 属性分段 + 合并组多行）
- utils/
  - css_parser.py: 内部辅助；CSS 文本/字典互转走 common/css_utils.py

注意：早期 V3 版的 "Step 1.5 _fix_overflow_after_restructure" 已迁移到源头
（targets/html/codegen/renderers/group_renderer.py 在生成 HTML 时直接判断
组的子元素是否溢出 bbox，溢出则不写 overflow:hidden、不溢出则写）。
本目录不再做 overflow 修复。
"""

from .optimizer import LayoutOptimizer, optimize_layout

__all__ = ['LayoutOptimizer', 'optimize_layout']
