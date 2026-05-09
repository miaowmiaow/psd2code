"""三向映射报告（class ↔ image ↔ PSD layer）+ 图片索引（按版块分组）。

为什么需要
==========
开发者拿到 psd2code 产物后最常见的两个动作：

1. **替换占位图**：设计师改了"按钮 3"的素材 → 开发要找"按钮 3 对应哪个 png"
   → 现状：只能在 ``style_optimized.css`` 里 grep 类名再找到 ``url(images/...)``，
   或在 PS 里查图层 → 用图层名猜文件名。两种方式都要跨文件。
2. **理解类名**：开发看到 ``.prop-bg-2`` 想知道"这是 PSD 哪个图层"——目前
   只能查 ``layer_map.json``（双向类名↔layer_id），但缺图片关联。

本模块产出**两份**互补的开发者文档：

* ``_mapping_report.md``：按 **class** 排序的三向映射表（class | image | PSD layer | abs 坐标）
* ``_image_index.md``：按 **版块（bankuai / section）** 分组的图片清单 + 缩略尺寸 / 用途，
  方便设计/产品对照"哪些图属于活动版块 1 / 版块 2 / 公共"。

数据来源
========
* ``layer_map.json``（``strip_dev_metadata`` 已生成）→ class ↔ layer_id ↔ name ↔ type
* ``style_optimized.css`` → class ↔ image url ↔ left/top/width/height
* ``index_optimized.html`` → 元素 DOM 结构 → 推断版块归属

接入点
======
``pipeline.py::LayoutOptimizeStage.run()`` 在 ``strip_and_collect`` /
``write_layer_map`` 之后调用 ``write_mapping_reports(html_dir)``。

约束
====
* 报告失败不阻断流水线（try/except + 错误回显）；
* 不修改 HTML / CSS / layer_map（纯只读 + 写两个新文件）。
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# 数据采集
# ---------------------------------------------------------------------------

# 解析 ``url("images/xxx.png")`` / ``url(images/xxx.png)`` / ``url('images/xxx.png')``
_URL_IN_BG_RE = re.compile(r'url\(\s*["\']?([^"\')]+?)["\']?\s*\)')

# 块级粗解析：抠出 ``.<class> { ... }``，仅用于把 class → image 关联做出来。
# 不追求 W3C 完备（合并组形如 ``.a, .b { ... }`` 也能拆 .a 和 .b 各自一份）。
_RULE_BLOCK_RE = re.compile(
    r'((?:[.#][A-Za-z0-9_-]+(?:\s*,\s*[.#][A-Za-z0-9_-]+)*)\s*)\{([^}]*)\}',
    re.DOTALL,
)


def _parse_class_to_images(css_text: str) -> Dict[str, List[str]]:
    """从 CSS 文本里抽 ``{class: [image_url, ...]}``。

    支持：
        - 单选择器：``.bg__1 { background-image: url(images/bg-f07984.png); }``
        - 合并组：``.a, .b, .c { background-image: url(...) }`` → a/b/c 都关联
        - 多个 url（如 fallback 多背景）：保留顺序
    """
    out: Dict[str, List[str]] = {}
    for m in _RULE_BLOCK_RE.finditer(css_text):
        sel_part = m.group(1).strip()
        body = m.group(2)
        urls = _URL_IN_BG_RE.findall(body)
        if not urls:
            continue
        # 取首类（去掉 # / .），合并组里每个独立处理
        for sel in sel_part.split(','):
            sel = sel.strip()
            if not sel.startswith('.'):
                continue
            cls = sel[1:]
            out.setdefault(cls, [])
            for u in urls:
                if u not in out[cls]:
                    out[cls].append(u)
    return out


def _parse_class_geometry(css_text: str) -> Dict[str, Dict[str, str]]:
    """抽 ``{class: {left, top, width, height}}``（仅有 abs 定位的元素）。

    用于 ``_mapping_report`` 里的 abs 列。
    """
    out: Dict[str, Dict[str, str]] = {}
    geom_keys = {'left', 'top', 'width', 'height'}
    for m in _RULE_BLOCK_RE.finditer(css_text):
        sel_part = m.group(1).strip()
        body = m.group(2)
        # 抠属性
        props: Dict[str, str] = {}
        for line in body.split(';'):
            if ':' not in line:
                continue
            k, _, v = line.partition(':')
            k = k.strip().lower()
            v = v.strip()
            if k in geom_keys:
                props[k] = v
        if not props:
            continue
        for sel in sel_part.split(','):
            sel = sel.strip()
            if not sel.startswith('.'):
                continue
            cls = sel[1:]
            # 只填补缺失字段（合并组多次出现取首条）
            entry = out.setdefault(cls, {})
            for k, v in props.items():
                entry.setdefault(k, v)
    return out


# ---------------------------------------------------------------------------
# 版块归属推断
# ---------------------------------------------------------------------------

# "版块容器"识别模式：class 首位包含这些前缀的容器。
_SECTION_CLASS_PREFIXES = (
    'bankuai', 'section', 'banner', 'header', 'footer',
)


def _infer_section(el) -> str:
    """从元素向上找最近的版块祖先，返回版块名（或 ``"全局"``）。"""
    cur = el.parent if el is not None else None
    while cur is not None and getattr(cur, 'name', None):
        classes = cur.get('class') or []
        for c in classes:
            for p in _SECTION_CLASS_PREFIXES:
                if c.startswith(p):
                    return c
        # 到 #canvas 或 body / html 停止
        if cur.get('id') == 'canvas':
            return '全局'
        cur = cur.parent
    return '全局'


def _build_class_to_section(html_path: Path) -> Dict[str, str]:
    """扫 HTML 建立 ``{class: section_name}``。

    重复 class 取首次遇到的版块；P0a 合并后这通常足够，因为合并的 N 个元素
    一般属于同一版块（如 prop 卡都在 "bankuai-1"）。
    """
    soup = BeautifulSoup(html_path.read_text(encoding='utf-8'), 'html.parser')
    out: Dict[str, str] = {}
    for el in soup.find_all(True):
        classes = el.get('class') or []
        if not classes:
            continue
        first = classes[0]
        if first not in out:
            out[first] = _infer_section(el)
    return out


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------

def _md_escape(s: str) -> str:
    return (s or '').replace('|', '\\|').replace('\n', ' ')


def render_mapping_report(
    layer_map: dict,
    class_to_images: Dict[str, List[str]],
    class_to_geom: Dict[str, Dict[str, str]],
    class_to_section: Dict[str, str],
) -> str:
    """生成 ``_mapping_report.md``：class ↔ image ↔ PSD layer 三向映射。"""
    by_class = (layer_map or {}).get('by_class', {})
    rows: List[Tuple[str, str, str, str, str, str, str]] = []
    # 行：(section, class, layer_id, name, type, abs, images)
    seen_classes: set = set()
    for cls, info in by_class.items():
        seen_classes.add(cls)
        images = class_to_images.get(cls, [])
        geom = class_to_geom.get(cls, {})
        abs_str = ''
        if {'left', 'top', 'width', 'height'} <= set(geom.keys()):
            abs_str = (
                f'{geom["left"]},{geom["top"]} '
                f'{geom["width"]}×{geom["height"]}'
            )
        rows.append((
            class_to_section.get(cls, '全局'),
            cls,
            info.get('layer_id', '') or '',
            info.get('name', '') or '',
            info.get('type', '') or '',
            abs_str,
            ', '.join(images),
        ))
    # 补"只在 CSS 有 url 但 layer_map 没有的"虚拟类（如 v-list / v-stack）
    for cls, images in class_to_images.items():
        if cls in seen_classes:
            continue
        geom = class_to_geom.get(cls, {})
        abs_str = ''
        if {'left', 'top', 'width', 'height'} <= set(geom.keys()):
            abs_str = (
                f'{geom["left"]},{geom["top"]} '
                f'{geom["width"]}×{geom["height"]}'
            )
        rows.append((
            class_to_section.get(cls, '全局'),
            cls, '', '', '', abs_str, ', '.join(images),
        ))

    # 按版块 + class 自然序排序
    rows.sort(key=lambda r: (r[0], r[1]))

    lines: List[str] = []
    lines.append('# Mapping Report (class ↔ image ↔ PSD layer)')
    lines.append('')
    lines.append(f'- 总条目: **{len(rows)}**')
    n_with_image = sum(1 for r in rows if r[6])
    n_with_layer = sum(1 for r in rows if r[2])
    lines.append(f'- 含 image url: {n_with_image}')
    lines.append(f'- 含 PSD layer 元数据: {n_with_layer}')
    lines.append('')
    lines.append('| section | class | layer_id | psd name | type | abs | image(s) |')
    lines.append('| --- | --- | --- | --- | --- | --- | --- |')
    for sec, cls, lid, name, ltype, abs_str, imgs in rows:
        lines.append(
            f'| {_md_escape(sec)} | `.{cls}` | `{lid}` | {_md_escape(name)} | '
            f'{ltype} | {abs_str} | {_md_escape(imgs)} |'
        )
    return '\n'.join(lines) + '\n'


def render_image_index(
    layer_map: dict,
    class_to_images: Dict[str, List[str]],
    class_to_section: Dict[str, str],
    images_dir: Optional[Path] = None,
) -> str:
    """生成 ``_image_index.md``：按版块分组的图片清单。

    每张图记录：image_filename | size_bytes | 用途 class | psd layer | psd name。
    若 images 子目录存在，会读 size 并 sort by 版块 → 文件名。
    """
    by_class = (layer_map or {}).get('by_class', {})

    # 反查：image → [(class, section, layer_id, name)]
    image_users: Dict[str, List[Tuple[str, str, str, str]]] = defaultdict(list)
    for cls, urls in class_to_images.items():
        sec = class_to_section.get(cls, '全局')
        info = by_class.get(cls, {})
        for u in urls:
            # url 形如 ``images/bg-f07984.png``，抠出文件名
            fname = u.split('/')[-1]
            image_users[fname].append((
                cls, sec,
                info.get('layer_id', '') or '',
                info.get('name', '') or '',
            ))

    # 按版块分组（取该图首个使用者的 section）
    sections: Dict[str, List[str]] = defaultdict(list)
    for fname, users in image_users.items():
        sec = users[0][1]  # 取第一个使用者的版块
        sections[sec].append(fname)

    # 实际文件大小
    sizes: Dict[str, int] = {}
    if images_dir and images_dir.is_dir():
        for p in images_dir.iterdir():
            if p.is_file():
                try:
                    sizes[p.name] = p.stat().st_size
                except OSError:
                    pass

    lines: List[str] = []
    lines.append('# Image Index (按版块分组)')
    lines.append('')
    total_imgs = sum(len(v) for v in sections.values())
    lines.append(f'- 图片总数（被 CSS 引用）: **{total_imgs}**')
    if sizes:
        used_size = sum(sizes.get(f, 0) for fs in sections.values() for f in fs)
        lines.append(f'- 总字节: {used_size:,} bytes ({used_size / 1024 / 1024:.2f} MB)')
    lines.append('')
    lines.append('用法：设计/产品对照"哪些图属于哪个版块"，方便定位替换。')
    lines.append('')
    for sec in sorted(sections.keys()):
        files = sorted(set(sections[sec]))
        lines.append(f'## {sec}  ({len(files)} 张)')
        lines.append('')
        lines.append('| image | size | 使用者 class | psd layer | psd name |')
        lines.append('| --- | --- | --- | --- | --- |')
        for fname in files:
            sz = sizes.get(fname)
            sz_str = f'{sz:,} B' if sz is not None else '?'
            users = image_users[fname]
            cls_list = ', '.join(f'`.{u[0]}`' for u in users[:5])
            if len(users) > 5:
                cls_list += f' (+{len(users) - 5})'
            layer_ids = ', '.join(u[2] for u in users if u[2])[:80]
            names = ', '.join(_md_escape(u[3]) for u in users if u[3])[:80]
            lines.append(
                f'| `{fname}` | {sz_str} | {cls_list} | {layer_ids} | {names} |'
            )
        lines.append('')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def write_mapping_reports(html_dir: Path) -> Tuple[Optional[Path], Optional[Path]]:
    """便捷入口：在 ``html_dir`` 下读 layer_map.json + style_optimized.css +
    index_optimized.html，写出 ``_mapping_report.md`` + ``_image_index.md``。

    Returns:
        (mapping_path, image_index_path)；任一失败返回 None。
    """
    html_path = html_dir / 'index_optimized.html'
    css_path = html_dir / 'style_optimized.css'
    map_path = html_dir / 'layer_map.json'
    images_dir = html_dir / 'images'

    if not (html_path.exists() and css_path.exists() and map_path.exists()):
        return None, None

    try:
        layer_map = json.loads(map_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        layer_map = {}
    css_text = css_path.read_text(encoding='utf-8')

    class_to_images = _parse_class_to_images(css_text)
    class_to_geom = _parse_class_geometry(css_text)
    class_to_section = _build_class_to_section(html_path)

    mapping_md = render_mapping_report(
        layer_map, class_to_images, class_to_geom, class_to_section,
    )
    image_md = render_image_index(
        layer_map, class_to_images, class_to_section, images_dir=images_dir,
    )

    out_mapping = html_dir / '_mapping_report.md'
    out_image = html_dir / '_image_index.md'
    out_mapping.write_text(mapping_md, encoding='utf-8')
    out_image.write_text(image_md, encoding='utf-8')
    return out_mapping, out_image


__all__ = [
    'write_mapping_reports',
    'render_mapping_report',
    'render_image_index',
]
