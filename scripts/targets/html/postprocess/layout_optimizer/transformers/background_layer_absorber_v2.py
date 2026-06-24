"""背景层吸收优化器 V2 - 安全版本

设计目标：
- 正确处理多容器场景（多个 layer-group 可能有相同 class 名）
- 精确指代每个容器内的直接子元素（避免全局污染）
- 使用 BeautifulSoup 而非正则，确保 HTML 解析正确

关键改进：
1. 容器身份管理：data-absorb-id（仅在处理过程中使用，最后清理）
2. 精确子选择器：容器选择器与第一个 class 组合，形如 [data-absorb-id="X"] > .class
3. CSS 规则合并：吸收完成后，把 [data-absorb-id] 选择器的规则合并回原始 class 选择器

流程：
1. 标记所有容器，分配 data-absorb-id
2. 对每个容器逐个处理背景层吸收
3. 把所有 [data-absorb-id="X"] 的 CSS 规则合并到容器原始选择器
4. 清理 HTML 中的 data-absorb-id 属性
"""

from __future__ import annotations

from typing import Any
from bs4 import BeautifulSoup, Tag
import re


def absorb_background_layers_safe(
    html_content: str, css_rules: dict[str, dict[str, str]]
) -> tuple[str, dict[str, dict[str, str]], dict[str, Any]]:
    """安全的背景层吸收优化。
    
    Args:
        html_content: HTML 文本
        css_rules: CSS 规则字典 {selector: {prop: value}}
    
    Returns:
        (优化后的 HTML, 更新后的 CSS 规则, 统计信息)
    """
    soup = BeautifulSoup(html_content, "html.parser")
    stats = {
        "bg_layers_absorbed": 0,
        "bg_elements_removed": 0,
        "containers_processed": 0,
        "errors": [],
    }
    
    # 步骤 1：标记所有容器
    container_info_map = _mark_containers(soup)
    
    if not container_info_map:
        return html_content, css_rules, stats
    
    # 步骤 2：处理每个容器中的背景层
    for container_id, container_info in container_info_map.items():
        _absorb_in_container_v2(
            container_info, container_id, css_rules, soup, stats
        )
    
    # 步骤 3：合并 CSS 规则（把 [data-absorb-id] 选择器合并回原始 class 选择器）
    _merge_absorb_css_rules(container_info_map, css_rules)
    
    # 步骤 4：清理 data-absorb-id 属性
    for elem in soup.find_all(attrs={"data-absorb-id": True}):
        del elem.attrs["data-absorb-id"]
    
    # 步骤 5：清理未使用的 CSS 规则
    _cleanup_unused_css_rules(soup, css_rules, stats)
    
    return str(soup), css_rules, stats


def _mark_containers(soup: BeautifulSoup) -> dict[str, dict[str, Any]]:
    """给所有容器标记 data-absorb-id。
    
    Returns:
        {container_id: {"element": Tag, "semantic_class": str, ...}}
    """
    result: dict[str, dict[str, Any]] = {}
    seq = 0
    
    # 标记 #canvas
    canvas = soup.find("div", id="canvas")
    if canvas:
        container_id = f"c{seq}"
        seq += 1
        canvas.attrs["data-absorb-id"] = container_id
        result[container_id] = {
            "element": canvas,
            "semantic_class": None,  # #canvas 没有 class
            "id": "canvas",
        }
    
    # 标记所有 layer-group
    for layer_group in soup.find_all("div", class_="layer-group"):
        container_id = f"c{seq}"
        seq += 1
        layer_group.attrs["data-absorb-id"] = container_id
        
        classes = layer_group.get("class", [])
        semantic_class = next(
            (c for c in classes if c != "layer-group"),
            None
        )
        
        result[container_id] = {
            "element": layer_group,
            "semantic_class": semantic_class,
            "id": None,
        }
    
    return result


