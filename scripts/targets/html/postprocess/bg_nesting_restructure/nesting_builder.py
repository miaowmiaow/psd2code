"""构建背景图层嵌套关系图"""

from typing import Any
from bs4 import Tag
from .detector import get_element_bbox, is_completely_contained


def build_bg_containment_graph(bg_leaves: list[Tag]) -> dict[Tag, list[Tag]]:
    """构建背景图层间的包含关系图
    
    Args:
        bg_leaves: 背景图层列表
    
    Returns:
        字典：key = 背景图层，value = 它包含的其他背景图层
    """
    graph: dict[Tag, list[Tag]] = {}
    
    for bg in bg_leaves:
        contained = []
        bg_bbox = get_element_bbox(bg)
        
        for other_bg in bg_leaves:
            if other_bg is bg:
                continue
            
            other_bbox = get_element_bbox(other_bg)
            if is_completely_contained(other_bbox, bg_bbox):
                contained.append(other_bg)
        
        graph[bg] = contained
    
    return graph


def find_containing_bg(elem: Tag, bg_leaves: list[Tag]) -> Tag | None:
    """找到包含该元素的最小背景图层
    
    Args:
        elem: 待查找的元素
        bg_leaves: 背景图层列表
    
    Returns:
        最小的包含背景图层（面积最小），如果没有则返回 None
    """
    elem_bbox = get_element_bbox(elem)
    candidates = []
    
    for bg_leaf in bg_leaves:
        bg_bbox = get_element_bbox(bg_leaf)
        
        # 严格的完全包含判定
        if is_completely_contained(elem_bbox, bg_bbox):
            candidates.append(bg_leaf)
    
    if not candidates:
        return None
    
    # 返回面积最小的包含者
    def get_area(bbox):
        _, _, w, h = bbox
        return w * h
    
    return min(candidates, key=lambda b: get_area(get_element_bbox(b)))


def order_bg_by_hierarchy(bg_leaves: list[Tag]) -> list[Tag]:
    """按层级顺序排列背景图层
    
    背景图层可能相互包含，需要按照包含关系排序：
    - 最外层的背景图层放在最前
    - 被包含的背景图层放在后
    
    Args:
        bg_leaves: 背景图层列表
    
    Returns:
        排序后的背景图层列表
    """
    # 构建包含关系图
    graph = build_bg_containment_graph(bg_leaves)
    
    # 计算每个背景的"包含深度"
    # 深度 = 有多少个其他背景包含它
    depth_map = {}
    
    for bg in bg_leaves:
        depth = 0
        for other_bg, contained in graph.items():
            if bg in contained:
                depth += 1
        depth_map[bg] = depth
    
    # 按深度升序排列（外层优先）
    return sorted(bg_leaves, key=lambda b: depth_map[b])
