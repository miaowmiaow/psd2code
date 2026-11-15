#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""像素裁决：对比两个 HTML 的渲染结果，判定改动是否可接受。

AI 负责改，这个脚本负责"是否允许保留"。任何一次改动都必须先过这里。

用法：
    python scripts/verify_page.py <base.html> <candidate.html>
    python scripts/verify_page.py base.html cand.html --max-delta 1 --region 0,4000,750,600

输出（stdout, JSON）：
    {"pass": true, "max_delta": 1, "changed_pixels": 2672,
     "ratio": 0.00053, "diff_image": ".../diff.png"}

退出码：0 通过，1 未通过，2 环境错误。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path
from typing import Optional


def _canvas_size(html_path: Path, default: tuple[int, int] = (750, 1334)) -> tuple[int, int]:
    """从同目录 CSS 的 ``#canvas`` 读设计尺寸。"""
    for name in ("style_optimized.css", "style.css", "style_ai.css"):
        css = html_path.parent / name
        if not css.exists():
            continue
        text = css.read_text(encoding="utf-8")
        block = re.search(r"#canvas\s*\{([^}]*)\}", text, re.S)
        if not block:
            continue
        body = block.group(1)

        def decl(prop: str) -> int:
            found = re.search(rf"{prop}\s*:\s*(-?[\d.]+)px", body)
            return int(float(found.group(1))) if found else 0

        width, height = decl("width"), decl("height")
        if width and height:
            return width, height
    return default


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="对比两个 HTML 的渲染像素差")
    parser.add_argument("base", help="基准 HTML（原产物）")
    parser.add_argument("candidate", help="改动后的 HTML")
    parser.add_argument("--max-delta", type=int, default=1, help="允许的最大通道差（默认 1）")
    parser.add_argument("--region", default=None, help="只比该区域 x,y,w,h（页面坐标）")
    parser.add_argument("--out", default=None, help="diff 图输出路径")
    args = parser.parse_args(argv)

    base = Path(args.base).expanduser().resolve()
    candidate = Path(args.candidate).expanduser().resolve()
    for path in (base, candidate):
        if not path.exists():
            raise SystemExit(f"找不到 {path}")

    try:
        import numpy as np
        from PIL import Image
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        print(json.dumps({"error": str(exc), "hint": "pip install playwright numpy pillow"}))
        return 2

    width, height = _canvas_size(base)

    def shot(path: Path):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome")
            page = browser.new_page(viewport={"width": width, "height": min(height, 1334)})
            page.goto(path.as_uri())
            page.wait_for_timeout(1200)
            raw = page.screenshot(full_page=True)
            browser.close()
        return np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"), dtype=np.int16), raw

    a, _ = shot(base)
    b, _ = shot(candidate)

    if args.region:
        x, y, w, h = (int(v) for v in args.region.split(","))
        a = a[y:y + h, x:x + w]
        b = b[y:y + h, x:x + w]

    if a.shape != b.shape:
        print(json.dumps({
            "pass": False,
            "reason": f"页面尺寸变化 {a.shape} -> {b.shape}",
            "max_delta": 255,
        }, ensure_ascii=False))
        return 1

    diff = np.abs(a - b).max(axis=2)
    changed = int((diff > 0).sum())
    result = {
        "pass": int(diff.max()) <= args.max_delta,
        "max_delta": int(diff.max()),
        "changed_pixels": changed,
        "total_pixels": int(diff.size),
        "ratio": round(changed / diff.size, 6),
        "threshold": args.max_delta,
    }

    if changed:
        out = Path(args.out) if args.out else candidate.parent / (candidate.stem + "-diff.png")
        heat = np.zeros((*diff.shape, 3), dtype=np.uint8)
        heat[..., 2] = 40
        mask = diff > args.max_delta
        heat[mask] = (255, 0, 0)
        mask_soft = (diff > 0) & ~mask
        heat[mask_soft] = (255, 200, 0)
        Image.fromarray(heat).save(out)
        result["diff_image"] = str(out)

    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
