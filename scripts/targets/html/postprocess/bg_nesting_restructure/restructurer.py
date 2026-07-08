"""背景图层嵌套重组核心逻辑"""

from bs4 import BeautifulSoup, Tag

from .detector import (
    detect_background_leaves,
    detect_image_absorption_hosts,
    get_css_style,
    get_element_bbox,
    get_element_z_index,
    is_completely_contained,
    is_contained_or_compatible_local,
    set_css_cache,
    set_css_rules_cache,
)
from .nesting_builder import (
    build_bg_containment_graph,
    find_containing_bg,
    order_bg_by_hierarchy,
)


def restructure_by_bg_nesting(
    html_content: str,
    css_content: str = "",
    css_rules: dict[str, dict[str, str]] | None = None,
    debug: bool = False,
) -> str:
    """对 HTML 进行背景图层嵌套重组
    
    流程：
    1. 初始化 CSS 缓存
    2. 解析 HTML
    3. 遍历所有 layer-group 元素
    4. 识别背景图层
    5. 构建嵌套关系图
    6. 重组元素结构
    7. 添加标记
    8. 序列化 HTML
    
    Args:
        html_content: 原始 HTML 内容
        css_content: CSS 文件内容（用于解析样式）
        css_rules: 解析后的 CSS 规则；传入后会被原地更新
        debug: 是否打印调试信息
    
    Returns:
        重组后的 HTML 内容
    """
    # 初始化 CSS 缓存
    if css_rules is not None:
        set_css_rules_cache(css_rules)
    elif css_content:
        set_css_cache(css_content)
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 全页面多轮判定：不仅扫描 layer-group，也扫描吸收后可能产生的新容器（如 image 容器）
    max_rounds = 12
    total_restructured = 0

    for round_idx in range(1, max_rounds + 1):
        containers = _collect_candidate_containers(soup)

        if debug:
            print(
                f"📦 第 {round_idx} 轮：扫描 {len(containers)} 个候选容器，开始检查背景图层..."
            )

        round_restructured = 0
        for container in containers:
            count = _restructure_group(container, css_rules=css_rules, debug=debug)
            round_restructured += count

        total_restructured += round_restructured

        if debug:
            print(f"   ↳ 第 {round_idx} 轮完成，重组 {round_restructured} 个元素")

        if round_restructured == 0:
            break

    if debug:
        print(f"✅ 背景图层嵌套重组完成，共处理 {total_restructured} 个元素")

    return str(soup)


def _collect_candidate_containers(soup: BeautifulSoup) -> list[Tag]:
    """收集可参与背景重组判定的容器。

    规则：
    - `#canvas` 始终参与；
    - 其他元素需至少包含一个直接子节点，且该子节点是 layer/layer-group。
    """
    containers: list[Tag] = []

    canvas = soup.find(id='canvas')
    if isinstance(canvas, Tag):
        containers.append(canvas)

    for elem in soup.find_all(True):
        if not isinstance(elem, Tag):
            continue
        if elem is canvas:
            continue

        children = [c for c in elem.children if isinstance(c, Tag)]
        if not children:
            continue

        has_layer_children = any(_is_layer_like(child) for child in children)
        if has_layer_children:
            containers.append(elem)

    return containers


def _is_layer_like(elem: Tag) -> bool:
    classes = _extract_classes(elem)
    return 'layer' in classes or 'layer-group' in classes


