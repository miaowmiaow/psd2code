#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按截图重建一个重复区块：grid 骨架 + 渲染截图作素材 + 文字保留 DOM。

与「改现有 DOM」的区别：
  产物的 div 结构完全不参与新结构的设计——只从它身上取两样东西：
    1. 每一格的屏幕坐标（浏览器实测，不是解析 CSS 累加）；
    2. 每一格的图形外观（直接截渲染结果，天然零误差）。
  新结构由「几行几列、列宽多少、间距多少」决定，类名按前端规范重新命名。

用法：
    python scripts/rebuild_block.py <html_dir> \\
        --rows section-bg-3,section-bg-7,section-bg-4 \\
        --name prop-wall --slot-name prop-slot

产出（不覆盖原产物）：
    index_ai.html / style_ai.css
    终端打印：原始 div 数、新 div 数、重建区域与新旧像素差

限制（都是实测结论，见 references/pitfalls.md）：
  * 只处理「行 × 列」的规则网格；行数/列数由 row class 的实际子元素决定；
  * 每格被压成一张 PNG，图形不再可编辑；文字仍是 DOM 文本；
  * 列间距、行间距允许不相等，脚本会把间隙并入 grid 轨道宽/高。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

_LAYERS_JS = """(col) => {
  const cr = col.getBoundingClientRect();
  const layers = [];
  [...col.querySelectorAll('div')].forEach((el, order) => {
    const cs = getComputedStyle(el);
    const bg = cs.backgroundImage;
    if (!bg || bg === 'none') return;
    const m = bg.match(/url\\(["']?([^"')]+)["']?\\)/);
    if (!m) return;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;
    const idx = m[1].indexOf('/images/');
    layers.push({
      src: idx >= 0 ? m[1].slice(idx + 8) : m[1],
      x: Math.round(r.left - cr.left),
      y: Math.round(r.top - cr.top),
      w: Math.round(r.width), h: Math.round(r.height),
      z: parseInt(cs.zIndex, 10) || 0,
      order,
    });
  });
  return layers;
}"""

_COLLECT_JS = """(rowCls) => {
  const rows = [...document.querySelectorAll('.' + rowCls)];
  const out = [];
  for (const row of rows) {
    const cols = [...row.children].filter(c => c.tagName === 'DIV');
    const items = cols.map(col => {
      const r = col.getBoundingClientRect();
      const texts = [...col.querySelectorAll('span')].map(sp => {
        const sr = sp.getBoundingClientRect();
        const cs = getComputedStyle(sp);
        return {
          text: (sp.textContent || '').trim(),
          dx: Math.round(sr.left - r.left),
          dy: Math.round(sr.top - r.top),
          w: Math.round(sr.width),
          h: Math.round(sr.height),
          size: parseFloat(cs.fontSize),
          color: cs.color,
          weight: cs.fontWeight,
        };
      });
      return {
        cls: (col.getAttribute('class') || '').split(/\\s+/)[0] || null,
        box: [Math.round(r.left + scrollX), Math.round(r.top + scrollY),
              Math.round(r.width), Math.round(r.height)],
        texts,
      };
    });
    out.push(items);
  }
  return out;
}"""