def _absorb_in_container_v2(
    container_info: dict[str, Any],
    container_id: str,
    css_rules: dict[str, dict[str, str]],
    soup: BeautifulSoup,
    stats: dict[str, Any],
) -> None:
    """在单个容器内吸收背景层。"""
    container_elem = container_info["element"]
    semantic_class = container_info["semantic_class"]
    
    # 获取容器的直接子 div
    direct_children: list[Tag] = [
        child for child in container_elem.find_all(recursive=False)
        if isinstance(child, Tag) and child.name == "div"
    ]
    
    if not direct_children:
        return
    
    # 检查容器本身是否有背景，如果有则不吸收
    container_css = _get_container_css(semantic_class, container_elem, css_rules)
    if _has_background(container_css):
        return
    
    stats["containers_processed"] += 1
    
    # 收集 z-index 信息
    z_info: dict[Tag, int] = {}
    for child in direct_children:
        classes = child.get("class", [])
        if not classes:
            continue
        z_val = _get_z_index_from_css(classes, css_rules)
        z_info[child] = z_val
    
    if not z_info:
        return
    
    min_z = min(z_info.values())
    max_z = max(z_info.values())
    
    # 特殊情况：如果只有一个子，且它是背景层（有背景），则直接吸收
    if len(direct_children) == 1 and min_z == max_z:
        child = direct_children[0]
        classes = child.get("class", [])
        if classes:
            first_class = classes[0]
            child_css = _get_css_for_classes(classes, css_rules)
            
            # 检查是否满足吸收条件
            if (child_css.get("position") == "absolute" and
                _has_background(child_css) and
                not _has_child_elements(child)):
                _perform_absorption(
                    child, first_class, container_id, semantic_class,
                    child_css, css_rules, soup, stats
                )
        return
    
    if min_z == max_z:
        return  # 多个子但 z-index 都相同，无法判断背景层
    
    # 识别并吸收背景层
    # 计算容器大小用于覆盖率检查
    container_styles = _get_container_css(semantic_class, container_elem, css_rules)
    container_width = _parse_dimension(container_styles.get("width", "100%"))
    container_height = _parse_dimension(container_styles.get("height", "100%"))
    
    # 背景层吸收的最小覆盖率（需要至少覆盖 95% 以确保是背景层）
    COVERAGE_RATIO_THRESHOLD = 0.95
    
    for child in direct_children:
        classes = child.get("class", [])
        if not classes:
            continue
        
        z_val = z_info[child]
        if z_val != min_z:
            continue  # 不是最小 z-index
        
        # 检查其他条件
        first_class = classes[0]
        child_css = _get_css_for_classes(classes, css_rules)
        
        if child_css.get("position") != "absolute":
            continue
        
        if not _has_background(child_css):
            continue
        
        if _has_child_elements(child):
            continue

        # 检查背景层是否溢出容器（left < 0 或 top < 0）
        # 若背景层刻意设置负偏移，说明设计师希望背景图超出容器边界显示（出血效果）。
        # 把这种层吸收为 background-image 后，background-position 的负值会导致
        # 图片被容器的 overflow 裁切，无法还原原始视觉。因此直接跳过不吸收。
        child_left = _parse_position_value(child_css.get("left", "0"))
        child_top  = _parse_position_value(child_css.get("top", "0"))
        if child_left < 0 or child_top < 0:
            continue  # 溢出容器，不吸收
        
        # 新增：检查覆盖率（防止吸收只占容器一小部分的前景元素）
        child_width = _parse_dimension(child_css.get("width", "0"))
        child_height = _parse_dimension(child_css.get("height", "0"))
        
        if container_width > 0 and container_height > 0:
            width_ratio = child_width / container_width
            height_ratio = child_height / container_height
            # 必须两个方向都至少覆盖 COVERAGE_RATIO_THRESHOLD（95%）才认为是背景层
            if width_ratio < COVERAGE_RATIO_THRESHOLD or height_ratio < COVERAGE_RATIO_THRESHOLD:
                continue  # 覆盖率不足，不吸收
        
        # 吸收背景层时，不检查 opacity
        # 理由：背景层的 opacity 是设计的一部分，吸收时直接合并背景，不保留 opacity
        
        # 满足所有条件，执行吸收
        _perform_absorption(
            child, first_class, container_id, semantic_class,
            child_css, css_rules, soup, stats
        )