def _restructure_group(
    group: Tag,
    css_rules: dict[str, dict[str, str]] | None = None,
    debug: bool = False,
) -> int:
    """重组单个 layer-group
    
    Args:
        group: layer-group 元素
        debug: 是否打印调试信息
    
    Returns:
        重组的元素个数
    """
    # 获取直接子元素（Tag 类型）
    children = [c for c in group.children if isinstance(c, Tag)]
    
    if not children:
        return 0
    
    # 步骤 1：识别全覆盖背景图层
    bg_leaves = detect_background_leaves(group, debug=debug)

    # 步骤 2：识别可吸收的 image 宿主（局部卡片背景等）
    image_hosts = detect_image_absorption_hosts(group, excluded=bg_leaves, debug=debug)

    if not bg_leaves and not image_hosts:
        return 0

    # 步骤 3：按层级排序背景图层（处理多个背景相互包含的情况）
    ordered_bg = order_bg_by_hierarchy(bg_leaves) if bg_leaves else []

    # 步骤 4：构建背景图层间的包含关系
    bg_hierarchy = build_bg_containment_graph(ordered_bg) if ordered_bg else {}

    # 步骤 5：处理背景图层间的嵌套
    _restructure_bg_hierarchy(ordered_bg, bg_hierarchy)

    # 步骤 6：获取最终的"有效背景图层"（经过重组后）
    effective_bg = _get_effective_bg_leaves(ordered_bg, bg_hierarchy) if ordered_bg else []

    # 步骤 7：处理普通元素到对应吸收目标的嵌套
    absorption_hosts = [*effective_bg, *image_hosts]
    non_bg_children = [c for c in children if c not in absorption_hosts]

    restructured_count = 0
    for non_bg_child in non_bg_children:
        target_bg = _pick_absorption_host(
            non_bg_child,
            image_hosts=image_hosts,
            fallback_bg_hosts=effective_bg,
        )

        if target_bg:
            # 将 non_bg_child 从 group.children 中移出
            # 添加到 target_bg.children 中
            _move_element(
                non_bg_child,
                target_bg,
                css_rules=css_rules,
                mark_nested=True,
            )
            restructured_count += 1

            if debug:
                non_bg_name = non_bg_child.get('data-name', non_bg_child.get('id', 'unknown'))
                bg_name = target_bg.get('data-name', target_bg.get('id', 'unknown'))
                print(f"    🔄 移动: {non_bg_name} → {bg_name}")

    return restructured_count


def _pick_absorption_host(
    elem: Tag,
    image_hosts: list[Tag],
    fallback_bg_hosts: list[Tag],
) -> Tag | None:
    """为元素选择最合适的吸收宿主。

    选择策略：
    1) 优先从 image 宿主中选择（覆盖局部卡片背景场景）；
    2) image 宿主按 z-index 由高到低、面积由小到大排序；
    3) 若无 image 宿主命中，则回退到全覆盖背景宿主。
    """
    elem_bbox = get_element_bbox(elem)
    elem_z = get_element_z_index(elem)

    image_candidates = [
        host for host in image_hosts
        if _is_contained_or_nearly(elem_bbox, get_element_bbox(host))
        and get_element_z_index(host) <= elem_z
    ]

    if image_candidates:
        return min(
            image_candidates,
            key=lambda host: (
                -get_element_z_index(host),
                _bbox_area(get_element_bbox(host)),
            ),
        )

    fallback_candidates = [
        host for host in fallback_bg_hosts
        if _is_contained_or_nearly(elem_bbox, get_element_bbox(host))
        and get_element_z_index(host) <= elem_z
    ]
    if not fallback_candidates:
        return None

    return min(
        fallback_candidates,
        key=lambda host: (
            -get_element_z_index(host),
            _bbox_area(get_element_bbox(host)),
        ),
    )


def _bbox_area(bbox: tuple[int, int, int, int]) -> int:
    _, _, width, height = bbox
    return max(width, 0) * max(height, 0)


def _is_contained_or_nearly(
    elem_bbox: tuple[int, int, int, int],
    host_bbox: tuple[int, int, int, int],
) -> bool:
    """判断元素是否可视为被宿主包含。"""
    return is_contained_or_compatible_local(elem_bbox, host_bbox)


