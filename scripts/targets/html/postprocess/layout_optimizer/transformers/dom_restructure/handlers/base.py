"""Handler 基类 - 提供所有 Handler 的共同接口和工具方法

所有具体的 Handler（BackgroundHandler、ClusteringHandler 等）都继承这个基类，
获得对 DOMRestructure 主对象的统一访问接口。
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..restructure import DOMRestructure


class DOMHandler:
    """DOM 重构 Handler 基类
    
    所有 Handler 都持有对主 DOMRestructure 对象的引用，通过它访问：
    - self.owner.soup: BeautifulSoup 对象
    - self.owner.css_rules: CSS 规则字典
    - self.owner.parser: CSS 解析器
    - self.owner.config: 配置对象
    - self.owner.stats: 统计信息
    - self.owner.images_dir: 图片目录
    
    以及使用其他 Handler 的方法（通过 self.owner 的属性）。
    """

    def __init__(self, owner: "DOMRestructure"):
        """
        Args:
            owner: 主 DOMRestructure 对象
        """
        self.owner = owner

    # ------------------------------------------------------------------
    # 便捷属性访问
    # ------------------------------------------------------------------

    @property
    def soup(self):
        """BeautifulSoup 文档对象"""
        return self.owner.soup

    @property
    def css_rules(self):
        """CSS 规则字典"""
        return self.owner.css_rules

    @property
    def parser(self):
        """CSS 解析器"""
        return self.owner.parser

    @property
    def config(self):
        """配置对象"""
        return self.owner.config

    @property
    def stats(self):
        """统计信息"""
        return self.owner.stats

    @property
    def images_dir(self):
        """图片目录"""
        return self.owner.images_dir

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _next_virtual_id(self, kind: str) -> str:
        """生成下一个虚拟 ID"""
        return self.owner._next_virtual_id(kind)

    def _envelope(self, bboxes):
        """计算 bbox 列表的外包络"""
        return self.owner._envelope(bboxes)