def _perform_absorption(
    bg_elem: Tag,
    bg_first_class: str,
    container_id: str,
    container_semantic_class: str | None,
    bg_css: dict[str, str],
    css_rules: dict[str, dict[str, str]],
    soup: BeautifulSoup,
    stats: dict[str, Any],
) -> None:
    """执行单个背景层的吸收。"""
    # 确保容器的 CSS 规则存在
    container_css_selector = f'[data-absorb-id="{container_id}"]'
    if container_css_selector not in css_rules:
        css_rules[container_css_selector] = {}
    
    # 吸收背景属性到容器 CSS
    bg_properties = [
        "background",
        "background-image",
        "background-position",
        "background-size",
        "background-repeat",
        "background-attachment",
    ]
    
    for prop in bg_properties:
        if prop in bg_css:
            css_rules[container_css_selector][prop] = bg_css[prop]
    
    # 处理定位属性：将 top/left/right/bottom 转换为 background-position 的偏移
    bg_pos_x = 0
    bg_pos_y = 0
    
    # 提取定位属性值（支持各种单位）
    for key in ["left", "right"]:
        if key in bg_css:
            val_str = bg_css[key]
            val_num = _parse_position_value(val_str)
            if key == "left":
                bg_pos_x = val_num
            else:  # right
                bg_pos_x = -val_num
    
    for key in ["top", "bottom"]:
        if key in bg_css:
            val_str = bg_css[key]
            val_num = _parse_position_value(val_str)
            if key == "top":
                bg_pos_y = val_num
            else:  # bottom
                bg_pos_y = -val_num
    
    # 如果有定位偏移，则累加到 background-position
    if bg_pos_x != 0 or bg_pos_y != 0:
        container_rule = css_rules[container_css_selector]

        # 解析现有的 background-position：
        #   优先从单独的 background-position 子属性读取；
        #   若不存在，再尝试从 background shorthand 中提取（仅取 position 部分）。
        existing_pos = container_rule.get("background-position", "")
        if not existing_pos and "background" in container_rule:
            existing_pos = _extract_position_from_shorthand(
                container_rule["background"]
            )
        existing_x, existing_y = _parse_background_position(existing_pos or "0 0")

        # 累加新的偏移
        new_x = existing_x + bg_pos_x
        new_y = existing_y + bg_pos_y
        pos_str = f"{new_x}px {new_y}px"

        # ⚠️ 关键：若容器已有 background shorthand，必须把 position 嵌入
        # shorthand 本身（W3C 语法：<image> <position> <repeat>），而不能
        # 单独写一行 background-position。
        # 原因：CSS 规范规定 background shorthand 会重置所有子属性（包括
        # background-position）。若 shorthand 写在前、background-position 写在后，
        # 顺序决定生效与否；若顺序相反则 shorthand 覆盖 background-position。
        # 两种情况都容易出错，唯一安全的做法是把 position 内嵌进 shorthand。
        if "background" in container_rule and "background-image" not in container_rule:
            # 容器只有 shorthand 形式（无单独子属性），把 position 嵌入 shorthand
            container_rule["background"] = _inject_position_into_shorthand(
                container_rule["background"], pos_str
            )
            # 删除可能残留的单独 background-position（避免冲突）
            container_rule.pop("background-position", None)
        else:
            # 容器使用分散子属性形式，直接写 background-position
            container_rule["background-position"] = pos_str
    
    # 从 HTML 中删除背景层元素
    if bg_elem.parent:
        bg_elem.decompose()
    
    stats["bg_layers_absorbed"] += 1
    stats["bg_elements_removed"] += 1


def _merge_absorb_css_rules(
    container_info_map: dict[str, dict[str, Any]],
    css_rules: dict[str, dict[str, str]],
) -> None:
    """把 [data-absorb-id] 选择器的 CSS 规则合并到原始 class 选择器。"""
    # 建立容器 ID -> 原始选择器的映射
    id_to_selector: dict[str, str] = {}
    
    for container_id, info in container_info_map.items():
        if info["semantic_class"]:
            # layer-group 情况
            id_to_selector[container_id] = f".{info['semantic_class']}"
        elif info["id"]:
            # #canvas 情况
            id_to_selector[container_id] = f"#{info['id']}"
    
    # 收集所有 absorb 选择器
    absorb_selectors: list[str] = [
        sel for sel in css_rules.keys()
        if "[data-absorb-id=" in sel
    ]
    
    # 合并每个 absorb 选择器到对应的原始选择器
    for abs_sel in absorb_selectors:
        # 提取容器 ID
        m = re.search(r'data-absorb-id="([^"]+)"', abs_sel)
        if not m:
            continue
        
        container_id = m.group(1)
        original_selector = id_to_selector.get(container_id)
        
        if not original_selector:
            continue
        
        # 确保原始选择器存在
        if original_selector not in css_rules:
            css_rules[original_selector] = {}
        
        # 合并属性
        css_rules[original_selector].update(css_rules[abs_sel])
        
        # 删除 absorb 选择器
        del css_rules[abs_sel]


