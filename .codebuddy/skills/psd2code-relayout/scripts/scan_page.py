#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集页面现状：全页截图切片 + 渲染后的 DOM 结构 + 统计摘要。

这是 AI 的"眼睛"。截图用于视觉判断，dom.json 用于结构化定位。

坐标必须来自**浏览器实测**（`getBoundingClientRect`），不能靠解析 CSS 累加：
产物里存在 flex 容器（子元素用 margin 定位、没有 top/left），静态累加会算错。

用法：
    python scripts/scan_page.py <html_dir> [--html index_optimized.html]
                               [--out .relayout] [--slice-height 1200]
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from typing import Any, Optional

_DOM_JS = """() => {
  const all = [...document.querySelectorAll('div')];
  const index = new Map(all.map((el, i) => [el, i]));
  return all.map((el, i) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const raw = (el.getAttribute('class') || '').split(/\\s+/).filter(Boolean);
    const cls = raw.filter(c => c !== 'layer' && c !== 'layer-group');
    const span = el.querySelector(':scope > span');
    return {
      id: 'n' + i,
      parent: el.parentElement && index.has(el.parentElement) ? 'n' + index.get(el.parentElement) : null,
      class: cls[0] || null,
      classes: cls,
      role: raw.includes('layer-group') ? 'group' : 'layer',
      bbox: [Math.round(r.left + scrollX), Math.round(r.top + scrollY),
             Math.round(r.width), Math.round(r.height)],
      text: span ? ((span.textContent || '').trim().slice(0, 80) || null) : null,
      bg: cs.backgroundImage !== 'none' ? cs.backgroundImage : null,
      z: parseInt(cs.zIndex, 10) || 0,
      position: cs.position,
      overflow: cs.overflow,
      children: el.children.length,
    };
  });
}"""


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="采集产物页面现状（截图 + DOM）")
    parser.add_argument("html_dir", help="产物 html 目录")
    parser.add_argument("--html", default="index_optimized.html", help="目标 HTML 文件名")
    parser.add_argument("--out", default=".relayout", help="输出目录（相对 html_dir）")
    parser.add_argument("--slice-height", type=int, default=1200, help="截图切片高度")
    args = parser.parse_args(argv)

    html_dir = Path(args.html_dir).expanduser().resolve()
    html_path = html_dir / args.html
    if not html_path.exists():
        raise SystemExit(f"找不到 {html_path}")

    out_dir = (html_dir / args.out).resolve()
    (out_dir / "slices").mkdir(parents=True, exist_ok=True)

    try:
        from PIL import Image
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "需要 playwright 与 Pillow：\n"
            "  pip install playwright pillow\n"
            "  python -m playwright install chromium"
        ) from exc

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 750, "height": 1334})
        page.goto(html_path.as_uri())
        page.wait_for_timeout(1200)
        raw = page.screenshot(full_page=True)
        metrics = page.evaluate(
            "() => ({w: document.documentElement.scrollWidth,"
            " h: document.documentElement.scrollHeight})"
        )
        nodes: list[dict[str, Any]] = page.evaluate(_DOM_JS)
        browser.close()

    (out_dir / "base.png").write_bytes(raw)
    image = Image.open(io.BytesIO(raw)).convert("RGB")

    slices = []
    for index, top in enumerate(range(0, image.height, args.slice_height)):
        bottom = min(image.height, top + args.slice_height)
        name = f"slices/slice-{index:02d}.png"
        image.crop((0, top, image.width, bottom)).save(out_dir / name)
        slices.append({"file": name, "y0": top, "y1": bottom})

    payload = {
        "source": html_path.name,
        "canvas": {"width": metrics["w"], "height": metrics["h"]},
        "slices": slices,
        "nodes": nodes,
    }
    (out_dir / "dom.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    groups = [n for n in nodes if n["role"] == "group"]
    img_nodes = [n for n in nodes if n["bg"]]
    summary = [
        "# 页面扫描摘要",
        "",
        f"- 源文件：`{html_path.name}`",
        f"- 画布：{metrics['w']} × {metrics['h']}",
        f"- `div` 节点：{len(nodes)}（组 {len(groups)} / 图片层 {len(img_nodes)}）",
        f"- 截图切片：{len(slices)} 张（每张高 {args.slice_height}px，存于 `slices/`）",
        "",
        "## 组内多图层清单（**仅供参考，合不合并由你看图决定**）",
        "",
        "| 组 | 位置 | 图片层 | 文本层 | z 范围 |",
        "|---|---|---|---|---|",
    ]
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        if node["parent"]:
            by_parent.setdefault(node["parent"], []).append(node)

    for group in groups:
        kids = by_parent.get(group["id"], [])
        imgs = [k for k in kids if k["bg"]]
        texts = [k for k in kids if k["text"]]
        if len(imgs) >= 2:
            zs = [k["z"] for k in imgs]
            summary.append(
                f"| `{group['class']}` | {group['bbox'][:2]} | {len(imgs)} | {len(texts)} | "
                f"{min(zs)}~{max(zs)} |"
            )
    (out_dir / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")

    print(json.dumps({
        "out_dir": str(out_dir),
        "canvas": metrics,
        "nodes": len(nodes),
        "slices": len(slices),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
