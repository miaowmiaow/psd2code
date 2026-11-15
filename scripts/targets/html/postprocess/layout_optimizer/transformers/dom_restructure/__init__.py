"""DOM 重构转换器包

结构（2026-05 起为 Handler 组合，不再是 Mixin 继承）：

- ``data_types``: 数据结构 + 配置阈值（BBox / LeafInfo / LayoutNode / ClusterConfig）
- ``handlers/``: 五个职责 Handler
    - ``background_handler``: 背景剥离 / 吸收 / 多层合并
    - ``tall_decor_handler``: 高瘦跨行装饰剥离
    - ``clustering_handler``: 空间聚类（行列切分 + 叠图判定）
    - ``rendering_handler``: DOM 渲染（wrapper 创建 / flex margin / stack 输出）
    - ``reclassify_handler``: Stack → Col 反向升级 / 容器背景吸收 pass
- ``restructure``: 主类 ``DOMRestructure``（编排逻辑，持有各 Handler 实例）

> 历史混入模块 ``background.py`` / ``clustering.py`` / ``rendering.py`` /
> ``reclassify.py`` / ``tall_decor.py`` 已于 2026-11 删除（重构后零引用，
> 且与 ``handlers/`` 实现漂移）。
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
