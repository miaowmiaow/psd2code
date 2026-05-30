"""DOM 重构转换器包

将原 ``dom_restructure.py`` 单体模块按功能边界拆分为子模块：

- ``data_types``: 数据结构 + 配置阈值
- ``background``: 背景剥离 / 吸收 / 多层合并
- ``tall_decor``: 高瘦跨行装饰剥离
- ``clustering``: 空间聚类（行列切分 + 叠图判定）
- ``rendering``: DOM 渲染（wrapper 创建 / flex margin / stack 输出）
- ``reclassify``: Stack → Col 反向升级
- ``restructure``: 主类 ``DOMRestructure``（编排逻辑）
"""

from .restructure import DOMRestructure
from .data_types import BBox, LeafInfo, LayoutNode, ClusterConfig

__all__ = [
    "DOMRestructure",
    "BBox",
    "LeafInfo",
    "LayoutNode",
    "ClusterConfig",
]
