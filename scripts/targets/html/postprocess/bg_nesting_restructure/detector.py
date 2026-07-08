"""背景图层检测模块

识别全覆盖背景图层的条件：
1. 数据类型为 'image' 或 'layer-group'
2. bbox 覆盖 >= 90% 的父容器
3. z-index 在同级中最高
"""

from typing import Any
from bs4 import Tag
import re


# 全局 CSS 缓存，用于解析样式
_css_cache: dict[str, dict[str, str]] = {}


def set_css_cache(css_content: str) -> None:
    """解析 CSS 并构建选择器到样式的映射
    
    Args:
        css_content: CSS 文件内容
    """
    global _css_cache
    _css_cache.clear()
    
    # 改进的 CSS 解析（支持多行样式）
    # 格式: .classname { property: value; ... }
    # re.DOTALL 让 . 匹配换行符，从而支持多行样式块
    # 支持包含连字符的类名（如 .diban-bg__30）
    pattern = r'\.([\w-]+)\s*\{([^}]+)\}'
    
    for match in re.finditer(pattern, css_content, re.DOTALL):
        class_name = match.group(1)
        declarations = match.group(2)
        
        styles = {}
        for decl in declarations.split(';'):
            if ':' not in decl:
                continue
            prop, value = decl.split(':', 1)
            prop = prop.strip()
            value = value.strip()
            if prop and value:  # 只添加非空的属性
                styles[prop] = value
        
        _css_cache[f'.{class_name}'] = styles


def set_css_rules_cache(css_rules: dict[str, dict[str, str]]) -> None:
    """从解析后的 CSS 规则字典初始化缓存。"""
    global _css_cache
    _css_cache.clear()
    for selector, styles in css_rules.items():
        _css_cache[selector] = dict(styles)


def get_css_style(elem: Tag, prop: str) -> str | None:
    """从 CSS 缓存中获取元素的样式属性
    
    Args:
        elem: HTML 元素
        prop: CSS 属性名
    
    Returns:
        属性值，如果找不到则返回 None
    """
    # 优先从元素的 style 属性读取
    style = elem.get('style', '')
    value = _extract_css_property(style, prop)
    if value:
        return value
    
    # 否则从 CSS 缓存读取
    classes = elem.get('class', [])
    if isinstance(classes, list):
        class_names = classes
    else:
        class_names = classes.split()
    
    for class_name in class_names:
        selector = f'.{class_name}'
        if selector in _css_cache:
            value = _css_cache[selector].get(prop)
            if value:
                return value
    
    return None


def get_element_bbox(elem: Tag) -> tuple[int, int, int, int]:
    """获取元素的 bbox (left, top, width, height)"""
    left_str = get_css_style(elem, 'left') or '0px'
    top_str = get_css_style(elem, 'top') or '0px'
    width_str = get_css_style(elem, 'width') or '0px'
    height_str = get_css_style(elem, 'height') or '0px'
    
    left = int(left_str.replace('px', '').strip()) if left_str else 0
    top = int(top_str.replace('px', '').strip()) if top_str else 0
    width = int(width_str.replace('px', '').strip()) if width_str else 0
    height = int(height_str.replace('px', '').strip()) if height_str else 0
    
    return (left, top, width, height)


def get_element_z_index(elem: Tag) -> int:
    """获取元素的 z-index"""
    z_index_str = get_css_style(elem, 'z-index')
    
    if z_index_str:
        try:
            return int(z_index_str.strip())
        except ValueError:
            pass
    
    return 0


def is_fully_opaque(elem: Tag, tolerance: float = 1e-6) -> bool:
    """判断元素是否为完全不透明（opacity == 1）。

    规则：
    - 未显式声明 opacity 视为 1；
    - 仅当 opacity 数值等于 1（容忍极小浮点误差）时返回 True；
    - 解析失败按非 1 处理（保守，不参与背景重组）。
    """
    opacity_str = get_css_style(elem, 'opacity')
    if opacity_str is None or str(opacity_str).strip() == '':
        return True

    try:
        opacity = float(str(opacity_str).strip())
    except (TypeError, ValueError):
        return False

    return abs(opacity - 1.0) <= tolerance


def _parse_style_value(style: str, prop: str) -> int:
    """从 style 字符串中提取数值属性（px）"""
    value_str = _extract_css_property(style, prop)
    if not value_str:
        return 0
    
    # 移除 'px' 后缀并转换为整数
    try:
        return int(value_str.replace('px', '').strip())
    except (ValueError, AttributeError):
        return 0


