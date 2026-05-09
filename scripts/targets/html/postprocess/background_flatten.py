"""
Background Flatten — 多层 url() 背景图合成的**文本兜底**

定位（重要：2026-04-30 重构后）
==================================
**主路径已下沉到 LayoutOptimizer**：
``layout_optimizer/transformers/dom_restructure.py::DOMRestructure._try_inline_compose_backgrounds``
在决定写出多 url 背景之前直接合成。CSS 一开始就是单 url，CssDedup /
CssPretty 都不会再看到多层背景。

本模块作为**安全网**，只处理"绕过 DOMRestructure 主路径"的边角场景：
- 旧版本产出的 CSS 文本被外部工具二次注入了多 url 背景
- 自定义 LayoutOptimizer pipeline 跳过 DOMRestructure 直接拼装 CSS
- DOMRestructure 主路径合成失败时（PIL 缺失 / 物理 PNG 缺失等）回落

正常 pipeline 跑下来，``stats['rules_flattened']`` 通常应为 0；非 0 表示
有"主路径漏掉的"多 url 规则被本模块兜底处理。

合成原则（与主路径一致）
========================
1. 同一规则同时含 ``background-image`` + ``background-position`` +
   ``background-size``，且三者都是逗号分隔的多值列表（长度一致）。
2. 每一层 ``background-image`` 形如 ``url("images/xxx.png")``（仅本地 PNG）。
3. ``background-position`` 形如 ``Npx Mpx`` 或 ``left top``。
4. ``background-size`` 形如 ``Wpx Hpx``。
5. ``background-repeat`` 全部 ``no-repeat``（缺省视为 no-repeat）。
6. 物理图片均存在且是合法 PNG。

实际合成委托给 ``background_compose.compose_layers``，与主路径共用一份代码。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .background_compose import (
    ComposeLayer,
    compose_layers,
    estimate_bytes_saved,
    parse_url_to_local_png,
)

# --- 正则 -------------------------------------------------------------------

# 一条 CSS 规则块（粗略：选择器 + { ... }），用于按规则迭代。
# 分组：
#   1 = selector（含尾随空白）
#   2 = 开 { 之后到首个非空白前的空白（保留缩进风格）
#   3 = body（去掉前后空白，避免 _PROP_RE 漏匹配第一行）
#   4 = body 末尾到 } 前的空白
_RULE_RE = re.compile(
    r'([^{}/]+?)\{(\s*)([^{}]*?)(\s*)\}',
    re.DOTALL,
)

# 一行 CSS 属性
_PROP_RE = re.compile(r'^\s*([a-zA-Z\-]+)\s*:\s*(.+?)\s*;?\s*$')

# url("xxx") / url('xxx') / url(xxx)
_URL_RE = re.compile(r"""url\(\s*(?:"([^"]+)"|'([^']+)'|([^)]+?))\s*\)""")

# 位置 token：数字 + px 或 'left top'
_POS_PX_RE = re.compile(r'^(-?\d+(?:\.\d+)?)px\s+(-?\d+(?:\.\d+)?)px$')

# size token：宽 高 px
_SIZE_PX_RE = re.compile(r'^(\d+(?:\.\d+)?)px\s+(\d+(?:\.\d+)?)px$')


# --- 解析辅助 ---------------------------------------------------------------

