"""Handler 组合架构 - 替代 Mixin 继承

本模块提供显式的组合架构，替代原有的 Mixin 多继承。
每个 Handler 都是独立的可测试单元。

使用示例：
    dom_restr = DOMRestructure(soup, css_rules, stats, images_dir)
    
    # 通过 Handler 调用方法
    bg_leaves, fg_leaves = dom_restr.background.extract_leaves(leaves)
    decor_leaves, remaining = dom_restr.tall_decor.extract_leaves(leaves)
    tree = dom_restr.clustering.cluster_and_build(leaves)
    dom_restr.rendering.apply_flex_to_container(group, tree)
"""

from .base import DOMHandler
from .background_handler import BackgroundHandler
from .tall_decor_handler import TallDecorHandler
from .clustering_handler import ClusteringHandler
from .rendering_handler import RenderingHandler
from .reclassify_handler import ReclassifyHandler

__all__ = [
    'DOMHandler',
    'BackgroundHandler',
    'TallDecorHandler',
    'ClusteringHandler',
    'RenderingHandler',
    'ReclassifyHandler',
]
