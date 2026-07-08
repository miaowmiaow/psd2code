"""背景图层嵌套重组模块

功能：识别全覆盖背景图层，将同级兄弟元素重新嵌入到背景图层内部

流程：
1. 识别每个 group 中的全覆盖背景图层（90% 覆盖 + image/layer-group 类型）
2. 构建背景图层间的嵌套关系图
3. 将其他元素按包含关系（完全包含）拉入对应的背景图层
4. 在 DOM 中添加 data-bg-nested 标记
"""

from .restructurer import restructure_by_bg_nesting

__all__ = ['restructure_by_bg_nesting']