def _extract_css_property(style: str, prop: str) -> str | None:
    """从 style 字符串中提取 CSS 属性值"""
    # 将 style 分解为键值对
    for declaration in style.split(';'):
        if ':' not in declaration:
            continue
        
        key, value = declaration.split(':', 1)
        key = key.strip()
        value = value.strip()
        
        if key == prop:
            return value
    
    return None


def get_parent_bbox(parent: Tag) -> tuple[int, int, int, int]:
    """获取父容器的 bbox
    
    对于 layer-group，通常需要根据其子元素的范围来计算
    如果没有明确的 style，则假设覆盖整个画布
    """
    style = parent.get('style', '')
    left = _parse_style_value(style, 'left')
    top = _parse_style_value(style, 'top')
    width = _parse_style_value(style, 'width')
    height = _parse_style_value(style, 'height')
    
    # 如果 group 没有设置 width/height，则根据子元素计算范围
    if width == 0 or height == 0:
        children = [c for c in parent.children if isinstance(c, Tag)]
        if children:
            min_left = float('inf')
            min_top = float('inf')
            max_right = float('-inf')
            max_bottom = float('-inf')

            for child in children:
                c_left, c_top, c_width, c_height = get_element_bbox(child)
                min_left = min(min_left, c_left)
                min_top = min(min_top, c_top)
                max_right = max(max_right, c_left + c_width)
                max_bottom = max(max_bottom, c_top + c_height)

            if min_left != float('inf'):
                width = int(max_right - min_left) if max_right > min_left else 0
                height = int(max_bottom - min_top) if max_bottom > min_top else 0

    return (left, top, width, height)


def calculate_overlap_ratio(elem_bbox: tuple[int, int, int, int], 
                            parent_bbox: tuple[int, int, int, int]) -> float:
    """计算元素覆盖父容器的比例
    
    Args:
        elem_bbox: (left, top, width, height)
        parent_bbox: (left, top, width, height)
    
    Returns:
        覆盖比例 [0, 1]
    """
    elem_left, elem_top, elem_width, elem_height = elem_bbox
    parent_left, parent_top, parent_width, parent_height = parent_bbox
    
    if parent_width == 0 or parent_height == 0:
        return 0.0
    
    # 计算重叠区域
    overlap_left = max(elem_left, parent_left)
    overlap_top = max(elem_top, parent_top)
    overlap_right = min(elem_left + elem_width, parent_left + parent_width)
    overlap_bottom = min(elem_top + elem_height, parent_top + parent_height)
    
    if overlap_right <= overlap_left or overlap_bottom <= overlap_top:
        # 没有重叠
        return 0.0
    
    overlap_area = (overlap_right - overlap_left) * (overlap_bottom - overlap_top)
    parent_area = parent_width * parent_height
    
    return overlap_area / parent_area


def calculate_intersection_ratio(
    target_bbox: tuple[int, int, int, int],
    cover_bbox: tuple[int, int, int, int],
) -> float:
    """计算 target 被 cover 重叠的面积比例（以 target 面积为分母）。"""
    t_left, t_top, t_width, t_height = target_bbox
    c_left, c_top, c_width, c_height = cover_bbox

    target_area = t_width * t_height
    if target_area <= 0:
        return 0.0

    overlap_left = max(t_left, c_left)
    overlap_top = max(t_top, c_top)
    overlap_right = min(t_left + t_width, c_left + c_width)
    overlap_bottom = min(t_top + t_height, c_top + c_height)

    if overlap_right <= overlap_left or overlap_bottom <= overlap_top:
        return 0.0

    overlap_area = (overlap_right - overlap_left) * (overlap_bottom - overlap_top)
    return overlap_area / target_area


def is_completely_contained(elem_bbox: tuple[int, int, int, int],
                            container_bbox: tuple[int, int, int, int]) -> bool:
    """判断元素是否被完全包含在容器内
    
    Args:
        elem_bbox: (left, top, width, height)
        container_bbox: (left, top, width, height)
    
    Returns:
        True if elem 完全在 container 内
    """
    elem_left, elem_top, elem_width, elem_height = elem_bbox
    cont_left, cont_top, cont_width, cont_height = container_bbox
    
    elem_right = elem_left + elem_width
    elem_bottom = elem_top + elem_height
    cont_right = cont_left + cont_width
    cont_bottom = cont_top + cont_height
    
    return (
        elem_left >= cont_left and
        elem_top >= cont_top and
        elem_right <= cont_right and
        elem_bottom <= cont_bottom
    )