def _get_z_index_from_css(
    classes: list[str],
    css_rules: dict[str, dict[str, str]],
) -> int:
    """从 class 列表获取 z-index 值。"""
    for cls in classes:
        selector = f".{cls}"
        if selector in css_rules:
            z_val = css_rules[selector].get("z-index", "0")
            return _parse_z_index(z_val)
    return 0


def _get_css_for_classes(
    classes: list[str],
    css_rules: dict[str, dict[str, str]],
) -> dict[str, str]:
    """通过 class 列表获取 CSS 规则。"""
    for cls in classes:
        selector = f".{cls}"
        if selector in css_rules:
            return css_rules[selector]
    return {}


def _get_container_css(
    semantic_class: str | None,
    container_elem: Tag,
    css_rules: dict[str, dict[str, str]],
) -> dict[str, str]:
    """获取容器自身的 CSS 规则。"""
    if semantic_class:
        selector = f".{semantic_class}"
        if selector in css_rules:
            return css_rules[selector]
    elif container_elem.get("id"):
        selector = f"#{container_elem.get('id')}"
        if selector in css_rules:
            return css_rules[selector]
    return {}


def _has_background(css: dict[str, str]) -> bool:
    """检查 CSS 中是否有背景图。"""
    if "background-image" in css:
        return True
    if "background" in css and "url(" in css["background"]:
        return True
    return False


def _is_fully_opaque(opacity_str: str) -> bool:
    """检查 opacity 是否为 1（完全不透明）。
    
    支持格式：
    - "1" 或 "1.0" -> True
    - "0.9" 或任何 < 1 的值 -> False
    - 空字符串或 "auto" -> True（默认为完全不透明）
    """
    if not opacity_str or opacity_str == "auto":
        return True
    
    opacity_str = opacity_str.strip()
    try:
        opacity_val = float(opacity_str)
        return opacity_val == 1.0
    except (ValueError, TypeError):
        return True


def _has_child_elements(elem: Tag) -> bool:
    """检查元素是否有子 Tag 元素。"""
    for child in elem.children:
        if isinstance(child, Tag):
            return True
    return False


def _parse_z_index(z_str: str) -> int:
    """解析 z-index 值。"""
    if not z_str or z_str == "auto":
        return 0
    try:
        return int(z_str)
    except (ValueError, TypeError):
        return 0


def _parse_dimension(dim_str: str) -> float:
    """解析 CSS 宽度/高度值为数值（像素）。
    
    支持格式：
    - "100px" -> 100.0
    - "100" -> 100.0
    - "100%" -> 0.0（百分比无法转换）
    - "auto" 或空字符串 -> 0.0
    """
    if not dim_str or dim_str == "auto" or dim_str == "100%":
        return 0.0
    
    dim_str = dim_str.strip()
    
    # 去掉单位 px
    if dim_str.endswith("px"):
        dim_str = dim_str[:-2]
    
    try:
        return float(dim_str)
    except (ValueError, TypeError):
        return 0.0


def _parse_position_value(val_str: str) -> int:
    """解析定位属性值（如 '10px', '5', '-4px'），返回数字部分。"""
    if not val_str or val_str == "auto":
        return 0
    # 移除 'px' 等单位
    val_str = val_str.strip()
    match = re.match(r'^(-?\d+(?:\.\d+)?)', val_str)
    if match:
        try:
            return int(float(match.group(1)))
        except (ValueError, TypeError):
            return 0
    return 0


def _parse_background_position(pos_str: str) -> tuple[int, int]:
    """解析 background-position 字符串，返回 (x, y) 像素值。
    
    支持格式：
    - "0 0"
    - "10px 20px"
    - "0"（单个值）
    - "center top" 等关键字（未完全支持，返回 0）
    """
    if not pos_str:
        return (0, 0)
    
    parts = pos_str.strip().split()
    
    if len(parts) == 0:
        return (0, 0)
    if len(parts) == 1:
        x = _parse_position_value(parts[0])
        return (x, 0)
    
    # len >= 2
    x = _parse_position_value(parts[0])
    y = _parse_position_value(parts[1])
    return (x, y)


