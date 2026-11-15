#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把若干图片层按 z-index 拍平成一张 PNG，并给出可直接粘贴的 CSS。

只做"合成 + 生成 CSS 片段"，**不修改任何文件**——改不改、怎么改，由 AI 决定。
这样 AI 可以先用 verify_page.py 试，再决定是否落盘。

支持的两种承载层：
  1. 图层承载（carrier 自己带 background-image，与其它层同父）——坐标直接 union；
  2. 容器承载（carrier 是纯容器，子层坐标相对它）——以 container 为原点，
     画布不小于容器自身尺寸。

用法：
    python scripts/merge_layers.py <html_dir> --layers shape-3,yue
    python scripts/merge_layers.py <html_dir> --layers avatar,ellipse-2,avatar-3
    python scripts/merge_layers.py <html_dir> --layers a,b,c --into a

输出（stdout, JSON）：
    {"image": "images/merged.png", "width": 138, "height": 69, "css": "...",
     "remove": [".yue"], "warnings": [...], "layers": [...]}
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional


def parse_css(text: str) -> dict[str, dict[str, str]]:
    rules: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"\.([\w-]+)\s*\{([^}]*)\}", text):
        cls, body = match.group(1), match.group(2)
        decls: dict[str, str] = {}
        for part in body.split(";"):
            if ":" in part:
                key, value = part.split(":", 1)
                if key.strip():
                    decls[key.strip()] = value.strip()
        if decls:
            rules[cls] = decls
    return rules


def _num(decls: dict[str, str], prop: str, default: float = 0.0) -> float:
    raw = decls.get(prop)
    if not raw:
        return default
    found = re.search(r"-?[\d.]+", raw)
    return float(found.group(0)) if found else default


def _bg(decls: dict[str, str]) -> Optional[str]:
    raw = decls.get("background", "") + decls.get("background-image", "")
    found = re.search(r'url\("?([^")]+)"?\)', raw)
    return found.group(1) if found else None


