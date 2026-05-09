#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSS 工具函数：CSS 字符串 ↔ 字典的双向转换
"""

import re
from typing import Dict, Tuple


# 识别为"按类/ID 解析"的选择器前缀：仅 `.foo` / `#foo` 形式
_SELECTOR_RE = re.compile(r'^[.#][\w-]+$')


def _iter_top_level_blocks(css_content: str):
    """逐个产出顶层规则块：(selector_text, body_text, full_block_text, start, end)。

    支持 @media 等嵌套规则（按 `{}` 配对），不会把内层选择器误判为顶层。
    对于 @media { ... }，body_text 是其内部原样内容。
    """
    i = 0
    n = len(css_content)
    while i < n:
        # 跳过前置空白
        while i < n and css_content[i] in ' \t\r\n':
            i += 1
        if i >= n:
            return
        # 定位到下一个 `{`
        brace = css_content.find('{', i)
        if brace < 0:
            return
        selector = css_content[i:brace].strip()
        # 配对 `{}` 找到本块结束
        depth = 1
        j = brace + 1
        while j < n and depth > 0:
            c = css_content[j]
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
            j += 1
        end = j  # 包含结尾的 `}`
        body = css_content[brace + 1:end - 1]
        full = css_content[i:end]
        yield selector, body, full, i, end
        i = end


def _strip_leading_comments(selector: str) -> str:
    """剥掉选择器前置的 ``/* ... */`` 注释段（含中间空白），只保留真选择器文本。

    背景：``_iter_top_level_blocks`` 是按 ``{`` 切分的，它会把"块前注释"
    粘在选择器前面，比如 ``"/* 图层样式 */\n.bg__1"``。下游的 ``_SELECTOR_RE``
    只匹配 ``^[.#][\\w-]+$``，所以这种"带注释的选择器"会被误判为"非 class/id
    顶层块"，被 ``extract_global_css_header`` 整段塞进全局头。
    """
    s = selector.lstrip()
    while s.startswith('/*'):
        end = s.find('*/')
        if end < 0:
            return s  # 异常：未闭合注释，原样返回
        s = s[end + 2:].lstrip()
    return s


def parse_css_to_dict(css_content: str) -> Dict[str, Dict[str, str]]:
    """
    将 CSS 字符串解析为字典格式，**仅提取形如 `.xxx` / `#xxx` 的顶层规则**，
    并忽略 `@media` 等嵌套块内部的规则（这些应通过 `extract_global_css_header`
    原样保留，避免在 `dict_to_css` 往返时丢失全局样式或把内层规则误作顶层。

    Args:
        css_content: CSS 文本

    Returns:
        {'.classname': {'property': 'value'}}
    """
    css_rules: Dict[str, Dict[str, str]] = {}

    for selector, body, _full, _s, _e in _iter_top_level_blocks(css_content):
        sel = _strip_leading_comments(selector)
        if not _SELECTOR_RE.match(sel):
            # @media / @keyframes / *, body 等非单个 class/id 选择器：跳过
            continue
        props: Dict[str, str] = {}
        for line in body.split(';'):
            line = line.strip()
            if ':' in line:
                prop, value = line.split(':', 1)
                props[prop.strip()] = value.strip()
        if props:
            css_rules[sel] = props

    return css_rules


def extract_global_css_header(css_content: str) -> str:
    """
    从 CSS 文本中提取"全局 header"：非 `.xxx` / `#xxx` 顶层选择器的所有块，
    例如 `* { ... }` / `body { ... }` / `@media ... { ... }`，以及
    `/* 注释 */`。按原样返回，供优化后的 CSS 复用，避免 parse→dict→rebuild
    的往返过程丢失这些关键样式。

    注意：
    - `#canvas` 在我们的模板里既出现在顶层也出现在 `@media` 中。`@media` 块
      **原样保留**，顶层 `#canvas` 则作为一条普通 id 规则交给 parse 走字典路径。
    """
    parts: list[str] = []
    # 保留开头到第一个规则前的注释/空白，以及规则之间未被识别的注释段
    last_end = 0
    for selector, _body, full, start, end in _iter_top_level_blocks(css_content):
        sel_clean = _strip_leading_comments(selector)
        is_class_or_id = bool(_SELECTOR_RE.match(sel_clean))
        # 把上一个块到当前块之间的内容（通常是注释、空行）纳入；
        # 但若当前是 class/id 块（而 selector 含前置注释），那段注释属于"图层样式"
        # 分隔注释，不应进 header（否则会污染全局头）。
        gap = css_content[last_end:start]
        if gap.strip() and not is_class_or_id:
            parts.append(gap)
        if not is_class_or_id:
            # 非 class/id 块原样保留；selector 里若粘了前置注释，也一并保留
            parts.append(full)
            parts.append('\n')
        last_end = end
    # 尾部注释/空白
    tail = css_content[last_end:]
    if tail.strip():
        parts.append(tail)
    return ''.join(parts).strip() + '\n'


def _format_number(num_str: str) -> str:
    """把 CSS 数字字面量做精度规范化（消除浮点拖尾噪声）。

    背景：text_extractor / text_renderer 的 ``font_size = height * 0.85`` 这类
    Python 浮点运算会产出 ``22.099999999999998px`` 这种"拖尾噪声"。CSS 没必要
    保留 15 位小数 —— 浏览器子像素都到不了千分位。

    规则：
        - 解析为 float
        - 整数值（如 22.0）→ 整数字符串 ``"22"``
        - 否则保留 ≤ 2 位小数；末尾 0 去掉（``22.10`` → ``22.1``）
    """
    try:
        v = float(num_str)
    except ValueError:
        return num_str
    # 整数走整数输出
    if v == int(v):
        return str(int(v))
    # 保留 2 位小数，去末尾 0 与孤立小数点
    s = f"{v:.2f}".rstrip('0').rstrip('.')
    return s


# 匹配 CSS 值里"独立的数字字面量"：前面不能紧跟字母/数字/下划线/连字符/点（避免命中
# 标识符内部的数字，如 ``bg-f07984.png`` 里的 ``07984``、``var(--color-1)`` 里的 ``1``）。
# 后面可选地紧跟 CSS 单位（px/em/rem/%/vh/vw/vmin/vmax/deg/rad/turn/s/ms/ch/ex/pt/pc/cm/mm/in/fr）。
_UNIT_RE = r'(?:px|em|rem|%|vh|vw|vmin|vmax|deg|rad|turn|grad|s|ms|ch|ex|pt|pc|cm|mm|in|fr)?'
_NUMBER_RE = re.compile(
    r'(?<![A-Za-z0-9_\-.])(-?\d+\.\d+|-?\d+)(' + _UNIT_RE + r')(?![A-Za-z0-9_])'
)

# 匹配 ``url(...)``（含可选引号），整体跳过不做规范化，避免改写文件名里的数字
_URL_RE = re.compile(r'url\(\s*(?:"[^"]*"|\'[^\']*\'|[^)]*)\s*\)')


def _normalize_css_value(value: str) -> str:
    """对 CSS 属性值里所有"独立数字字面量"执行 _format_number。

    设计要点（避免历史踩坑）：
    1. ``url(...)`` 整体跳过 —— 文件名里 ``bg-f07984.png`` 不能被改写成 ``bg-f7984.png``
    2. 数字必须前不接 ``[A-Za-z0-9_-.]`` —— 避免误吃标识符内部数字
    3. 数字后可选跟标准 CSS 单位；单位前后都做边界检查

    例：
        ``"22.099999999999998px"`` → ``"22.1px"``
        ``"rgba(19, 12, 41, 1.0)"`` → ``"rgba(19, 12, 41, 1)"``
        ``"1.0"`` → ``"1"``
        ``'url("images/bg-f07984.png")'`` → 原样保留
        ``"var(--color-1)"`` → 原样保留
    """
    if not isinstance(value, str):
        return str(value)

    # 1. 先把 url(...) 抠出来用占位符替代，避免 _NUMBER_RE 误伤
    placeholders = []

    def _stash_url(m):
        placeholders.append(m.group(0))
        return f'\x00URL{len(placeholders) - 1}\x00'

    masked = _URL_RE.sub(_stash_url, value)

    # 2. 在剩余文本上做数字规范化
    def _norm(m):
        return _format_number(m.group(1)) + m.group(2)

    masked = _NUMBER_RE.sub(_norm, masked)

    # 3. 还原 url(...)
    for idx, raw in enumerate(placeholders):
        masked = masked.replace(f'\x00URL{idx}\x00', raw)
    return masked


def _emit_property_lines(properties: Dict[str, str]) -> list:
    """把一个 properties dict 产出 CSS 属性行（含数值规范化）。"""
    lines = []
    for prop, value in sorted(properties.items()):
        lines.append(f"  {prop}: {_normalize_css_value(value)};")
    return lines


def dict_to_css(
    css_rules: Dict[str, Dict[str, str]],
    header: str = '',
    merge_groups: list[list[str]] | None = None,
) -> str:
    """
    将 CSS 字典转换为 CSS 字符串。

    Args:
        css_rules:    CSS 规则字典
        header:       可选的"全局 header"文本，按原样作为输出前缀
                      （由 `extract_global_css_header` 产出）
        merge_groups: 可选的"合并组"列表，每组是一组属性等价的选择器；
                      渲染时这些选择器会被写成 `.a, .b, .c { ... }` 形式，
                      共享同一个属性块。组内选择器之外的规则按原方式逐条
                      输出。组之间不重叠（同一选择器只能属于一个组）。

    Returns:
        格式化后的 CSS 字符串
    """
    css_lines = []
    if header:
        css_lines.append(header.rstrip())
        css_lines.append("")
        css_lines.append("/* ========== 图层样式 ========== */")
        css_lines.append("")

    # 把"属于合并组"的选择器从普通输出流程里挪走，统一记到 grouped_for；
    # 单条输出时只渲染剩余 selector。
    grouped_for: Dict[str, int] = {}
    if merge_groups:
        for idx, group in enumerate(merge_groups):
            for sel in group:
                grouped_for[sel] = idx

    # 第一段：单条规则（未参与合并）
    for selector, properties in sorted(css_rules.items()):
        if not properties:
            continue
        if selector in grouped_for:
            continue
        css_lines.append(f"{selector} {{")
        css_lines.extend(_emit_property_lines(properties))
        css_lines.append("}")
        css_lines.append("")

    # 第二段：合并组规则（一条逗号分隔的选择器 + 共享属性块）
    if merge_groups:
        # 按组内首个选择器排序，输出稳定
        sorted_groups = sorted(
            (g for g in merge_groups if g),
            key=lambda g: sorted(g)[0],
        )
        for group in sorted_groups:
            # 只取真正存在于 css_rules 的选择器
            members = [s for s in group if s in css_rules and css_rules[s]]
            if len(members) < 2:
                # 组内不足两条则按单条规则补回（防御性：理论不该发生）
                for s in members:
                    properties = css_rules[s]
                    css_lines.append(f"{s} {{")
                    css_lines.extend(_emit_property_lines(properties))
                    css_lines.append("}")
                    css_lines.append("")
                continue
            members_sorted = sorted(members)
            # 第一个选择器代表本组的属性（CssDedup 已保证组内属性等价）
            properties = css_rules[members_sorted[0]]
            # 多选择器换行排版，便于阅读
            css_lines.append(",\n".join(members_sorted) + " {")
            css_lines.extend(_emit_property_lines(properties))
            css_lines.append("}")
            css_lines.append("")

    return '\n'.join(css_lines)