def _extract_position_from_shorthand(shorthand: str) -> str:
    """从 background shorthand 中提取 position 部分（如果有）。

    background shorthand 格式（W3C 简化版，psd2code 实际产物）：
      ``url(...) <position> <repeat>``  或  ``url(...) <repeat>``

    本函数尝试识别 position token（数字 + px / 关键字 center/left/right/top/bottom）。
    若无法解析，返回空字符串（由调用方 fallback 到 "0 0"）。
    """
    if not shorthand:
        return ""
    # 移除 url(...) 部分
    cleaned = re.sub(r'url\([^)]*\)', '', shorthand).strip()
    # 已知的 repeat/attachment/clip/origin 关键字
    _repeat_kw = {'no-repeat', 'repeat', 'repeat-x', 'repeat-y', 'space', 'round',
                  'scroll', 'fixed', 'local', 'border-box', 'padding-box', 'content-box'}
    _pos_kw = {'left', 'right', 'top', 'bottom', 'center'}
    parts = cleaned.split()
    pos_tokens = []
    for token in parts:
        t = token.lower()
        if t in _repeat_kw:
            continue  # 跳过非 position token
        # 像素值或百分比
        if re.match(r'^-?\d+(?:\.\d+)?(?:px|%|em|rem)?$', t):
            pos_tokens.append(token)
        elif t in _pos_kw:
            pos_tokens.append(token)
    if len(pos_tokens) >= 2:
        return f"{pos_tokens[0]} {pos_tokens[1]}"
    if len(pos_tokens) == 1:
        return pos_tokens[0]
    return ""


def _inject_position_into_shorthand(shorthand: str, pos_str: str) -> str:
    """将 position 值注入 background shorthand，替换原有 position 部分（如有）。

    输入：
      shorthand = 'url("foo.png") no-repeat'
      pos_str   = '-10px -10px'
    输出：
      'url("foo.png") -10px -10px no-repeat'

    策略：先剥离 url(...) 和已知 repeat 关键字，拼接新 position，再重组。
    """
    if not shorthand:
        return shorthand
    # 提取 url 部分
    url_match = re.search(r'url\([^)]*\)', shorthand)
    url_part = url_match.group(0) if url_match else ""
    # 移除 url(...) 后剩余 token
    rest = re.sub(r'url\([^)]*\)', '', shorthand).strip()
    _repeat_kw = {'no-repeat', 'repeat', 'repeat-x', 'repeat-y', 'space', 'round',
                  'scroll', 'fixed', 'local', 'border-box', 'padding-box', 'content-box'}
    _pos_kw = {'left', 'right', 'top', 'bottom', 'center'}
    keep_tokens = []   # repeat / attachment 等非 position token
    for token in rest.split():
        t = token.lower()
        if t in _repeat_kw:
            keep_tokens.append(token)
        elif re.match(r'^-?\d+(?:\.\d+)?(?:px|%|em|rem)?$', t):
            pass  # 旧的 position 值，丢弃（用 pos_str 替换）
        elif t in _pos_kw:
            pass  # 旧的 position 关键字，丢弃
    # 重组：url position repeat/attachment...
    parts = [p for p in [url_part, pos_str] + keep_tokens if p]
    return ' '.join(parts)


def _cleanup_unused_css_rules(
    soup: BeautifulSoup,
    css_rules: dict[str, dict[str, str]],
    stats: dict[str, Any],
) -> None:
    """清理 HTML 中未被使用的 CSS 规则。
    
    遍历所有 CSS 规则，检查对应的选择器是否在 HTML 中被使用。
    如果未被使用，则删除该 CSS 规则。
    """
    # 收集所有在 HTML 中被使用的 class
    used_classes = set()
    
    for elem in soup.find_all(class_=True):
        classes = elem.get("class", [])
        for cls in classes:
            if cls and cls != "layer" and cls != "layer-group":
                used_classes.add(cls)
    
    # 删除未被使用的 CSS 规则
    selectors_to_delete = []
    for selector in css_rules.keys():
        # 提取 selector 中的 class 名（假设是 .classname 格式）
        if selector.startswith("."):
            class_name = selector[1:].split(" ")[0].split("[")[0]  # 处理组合选择器
            if class_name and class_name not in used_classes:
                selectors_to_delete.append(selector)
    
    # 删除规则并计数
    for selector in selectors_to_delete:
        del css_rules[selector]
        stats.setdefault("css_rules_removed", 0)
        stats["css_rules_removed"] += 1
