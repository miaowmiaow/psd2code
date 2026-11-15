#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集现状：全页截图切片 + 容器坐标清单。

重建区块的第一步是"看图定结构"，这个脚本只提供原料：
  * slices/*.png   —— 按视口高度切片的截图，逐张看；
  * containers.json —— 每个容器的 class / 页面坐标 / 子层数（用来找"行容器"）。

坐标一律浏览器实测。不要用 CSS 的 left/top 累加——flex 容器内的子元素
用 margin 定位，静态累加必然算错（实测整块偏移 61.7%）。

用法：
    python scripts/scan_page.py <html_dir> [--src index_optimized.html]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

_COLLECT_JS = """() => {
  const out = [];
  for (const el of document.querySelectorAll('div')) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const kids = [...el.children].filter(c => c.tagName === 'DIV');
    if (!kids.length) continue;                    // 叶子图层不参与结构判断
    const raw = (el.getAttribute('class') || '').split(/\\s+/).filter(Boolean);
    out.push({
      cls: raw[0] || null,
      classes: raw,
      box: [Math.round(r.left + scrollX), Math.round(r.top + scrollY),
            Math.round(r.width), Math.round(r.height)],
      children: kids.length,
      texts: el.querySelectorAll('span').length,
      depth: (() => { let d = 0, p = el; while (p && p.tagName === 'DIV') { d++; p = p.parentElement; } return d; })(),
    });
  }
  return {
    canvas: {width: document.documentElement.scrollWidth,
             height: document.documentElement.scrollHeight},
    containers: out,
  };
}"""


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='采集全页截图切片与容器坐标')
    parser.add_argument('html_dir')
    parser.add_argument('--src', default='index_optimized.html')
    parser.add_argument('--out', default='.scan')
    parser.add_argument('--slice-height', type=int, default=1200)
    parser.add_argument('--viewport', default='750x1334')
    args = parser.parse_args(argv)

    try:
        from PIL import Image
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(f'需要 playwright 与 Pillow：{exc}') from exc

    import io

    base = Path(args.html_dir).expanduser().resolve()
    src = base / args.src
    if not src.exists():
        raise SystemExit(f'找不到 {src}')
    out_dir = (base / args.out).resolve()
    (out_dir / 'slices').mkdir(parents=True, exist_ok=True)

    vw, vh = (int(v) for v in args.viewport.lower().split('x'))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome')
        page = browser.new_page(viewport={'width': vw, 'height': vh})
        page.goto(src.as_uri())
        page.wait_for_timeout(1200)
        raw = page.screenshot(full_page=True)
        data: dict[str, Any] = page.evaluate(_COLLECT_JS)
        browser.close()

    image = Image.open(io.BytesIO(raw)).convert('RGB')
    slices = []
    for i, top in enumerate(range(0, image.height, args.slice_height)):
        bottom = min(image.height, top + args.slice_height)
        name = f'slices/slice-{i:02d}.png'
        image.crop((0, top, image.width, bottom)).save(out_dir / name)
        slices.append({'file': name, 'y0': top, 'y1': bottom})

    data['slices'] = slices
    (out_dir / 'containers.json').write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8'
    )

    # 按「子层数 ≥3 且宽度一致」粗筛出可能的行容器，供看图时对照
    from collections import Counter
    widths = Counter(c['box'][2] for c in data['containers'] if c['children'] >= 3)
    repeated = [w for w, n in widths.items() if n >= 2]

    print(json.dumps({
        'out_dir': str(out_dir),
        'canvas': data['canvas'],
        'containers': len(data['containers']),
        'slices': len(slices),
        'repeated_widths': repeated[:8],
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
