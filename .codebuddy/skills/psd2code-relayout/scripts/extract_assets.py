#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把产物解构成**扁平资源池**：素材与结构彻底解耦。

背景
----
PSD 的图层摆放不遵守前端规范，原产物的 DOM 结构（谁是谁的父子、谁在第几层）
没有语义价值，不能作为改版依据。能复用的只有两样东西：

  1. 素材——哪张图、哪段文字、什么颜色、多大字号；
  2. 验收基准——改完之后和原渲染做像素比对。

所以这里把产物打散成 ``assets.json``：每个视觉元素独立成条，**不带父子关系**。
后续任何布局方案都只引用素材 id，不再回头读 DOM。

用法：
    python scripts/extract_assets.py <html_dir>
    python scripts/extract_assets.py <html_dir> --html index_optimized.html --out .relayout

产出：
    .relayout/assets.json          扁平素材池
    .relayout/assets_preview.png   素材总览图（按页面 y 排序，便于肉眼核对）
"""
from __future__ import annotations

import argparse
import io
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Optional

_COLLECT_JS = """() => {
  // getComputedStyle().backgroundImage 给的是解析后的绝对 URL，
  // 这里统一还原成 ``images/<file>``，方便跨机器复用与回读。
  const clean = (s) => {
    const m = (s || '').match(/url\\(["']?([^"')]+)["']?\\)/);
    if (!m) return null;
    const raw = m[1];
    const idx = raw.indexOf('/images/');
    return idx >= 0 ? 'images/' + raw.slice(idx + 8) : raw;
  };

  const images = [];
  const texts = [];

  // 文本陷阱：span 不在容器的直接子级时，合并子层会把它连带删除。
  // 同一个 span 只报一次（取最外层容器），避免嵌套重复计数。
  const textTraps = [];
  const trapped = new Set();

  [...document.querySelectorAll('div')].forEach((el, i) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;

    const cs = getComputedStyle(el);
    const bbox = [Math.round(r.left + scrollX), Math.round(r.top + scrollY),
                  Math.round(r.width), Math.round(r.height)];
    const z = parseInt(cs.zIndex, 10) || 0;
    const raw = (el.getAttribute('class') || '').split(/\\s+/).filter(Boolean);
    const cls = raw.filter(c => c !== 'layer' && c !== 'layer-group')[0] || null;

    const bg = cs.backgroundImage;
    if (bg && bg !== 'none') {
      const urls = bg.split(/,(?=url\\()/).map(clean).filter(Boolean);
      images.push({node: i, cls, bbox, z, src: urls[0] || null,
                   extra_srcs: urls.slice(1)});
    }

    // 文本陷阱：span 嵌在更深的 div 里 → 合并子层前必须 extract
    const deepSpan = el.querySelector('span');
    if (deepSpan && deepSpan.parentElement !== el && !trapped.has(deepSpan)) {
      trapped.add(deepSpan);
      let depth = 0;
      const chain = [];
      let node = deepSpan.parentElement;
      while (node && node !== el) {
        depth += 1;
        const c = (node.getAttribute('class') || '').split(/\\s+/).filter(Boolean)[0];
        if (c) chain.push(c);
        node = node.parentElement;
      }
      if (node === el && depth > 0) {
        textTraps.push({
          node: i, container: cls, depth,
          chain: chain.reverse(),
          text: (deepSpan.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 24),
        });
      }
    }

    const span = el.querySelector(':scope > span');
    if (span) {
      const scs = getComputedStyle(span);
      texts.push({
        node: i, cls, bbox, z,
        text: (span.textContent || '').replace(/\\s+/g, ' ').trim(),
        style: {
          font_size: parseFloat(scs.fontSize) || 0,
          line_height: scs.lineHeight,
          color: scs.color,
          align: scs.textAlign,
          weight: scs.fontWeight,
          family: scs.fontFamily.split(',')[0].replace(/["']/g, ''),
        },
      });
    }
  });

  return {
    canvas: {width: document.documentElement.scrollWidth,
             height: document.documentElement.scrollHeight},
    images, texts, textTraps,
  };
}"""


def _contact_sheet(raw: dict[str, Any], images_dir: Path, out_path: Path) -> int:
    """把图片素材贴成一张总览图，按页面 y 排序，方便肉眼核对。"""
    from PIL import Image

    items = [im for im in raw["images"] if im.get("src")]
    if not items:
        return 0

    tiles = []
    for item in sorted(items, key=lambda x: (x["bbox"][1], x["bbox"][0])):
        path = images_dir / Path(item["src"]).name
        if not path.exists():
            continue
        try:
            img = Image.open(path).convert("RGBA")
        except Exception:
            continue
        scale = min(1.0, 96 / max(img.width, img.height))
        if scale < 1.0:
            img = img.resize((max(1, int(img.width * scale)),
                              max(1, int(img.height * scale))), Image.LANCZOS)
        tiles.append(img)

    if not tiles:
        return 0

    cols = min(10, max(1, len(tiles)))
    cell_w = max(t.width for t in tiles) + 12
    cell_h = max(t.height for t in tiles) + 12
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (238, 238, 242))
    for index, tile in enumerate(tiles):
        cx = (index % cols) * cell_w + (cell_w - tile.width) // 2
        cy = (index // cols) * cell_h + (cell_h - tile.height) // 2
        sheet.paste(tile, (cx, cy), tile)
    sheet.save(out_path)
    return len(tiles)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="把产物解构成扁平资源池（assets.json）")
    parser.add_argument("html_dir", help="产物 html 目录")
    parser.add_argument("--html", default="index_optimized.html", help="目标 HTML 文件名")
    parser.add_argument("--out", default=".relayout", help="输出目录（相对 html_dir）")
    parser.add_argument("--viewport", default=None, help="视口，形如 750x1334；默认自动探测")
    args = parser.parse_args(argv)

    html_dir = Path(args.html_dir).expanduser().resolve()
    html_path = html_dir / args.html
    if not html_path.exists():
        raise SystemExit(f"找不到 {html_path}")

    out_dir = (html_dir / args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "需要 playwright：pip install playwright && python -m playwright install chromium"
        ) from exc

    width, height = 750, 1334
    if args.viewport:
        width, height = (int(v) for v in args.viewport.lower().split("x"))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(html_path.as_uri())
        page.wait_for_timeout(1200)
        raw = page.evaluate(_COLLECT_JS)
        browser.close()

    assets = raw["images"]
    texts = raw["texts"]
    canvas_area = max(raw["canvas"]["width"] * raw["canvas"]["height"], 1)

    # 素材 id：扁平编号，与源 DOM 节点号解耦
    for index, item in enumerate(assets, 1):
        item["id"] = f"img-{index:03d}"
        x, y, w, h = item["bbox"]
        # 覆盖大半画布的是底图，不是"可摆放的素材"，单独标出来
        item["role"] = "background" if (w * h) / canvas_area >= 0.5 else "element"
    for index, item in enumerate(texts, 1):
        item["id"] = f"txt-{index:03d}"

    src_counter = Counter(Path(item["src"]).name for item in assets if item.get("src"))
    text_counter = Counter(item["text"] for item in texts if item["text"])

    palette = Counter()
    for item in texts:
        palette[item["style"]["color"]] += 1

    payload = {
        "source": html_path.name,
        "canvas": raw["canvas"],
        "images": assets,
        "texts": texts,
        "palette": [c for c, _ in palette.most_common(24)],
        "reuse": {
            "images": {name: n for name, n in src_counter.most_common() if n > 1},
            "texts": {text: n for text, n in text_counter.most_common() if n > 1},
        },
        # 合并子层前必须把这些 span 先 extract 出来，否则会连带删除
        "text_traps": raw.get("textTraps", []),
        "note": "扁平素材池：无父子关系，供视觉重排时按 bbox 取用；原 DOM 结构不作依据。",
    }
    (out_dir / "assets.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    preview = out_dir / "assets_preview.png"
    tiled = _contact_sheet(raw, html_dir / "images", preview)

    print(json.dumps({
        "out_dir": str(out_dir),
        "canvas": raw["canvas"],
        "images": len(assets),
        "texts": len(texts),
        "unique_image_files": len(src_counter),
        "reused_image_files": sum(1 for n in src_counter.values() if n > 1),
        "text_traps": len(raw.get("textTraps", [])),
        "preview_tiles": tiled,
        "preview": str(preview) if tiled else None,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