def _parse_css_rules(css: str) -> int:
    return len(re.findall(r'^\.[\w-]+\s*\{', css, re.M))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='按截图重建重复区块（grid + 截图素材）')
    parser.add_argument('html_dir')
    parser.add_argument('--rows', required=True, help='构成网格的各行容器 class，逗号分隔，按视觉从上到下')
    parser.add_argument('--name', default='rebuilt-block', help='新容器类名')
    parser.add_argument('--slot-name', default=None, help='新单元类名；默认 <name>__cell')
    parser.add_argument('--src', default='index_optimized.html')
    parser.add_argument('--src-css', default='style_optimized.css')
    parser.add_argument('--compare', action='store_true', help='重建后截新旧区域做像素对比')
    parser.add_argument('--mode', choices=('compose', 'screenshot'), default='compose',
                        help='compose=逐层取原始 PNG 合成透明底（推荐）；screenshot=整格截图')
    args = parser.parse_args(argv)

    try:
        from bs4 import BeautifulSoup
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(f'需要 playwright 与 bs4：{exc}') from exc

    base = Path(args.html_dir).expanduser().resolve()
    src_html = base / args.src
    src_css = base / args.src_css
    if not src_html.exists() or not src_css.exists():
        raise SystemExit(f'缺少 {src_html.name} / {src_css.name}')

    row_classes = [c.strip() for c in args.rows.split(',') if c.strip()]
    slot_name = args.slot_name or f'{args.name}__cell'
    soup = BeautifulSoup(src_html.read_text(encoding='utf-8'), 'html.parser')
    images_dir = base / 'images'

    # ---------- 1. 采集坐标 + 文本，并截每格的图形 ----------
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome')
        page = browser.new_page(viewport={'width': 750, 'height': 1334})
        page.goto(src_html.as_uri())
        page.wait_for_timeout(1200)

        rows_data: list[list[dict[str, Any]]] = []
        for cls in row_classes:
            rows_data.extend(page.evaluate(_COLLECT_JS, cls))
        if not rows_data:
            raise SystemExit('没有采集到任何行，检查 --rows 的 class 是否正确')

        # 新节点挂在原行的父容器下，坐标需要转成相对父容器
        parent_box = page.evaluate(
            """(cls) => {
                 const first = document.querySelector('.' + cls);
                 const p = first && first.parentElement;
                 if (!p) return [0, 0];
                 const r = p.getBoundingClientRect();
                 return [Math.round(r.left + scrollX), Math.round(r.top + scrollY)];
               }""",
            row_classes[0],
        )

        if args.mode == 'screenshot':
            # 整格截图（含父容器背景，不透明）——仅用于快速比对，不推荐出品
            page.evaluate(
                "() => document.querySelectorAll('span').forEach(s => s.style.visibility='hidden')"
            )
            page.wait_for_timeout(200)
            for ri, row in enumerate(rows_data):
                for ci, item in enumerate(row):
                    x, y, w, h = item['box']
                    raw = page.screenshot(
                        clip={'x': x, 'y': y, 'width': w, 'height': h}, full_page=True
                    )
                    fname = f'{args.name}-{ri + 1}-{ci + 1}.png'
                    (images_dir / fname).write_bytes(raw)
                    item['file'] = fname
        else:
            # 合成：逐层取原始 PNG，用实测 bbox 叠到透明画布上。
            # 用截图会把父容器的底色一起烘焙进去（不可换肤、体积翻几倍）。
            flat_handles = []
            for cls in row_classes:
                flat_handles.extend(page.query_selector_all(f'.{cls} > div'))
            flat_items = [it for row in rows_data for it in row]
            for idx, item in enumerate(flat_items):
                if idx < len(flat_handles):
                    item['layers'] = page.evaluate(_LAYERS_JS, flat_handles[idx])
        browser.close()

    # ---------- 1.5 合成透明底图形（mode=compose） ----------
    if args.mode != 'screenshot':
        from hashlib import md5

        from PIL import Image

        cache: dict[str, str] = {}
        for ri, row in enumerate(rows_data):
            for ci, item in enumerate(row):
                w, h = item['box'][2], item['box'][3]
                # 按 DOM 文档顺序（先序）叠加：父子之间的绘制顺序由文档顺序决定，
                # 与 z-index 无关。按 z 排序会让"子元素被父背景盖住"
                # （实测：宝箱列 btn-locked z=101 是 prop-bg-18 z=103 的子元素，
                #  按 z 排会丢掉"待开启"文字）。
                layers = sorted(item.get('layers') or [], key=lambda l: l['order'])
                # 图层允许负坐标或超出列盒（如宝箱的底板 box=[-5,96,140,80]），
                # 直接按列尺寸建画布 alpha_composite 会错位；先扩画布再裁回。
                min_x = min([0] + [l['x'] for l in layers])
                min_y = min([0] + [l['y'] for l in layers])
                max_x = max([w] + [l['x'] + l['w'] for l in layers])
                max_y = max([h] + [l['y'] + l['h'] for l in layers])
                canvas = Image.new('RGBA', (max_x - min_x, max_y - min_y), (0, 0, 0, 0))
                for layer in layers:
                    path = images_dir / Path(layer['src']).name
                    if not path.exists():
                        continue
                    img = Image.open(path).convert('RGBA')
                    # 浏览器会把背景图画在元素盒内并裁掉溢出部分
                    if img.width > layer['w'] or img.height > layer['h']:
                        img = img.crop((0, 0, min(img.width, layer['w']),
                                        min(img.height, layer['h'])))
                    canvas.alpha_composite(img, (layer['x'] - min_x, layer['y'] - min_y))
                canvas = canvas.crop((-min_x, -min_y, -min_x + w, -min_y + h))
                digest = md5(canvas.tobytes()).hexdigest()[:10]
                if digest in cache:
                    item['file'] = cache[digest]
                    continue
                fname = f'{args.name}-{digest}.png'
                canvas.save(images_dir / fname)
                cache[digest] = fname
                item['file'] = fname

    # ---------- 2. 算网格参数 ----------
    all_items = [it for row in rows_data for it in row]
    left = min(it['box'][0] for it in all_items)
    top = min(it['box'][1] for it in all_items)
    right = max(it['box'][0] + it['box'][2] for it in all_items)
    bottom = max(it['box'][1] + it['box'][3] for it in all_items)

    first_row = rows_data[0]
    col_x = [it['box'][0] for it in first_row]
    track_w = [
        (col_x[i + 1] - col_x[i]) if i + 1 < len(col_x) else (right - col_x[i])
        for i in range(len(col_x))
    ]
    row_y = [row[0]['box'][1] for row in rows_data]
    row_track_h = [
        (row_y[i + 1] - row_y[i]) if i + 1 < len(row_y) else (bottom - row_y[i])
        for i in range(len(row_y))
    ]

    # ---------- 3. 生成新结构 ----------
    parts = [f'<section class="{args.name}">']
    for ri, row in enumerate(rows_data):
        for item in row:
            x, y, w, h = item['box']
            dy = y - row_y[ri]
            mt = f' margin-top:{dy}px;' if dy else ''
            texts = ''.join(
                f'<span class="{slot_name}__text" style="left:{t["dx"]}px;top:{t["dy"]}px;'
                f'width:{t["w"]}px;height:{t["h"]}px;font-size:{t["size"]}px;'
                f'color:{t["color"]};font-weight:{t["weight"]}">{t["text"]}</span>'
                for t in item['texts']
            )
            parts.append(
                f'<div class="{slot_name}" style="{mt.strip()}">'
                f'<img class="{slot_name}__art" src="images/{item["file"]}" '
                f'width="{w}" height="{h}" alt="">{texts}</div>'
            )
    parts.append('</section>')

    # ---------- 4. 替换 DOM（挂在原父容器下，坐标转相对） ----------
    anchor = soup.find('div', class_=row_classes[0])
    if anchor is None:
        raise SystemExit(f'HTML 里找不到 .{row_classes[0]}')
    parent = anchor.parent
    for cls in row_classes:
        node = soup.find('div', class_=cls)
        if node is not None:
            node.decompose()
    parent.append(BeautifulSoup(''.join(parts), 'html.parser'))

    # ---------- 5. 写 CSS ----------
    css = src_css.read_text(encoding='utf-8')
    removed = 0
    for cls in row_classes:
        css, n = re.subn(r'^\.' + re.escape(cls) + r'\s*\{[^}]*\}\s*$', '', css, flags=re.M)
        removed += n

    # 被替换掉的子层（prop / prop-icon / rounded-24 …）在 DOM 里已不存在，
    # 它们的规则是死规则，一并清掉。产物是静态的，不存在运行时加 class 的情况。
    alive = {c for el in soup.find_all(True) for c in (el.get('class') or [])}

    def _drop_dead(match: re.Match) -> str:
        nonlocal removed
        if match.group(1) in alive:
            return match.group(0)
        removed += 1
        return ''

    css = re.sub(r'^\.([\w-]+)\s*\{[^}]*\}\s*$', _drop_dead, css, flags=re.M)

    rel_left, rel_top = left - parent_box[0], top - parent_box[1]
    css = css.rstrip() + f"""

/* ---------- 截图驱动重建：{args.name} ---------- */
.{args.name} {{
  position: absolute;
  left: {rel_left}px; top: {rel_top}px;
  width: {right - left}px; height: {bottom - top}px;
  display: grid;
  /* 轨道宽 = 本列宽 + 到下一列的间距；间距不等时也能精确复现列起点 */
  grid-template-columns: {' '.join(f'{w}px' for w in track_w)};
  grid-template-rows: {' '.join(f'{h}px' for h in row_track_h)};
  align-items: start;
}}
.{slot_name} {{
  position: relative;
  display: flex;
  flex-direction: column;
}}
.{slot_name}__art {{ display: block; }}
.{slot_name}__text {{
  position: absolute;
  overflow: hidden;
  line-height: 1;
  white-space: nowrap;
}}
"""
    out_css = base / 'style_ai.css'
    out_css.write_text(css, encoding='utf-8')

    html = str(soup).replace(src_css.name, out_css.name)
    out_html = base / 'index_ai.html'
    out_html.write_text(html, encoding='utf-8')

    stats = {
        'rows': len(rows_data),
        'cols': len(first_row),
        'grid': [left, top, right - left, bottom - top],
        'track_w': track_w,
        'track_h': row_track_h,
        'removed_rules': removed,
        'div_before': len(BeautifulSoup(src_html.read_text(encoding='utf-8'), 'html.parser').find_all('div')),
        'div_after': len(soup.find_all('div')),
        'css_rules_before': _parse_css_rules(src_css.read_text(encoding='utf-8')),
        'css_rules_after': _parse_css_rules(css),
    }

    # ---------- 6. 可选：区域像素对比 ----------
    if args.compare:
        from PIL import Image
        import numpy as np

        clip = {'x': max(0, left - 20), 'y': max(0, top - 20),
                'width': (right - left) + 40, 'height': (bottom - top) + 40}

        def shoot(path: Path) -> bytes:
            with sync_playwright() as pw2:
                b2 = pw2.chromium.launch(channel='chrome')
                p2 = b2.new_page(viewport={'width': 750, 'height': 1334})
                p2.goto(path.as_uri())
                p2.wait_for_timeout(1200)
                raw = p2.screenshot(clip=clip, full_page=True)
                b2.close()
            return raw

        a = np.asarray(Image.open(io.BytesIO(shoot(src_html))).convert('RGB'), dtype=np.int16)
        b = np.asarray(Image.open(io.BytesIO(shoot(out_html))).convert('RGB'), dtype=np.int16)
        if a.shape == b.shape:
            d = np.abs(a - b).max(axis=2)
            stats['diff_pixels'] = int((d > 2).sum())
            stats['diff_ratio'] = round(float((d > 2).sum()) / d.size, 5)
            stats['max_delta'] = int(d.max())
        else:
            stats['diff_ratio'] = 'size mismatch'

    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