def is_contained_or_compatible_local(
    elem_bbox: tuple[int, int, int, int],
    container_bbox: tuple[int, int, int, int],
) -> bool:
    """判断元素是否可视为被容器包含。

    兼容两类情况：
    1) 严格包含；
    2) 导出坐标误差导致的高重叠；
    3) 元素坐标已局部化到容器坐标系，但 DOM 仍与容器同级。
    """
    if is_completely_contained(elem_bbox, container_bbox):
        return True

    if calculate_intersection_ratio(elem_bbox, container_bbox) >= 0.985:
        return True

    elem_left, elem_top, elem_width, elem_height = elem_bbox
    cont_left, cont_top, cont_width, cont_height = container_bbox

    if elem_width <= 0 or elem_height <= 0 or cont_width <= 0 or cont_height <= 0:
        return False

    # 局部化同级兼容：elem 的 left/top 看起来像容器内局部坐标，
    # 且原始坐标落在容器左/上方（说明坐标系不一致）。
    if not (elem_left < cont_left - 2 or elem_top < cont_top - 2):
        return False

    if elem_left < 0 or elem_top < 0:
        return False

    if elem_left > cont_width + 6 or elem_top > cont_height + 6:
        return False

    shifted_bbox = (elem_left + cont_left, elem_top + cont_top, elem_width, elem_height)
    return is_completely_contained(shifted_bbox, container_bbox)


def is_background_leaf(elem: Tag, parent: Tag, siblings: list[Tag]) -> bool:
    """判断元素是否是全覆盖背景图层
    
    条件：
    1. 数据类型为 'image' 或 'layer-group'
    2. 覆盖面积 >= 90%
    3. z-index 在同级中最高
    """
    
    # 条件 1：数据类型检查
    data_type = elem.get('data-type', '')
    if data_type not in ['image', 'layer-group']:
        return False

    # 条件 1.5：透明度必须为 1（半透明层不参与背景重组）
    if not is_fully_opaque(elem):
        return False
    
    # 条件 2：覆盖面积检查
    elem_bbox = get_element_bbox(elem)
    parent_bbox = get_parent_bbox(parent)
    coverage = calculate_overlap_ratio(elem_bbox, parent_bbox)
    
    if coverage < 0.90:
        return False
    
    # 条件 3：z-index 检查（两种情况）
    # 情况 A：z-index 最高（遮挡型背景）
    # 情况 B：z-index 最低且覆盖最大（底层背景）
    elem_z = get_element_z_index(elem)
    
    if not siblings:
        return True  # 唯一的子元素
    
    # 获取所有兄弟的 z-index，包括当前元素
    all_z = [elem_z] + [get_element_z_index(s) for s in siblings]
    max_z = max(all_z)
    min_z = min(all_z)
    
    # 情况 A：z-index 最高
    if elem_z == max_z:
        return True
    
    # 情况 B：z-index 最低，且覆盖面积最大
    # 更宽松的条件：z-index 在最低的 5 个数值内，且覆盖最大
    if elem_z <= min_z + 5:  # 允许 z-index 接近最小值
        all_coverages = [coverage] + [
            calculate_overlap_ratio(get_element_bbox(s), parent_bbox)
            for s in siblings
        ]
        max_coverage = max(all_coverages)
        # 覆盖面积是最大的，且明显高于其他元素
        if coverage >= max_coverage - 0.001:  # 允许浮点误差
            return True
    
    return False


def detect_background_leaves(parent: Tag, debug: bool = False) -> list[Tag]:
    """识别 parent 的直接子元素中的背景图层
    
    Args:
        parent: 父元素（通常是 layer-group）
        debug: 是否打印调试信息
    
    Returns:
        背景图层列表
    """
    children = [c for c in parent.children if isinstance(c, Tag)]
    bg_leaves = []
    
    if debug:
        parent_name = parent.get('data-name', parent.get('id', 'unknown'))
        parent_bbox = get_parent_bbox(parent)
        print(f"  🔍 检查父容器: {parent_name}, bbox={parent_bbox}")
    
    for child in children:
        if is_background_leaf(child, parent, children):
            bg_leaves.append(child)
            if debug:
                child_name = child.get('data-name', child.get('id', 'unknown'))
                child_bbox = get_element_bbox(child)
                coverage = calculate_overlap_ratio(child_bbox, get_parent_bbox(parent))
                z_index = get_element_z_index(child)
                print(f"    ✅ 背景图层: {child_name}, bbox={child_bbox}, coverage={coverage:.2%}, z-index={z_index}")
        elif debug:
            child_name = child.get('data-name', child.get('id', 'unknown'))
            data_type = child.get('data-type', 'unknown')
            child_bbox = get_element_bbox(child)
            coverage = calculate_overlap_ratio(child_bbox, get_parent_bbox(parent))
            z_index = get_element_z_index(child)
            print(f"    ❌ 非背景: {child_name}, type={data_type}, bbox={child_bbox}, coverage={coverage:.2%}, z-index={z_index}")
    
    return bg_leaves