def _restructure_bg_hierarchy(bg_leaves: list[Tag],
                              bg_hierarchy: dict[Tag, list[Tag]]) -> None:
    """处理背景图层间的嵌套关系
    
    如果背景图层 A 包含背景图层 B，则将 B 从 group 中移出，添加到 A 中。
    
    Args:
        bg_leaves: 排序后的背景图层列表
        bg_hierarchy: 包含关系图
    """
    for bg in bg_leaves:
        contained_bgs = bg_hierarchy.get(bg, [])
        
        for contained_bg in contained_bgs:
            # z-index 防护：仅允许宿主层级 <= 被吸收层级，避免反向层级嵌套
            if get_element_z_index(contained_bg) < get_element_z_index(bg):
                continue

            # 几何防护：执行移动前再次确认“真实包含”仍成立
            # 避免图构建阶段的误判把并列背景错误父子化
            if not is_completely_contained(
                get_element_bbox(contained_bg),
                get_element_bbox(bg),
            ):
                continue

            # 检查 contained_bg 是否仍然在原 group 中
            # （可能已经被前面的操作移出了）
            if contained_bg.parent == bg.parent:
                _move_element(contained_bg, bg, css_rules=None, mark_nested=True)


def _move_element(
    elem: Tag,
    target_parent: Tag,
    css_rules: dict[str, dict[str, str]] | None = None,
    mark_nested: bool = False,
) -> None:
    """将元素从原位置移到目标父容器中。

    通用坐标修正：
    当元素被吸收到新的父容器时，统一将元素 ``left/top`` 从旧父坐标系
    换算到目标父坐标系（写回对应 class 的 CSS 规则），以保持视觉位置稳定。

    Args:
        elem: 要移动的元素
        target_parent: 目标父容器
        mark_nested: 是否添加 data-bg-nested 标记
    """
    if css_rules is not None:
        elem_left = _parse_px(get_css_style(elem, 'left'))
        elem_top = _parse_px(get_css_style(elem, 'top'))
        target_left = _parse_px(get_css_style(target_parent, 'left'))
        target_top = _parse_px(get_css_style(target_parent, 'top'))

        new_left = elem_left - target_left
        new_top = elem_top - target_top
        _update_css_left_top(elem, new_left, new_top, css_rules)

    # 从原位置移出
    elem.extract()

    # 添加到目标位置
    target_parent.append(elem)

    # 添加标记
    if mark_nested:
        elem['data-bg-nested'] = 'true'


def _parse_px(value: str | None) -> int:
    if not value:
        return 0
    try:
        return int(str(value).replace('px', '').strip())
    except (TypeError, ValueError):
        return 0


def _update_css_left_top(
    elem: Tag,
    left: int,
    top: int,
    css_rules: dict[str, dict[str, str]] | None,
) -> None:
    """更新元素 class 对应的 CSS 规则中的 left/top。"""
    if css_rules is None:
        return

    classes = _extract_classes(elem)
    if not classes:
        return

    selector = f".{classes[0]}"
    styles = css_rules.setdefault(selector, {})
    styles['left'] = f'{int(left)}px'
    styles['top'] = f'{int(top)}px'


def _extract_classes(elem: Tag) -> list[str]:
    """安全读取元素 class 列表。"""
    classes_attr = elem.get('class')
    if isinstance(classes_attr, list):
        return [str(c) for c in classes_attr if c]
    if isinstance(classes_attr, str):
        return [c for c in classes_attr.split() if c]
    return []


def _get_effective_bg_leaves(bg_leaves: list[Tag],
                             bg_hierarchy: dict[Tag, list[Tag]]) -> list[Tag]:
    """获取最终的"有效背景图层"列表
    
    有效背景图层 = 被包含到其他背景图层中的背景除外
    
    Args:
        bg_leaves: 背景图层列表
        bg_hierarchy: 包含关系图
    
    Returns:
        有效背景图层列表
    """
    contained_bgs: set[Tag] = set()

    for _bg, contained in bg_hierarchy.items():
        contained_bgs.update(contained)
    
    # 有效背景 = 所有背景 - 被包含的背景
    return [bg for bg in bg_leaves if bg not in contained_bgs]