def _split_top_level_commas(text: str) -> List[str]:
    """按逗号切分 CSS 多值，但忽略 url(...) 内的逗号"""
    out: List[str] = []
    depth = 0
    buf: List[str] = []
    for ch in text:
        if ch == '(':
            depth += 1
            buf.append(ch)
        elif ch == ')':
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == ',' and depth == 0:
            out.append(''.join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append(''.join(buf).strip())
    return out


def _parse_pos(token: str) -> Optional[Tuple[int, int]]:
    s = token.strip()
    if s in ('left top', '0 0', '0px 0px'):
        return (0, 0)
    m = _POS_PX_RE.match(s)
    if m:
        return (int(round(float(m.group(1)))), int(round(float(m.group(2)))))
    return None


def _parse_size(token: str) -> Optional[Tuple[int, int]]:
    s = token.strip()
    if s in ('100% 100%', 'auto'):
        return None  # 不在本期处理范围
    m = _SIZE_PX_RE.match(s)
    if m:
        return (int(round(float(m.group(1)))), int(round(float(m.group(2)))))
    return None


# --- 单条规则处理 ----------------------------------------------------------

def _try_flatten_rule(
    body: str,
    images_dir: Path,
) -> Tuple[Optional[str], Dict[str, int]]:
    """尝试合成一条规则的多层背景。

    Returns:
        (new_body, info)
        new_body=None 表示不修改；info 里 'flattened'=1 表示完成合成。
    """
    info = {'flattened': 0, 'layers_in': 0}

    # 收集相关属性的字符串值（保留行号以便回写时仅替换值部分）
    props: Dict[str, Tuple[int, str]] = {}  # name -> (line_idx, raw_value)
    lines = body.split('\n')
    for i, line in enumerate(lines):
        m = _PROP_RE.match(line)
        if not m:
            continue
        name = m.group(1).strip().lower()
        if name in (
            'background-image',
            'background-position',
            'background-size',
            'background-repeat',
        ):
            props[name] = (i, m.group(2).strip())

    if 'background-image' not in props:
        return None, info

    img_tokens = _split_top_level_commas(props['background-image'][1])
    if len(img_tokens) < 2:
        return None, info  # 单层无需合成

    info['layers_in'] = len(img_tokens)

    pos_str = props.get('background-position', (None, ''))[1]
    size_str = props.get('background-size', (None, ''))[1]
    repeat_str = props.get('background-repeat', (None, ''))[1]

    pos_tokens = _split_top_level_commas(pos_str) if pos_str else []
    size_tokens = _split_top_level_commas(size_str) if size_str else []
    repeat_tokens = _split_top_level_commas(repeat_str) if repeat_str else []

    n = len(img_tokens)
    if len(pos_tokens) != n or len(size_tokens) != n:
        return None, info
    if repeat_tokens and len(repeat_tokens) != n:
        return None, info

    # CSS 多 url：第一个 = 视觉最上层；compose_layers 期望底层在前 → 倒序
    layers: List[ComposeLayer] = []
    src_paths: List[Path] = []
    html_dir = images_dir.parent
    for k in range(n - 1, -1, -1):
        png_path = parse_url_to_local_png(img_tokens[k], html_dir)
        if png_path is None:
            return None, info
        # 仅处理 images/ 下的本地 PNG
        if 'images/' not in png_path.as_posix():
            return None, info
        pos = _parse_pos(pos_tokens[k])
        if pos is None:
            return None, info
        size = _parse_size(size_tokens[k])
        if size is None:
            return None, info
        rep = repeat_tokens[k].strip().lower() if repeat_tokens else 'no-repeat'
        if rep != 'no-repeat':
            return None, info
        layers.append(ComposeLayer(
            png_path=png_path,
            pos_x=pos[0], pos_y=pos[1],
            size_w=size[0], size_h=size[1],
        ))
        src_paths.append(png_path)

    result = compose_layers(layers, images_dir)
    if result is None:
        return None, info

    new_url = f'url("{result.rel_url}")'
    new_pos = (
        'left top'
        if (result.origin_x == 0 and result.origin_y == 0)
        else f'{result.origin_x}px {result.origin_y}px'
    )
    new_size = f'{result.canvas_w}px {result.canvas_h}px'

    def _replace_value(line_idx: int, name: str, new_value: str):
        old = lines[line_idx]
        m = re.match(r'^(\s*)' + re.escape(name) + r'\s*:\s*(.+?)(;?)\s*$', old)
        if not m:
            return
        indent, _, semi = m.group(1), m.group(2), m.group(3) or ';'
        lines[line_idx] = f'{indent}{name}: {new_value}{semi}'

    _replace_value(props['background-image'][0], 'background-image', new_url)
    if 'background-position' in props:
        _replace_value(props['background-position'][0], 'background-position', new_pos)
    if 'background-size' in props:
        _replace_value(props['background-size'][0], 'background-size', new_size)
    if 'background-repeat' in props:
        _replace_value(props['background-repeat'][0], 'background-repeat', 'no-repeat')

    info['flattened'] = 1
    info['_bytes_saved'] = estimate_bytes_saved(src_paths, result)
    return '\n'.join(lines), info


# --- 公开入口 --------------------------------------------------------------

def flatten_multi_url_backgrounds(
    css_text: str,
    images_dir: Path,
) -> Tuple[str, Dict[str, int]]:
    """扫描 CSS 文本，把可合成的多层 background-image 合并为单图。

    主路径正常工作时本函数应几乎无事可做（``rules_flattened`` 通常为 0）。

    Args:
        css_text: 完整 CSS 字符串
        images_dir: 物理 ``images/`` 目录

    Returns:
        (new_css_text, stats)
        stats 字段：
          - rules_scanned    扫描的规则总数
          - rules_flattened  成功合成的规则数（兜底命中数）
          - layers_collapsed 折叠掉的层数（n_in - 1 求和）
          - bytes_saved      源文件总和 - 合成图字节
    """
    images_dir = Path(images_dir)
    stats = {
        'rules_scanned': 0,
        'rules_flattened': 0,
        'layers_collapsed': 0,
        'bytes_saved': 0,
    }
    if not images_dir.is_dir():
        return css_text, stats

    out_chunks: List[str] = []
    cursor = 0
    for m in _RULE_RE.finditer(css_text):
        out_chunks.append(css_text[cursor:m.start()])
        selector = m.group(1)
        body_pre = m.group(2)
        body = m.group(3)
        body_post = m.group(4)
        stats['rules_scanned'] += 1

        if 'background-image' in body and ',' in body:
            new_body, info = _try_flatten_rule(body, images_dir)
            if new_body is not None and info['flattened']:
                stats['rules_flattened'] += 1
                stats['layers_collapsed'] += info['layers_in'] - 1
                stats['bytes_saved'] += int(info.get('_bytes_saved', 0))
                out_chunks.append(
                    selector + '{' + body_pre + new_body + body_post + '}'
                )
                cursor = m.end()
                continue

        out_chunks.append(css_text[m.start():m.end()])
        cursor = m.end()

    out_chunks.append(css_text[cursor:])
    return ''.join(out_chunks), stats