def detect_image_absorption_hosts(
    parent: Tag,
    excluded: list[Tag] | None = None,
    debug: bool = False,
) -> list[Tag]:
    """识别可吸收兄弟元素的 image 宿主（局部背景卡片）。

    目的：补充“全覆盖背景”规则无法覆盖的场景，让 ``data-type=image``
    也能在满足几何包含关系时充当吸收目标。
    """
    excluded_set = set(excluded or [])
    children = [c for c in parent.children if isinstance(c, Tag)]
    parent_bbox = get_parent_bbox(parent)
    _, _, parent_w, parent_h = parent_bbox
    parent_area = max(parent_w * parent_h, 1)

    hosts: list[Tag] = []

    for child in children:
        if child in excluded_set:
            continue

        if child.get('data-type', '') != 'image':
            continue

        # 半透明 image 不作为吸收宿主，避免把兄弟元素错误吸收进叠加层
        if not is_fully_opaque(child):
            continue

        bbox = get_element_bbox(child)
        _, _, width, height = bbox
        if width <= 0 or height <= 0:
            continue

        coverage = calculate_overlap_ratio(bbox, parent_bbox)
        parent_left, parent_top, parent_w, _ = parent_bbox

        # 过滤接近全屏且贴顶的大场景图层，避免误吸收（应由背景叶子规则处理）
        # 仅高覆盖还不够：像 rounded__368/392 这类下移卡片背景仍应允许吸收。
        if coverage >= 0.75 and bbox[1] <= parent_top + 8:
            continue

        # 过滤“贴左上角 + 近满宽”的场景图（如 shape__3），避免把顶部装饰/文案误吸收
        if (
            parent_w > 0
            and width >= int(parent_w * 0.98)
            and bbox[0] <= parent_left + 2
            and bbox[1] <= parent_top + 2
        ):
            continue

        # 过滤微小装饰图层，降低误吸收风险
        area = width * height
        if area < int(parent_area * 0.08):
            continue

        child_z = get_element_z_index(child)

        # 若该候选几乎被更高层 image 兄弟覆盖，则跳过（避免底层大图误吸收）
        overshadowed_by_higher_image = any(
            sibling.get('data-type', '') == 'image'
            and sibling is not child
            and get_element_z_index(sibling) > child_z
            and calculate_intersection_ratio(bbox, get_element_bbox(sibling)) >= 0.92
            for sibling in children
        )
        if overshadowed_by_higher_image:
            continue

        siblings = [s for s in children if s is not child and s not in excluded_set]
        contained = [
            s for s in siblings
            if is_contained_or_compatible_local(get_element_bbox(s), bbox)
        ]
        if not contained:
            continue

        contained_groups = [s for s in contained if s.get('data-type', '') == 'group']
        contained_layers = [s for s in contained if s.get('data-type', '') != 'group']

        # 当仅包含 group 时，按覆盖率区分：
        # - 接近全屏的大场景图（高覆盖）仍禁止成为宿主（如 shape__3）
        # - 中小型卡片背景允许成为宿主（如 rounded__335）
        if len(contained_layers) == 0:
            # 仅在“高覆盖且贴顶”时拒绝 group-only 宿主，避免误伤中部任务卡片背景
            if coverage >= 0.55 and bbox[1] <= parent_top + 8:
                continue

        # 至少命中一个组，或命中>=2个普通兄弟，避免把局部 icon 当宿主。
        # 特例：按钮底图常见“1个 text”兄弟（如“已领取”），允许作为宿主。
        allow_single_text_layer = (
            len(contained_layers) == 1
            and contained_layers[0].get('data-type', '') == 'text'
        )
        if not contained_groups and len(contained_layers) < 2 and not allow_single_text_layer:
            continue

        higher_count = sum(1 for s in contained if get_element_z_index(s) >= child_z)
        if higher_count == 0:
            continue

        hosts.append(child)

        if debug:
            child_name = child.get('data-name', child.get('id', 'unknown'))
            print(
                f"    ✅ image宿主: {child_name}, bbox={bbox}, "
                f"contained={len(contained)}, z-index={child_z}"
            )

    return hosts