def _exact_find(soup, cls: str):
    """按 class 精确匹配（避免 'avatar' 命中 'avatar-2'）。"""
    return soup.find("div", class_=lambda c: bool(c) and cls in c)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="把多个图片层合成为一张 PNG")
    parser.add_argument("html_dir", help="产物 html 目录（含 CSS 与 images/）")
    parser.add_argument("--layers", required=True, help="逗号分隔的 class 列表（承载层在前）")
    parser.add_argument("--into", default=None, help="承载层 class；默认取 --layers 第一个")
    parser.add_argument("--out", default=None, help="输出 PNG（相对 html_dir）")
    parser.add_argument("--css-file", default=None, help="CSS 文件名；默认自动探测")
    parser.add_argument("--html-file", default="index_optimized.html", help="HTML 文件名（结构体检用）")
    args = parser.parse_args(argv)

    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        print(json.dumps({"error": str(exc)}))
        return 2

    html_dir = Path(args.html_dir).expanduser().resolve()
    images_dir = html_dir / "images"
    if not images_dir.is_dir():
        raise SystemExit(f"找不到 images 目录：{images_dir}")

    css_path = None
    for name in ([args.css_file] if args.css_file else
                 ["style_optimized.css", "style_ai.css", "style.css"]):
        candidate = html_dir / name
        if candidate.exists():
            css_path = candidate
            break
    if css_path is None:
        raise SystemExit("找不到 CSS 文件")
    rules = parse_css(css_path.read_text(encoding="utf-8"))

    classes = [c.strip() for c in args.layers.split(",") if c.strip()]
    if len(classes) < 2:
        raise SystemExit("至少需要 2 个层")
    carrier = args.into or classes[0]

    carrier_decls = rules.get(carrier)
    if not carrier_decls:
        raise SystemExit(f"CSS 中找不到 .{carrier}")
    carrier_has_bg = _bg(carrier_decls) is not None
    carrier_box = {
        "left": _num(carrier_decls, "left"),
        "top": _num(carrier_decls, "top"),
        "width": _num(carrier_decls, "width"),
        "height": _num(carrier_decls, "height"),
        "z": _num(carrier_decls, "z-index"),
    }

    layers = []
    for cls in classes:
        decls = rules.get(cls)
        if not decls:
            raise SystemExit(f"CSS 中找不到 .{cls}")
        url = _bg(decls)
        if url is None:
            if cls != carrier:
                raise SystemExit(f".{cls} 没有 background-image")
            continue  # 纯容器承载：只提供画布原点与尺寸
        if "background-size" in decls:
            raise SystemExit(f".{cls} 带 background-size，缩放层不能直接拍平")
        file = images_dir / Path(url).name
        if not file.exists():
            raise SystemExit(f"图片缺失：{file}")
        layers.append({
            "class": cls,
            "left": _num(decls, "left"),
            "top": _num(decls, "top"),
            "width": _num(decls, "width"),
            "height": _num(decls, "height"),
            "z": _num(decls, "z-index"),
            "file": file,
            "bg": url,
        })

    if not layers:
        raise SystemExit("没有可合成的图片层")

    if carrier_has_bg:
        origin_left = min(l["left"] for l in layers)
        origin_top = min(l["top"] for l in layers)
        right = max(l["left"] + l["width"] for l in layers)
        bottom = max(l["top"] + l["height"] for l in layers)
        new_left, new_top = origin_left, origin_top
    else:
        # 容器承载：子层坐标以容器为原点；画布不能小于容器自身
        origin_left = min(0.0, min(l["left"] for l in layers))
        origin_top = min(0.0, min(l["top"] for l in layers))
        right = max(carrier_box["width"], max(l["left"] + l["width"] for l in layers))
        bottom = max(carrier_box["height"], max(l["top"] + l["height"] for l in layers))
        new_left = carrier_box["left"] + origin_left
        new_top = carrier_box["top"] + origin_top

    width = int(round(right - origin_left))
    height = int(round(bottom - origin_top))
    if width <= 0 or height <= 0:
        raise SystemExit("合并尺寸异常")

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for layer in sorted(layers, key=lambda l: l["z"]):
        image = Image.open(layer["file"]).convert("RGBA")
        canvas.alpha_composite(
            image,
            (int(round(layer["left"] - origin_left)), int(round(layer["top"] - origin_top))),
        )

    out_rel = args.out or f"images/merged-{abs(hash(tuple(classes))) % 100000}.png"
    out_path = (html_dir / out_rel).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)

    # ---------- 结构体检 ----------
    warnings: list[str] = []
    html_path = html_dir / args.html_file
    if html_path.exists():
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
        counts: dict[str, int] = {}
        for element in soup.find_all("div"):
            for cls in (element.get("class") or []):
                counts[cls] = counts.get(cls, 0) + 1
        carrier_el = _exact_find(soup, carrier)
        for cls in classes:
            if counts.get(cls, 0) != 1:
                warnings.append(
                    f".{cls} 在页面出现 {counts.get(cls, 0)} 次：改/删该规则会影响其它元素"
                )
            element = _exact_find(soup, cls)
            if element is not None and carrier_el is not None and carrier_el in element.parents:
                warnings.append(
                    f".{cls} 是承载层的嵌套子层（坐标相对上一级），合成偏移需用实测坐标核对"
                )
    else:
        warnings.append(f"未找到 {args.html_file}，跳过结构体检（class 唯一性/嵌套关系未校验）")

    z_max = int(max([carrier_box["z"]] + [l["z"] for l in layers]))
    css = (
        f".{carrier} {{ position: absolute; left: {int(round(new_left))}px; "
        f"top: {int(round(new_top))}px; z-index: {z_max}; width: {width}px; "
        f'height: {height}px; background: url("{out_rel}") no-repeat; }}'
    )

    print(json.dumps({
        "image": out_rel,
        "width": width,
        "height": height,
        "carrier": carrier,
        "carrier_is_container": not carrier_has_bg,
        "css": css,
        "remove": [f".{c}" for c in classes if c != carrier],
        "warnings": warnings,
        "layers": [{k: v for k, v in l.items() if k != "file"} for l in layers],
        "note": "接下来由 AI 决定是否把该 CSS 写回、并删除 remove 里的层与 DOM 节点；改完必须跑 verify_page.py。",
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
