"""
Background Compose — 多层 PNG 离线合成为单图（共享底层）

定位
====
本模块提供"把若干已落盘的 PNG 按 (position, size) 合成为一张 PNG"的纯
工具能力。它**不**关心 CSS 文本、不关心 DOM、不关心调用上下文。

两层调用方：

1. **DOMRestructure**（主路径）：在 ``_merge_bg_candidates_into_container_css``
   决定写出多 url 背景之前，调本模块尝试合成；成功就直接写单层 url。
   这样 CSS 一开始就是单 url，CssDedup/CssPretty 都不会再看到多层背景。

2. **background_flatten**（兜底）：扫描已落盘 CSS 文本里仍存在的多 url
   背景规则（少数绕过 DOMRestructure 的场景），调本模块合成。

合成结果落盘命名为 ``flat-<md5[:8]>.png``，与 hash 文件命名风格一致；同
内容多次合成会复用同一个文件（不重复写盘）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class ComposeLayer:
    """单层背景输入（坐标 / 尺寸 单位均为 px，坐标可为负或大于画布）"""
    png_path: Path          # 物理 PNG 文件
    pos_x: int              # background-position X
    pos_y: int              # background-position Y
    size_w: int             # background-size 宽（即"绘制宽"）
    size_h: int             # background-size 高


@dataclass
class ComposeResult:
    """合成产物"""
    out_path: Path          # 落盘后的 PNG 路径（images_dir / flat-xxxx.png）
    rel_url: str            # 形如 'images/flat-xxxx.png'，可直接拼到 url("...")
    canvas_w: int           # 合成图宽
    canvas_h: int           # 合成图高
    origin_x: int           # 合成图在原坐标系下左上角的 X（= min(layer.pos_x)）
    origin_y: int           # 合成图在原坐标系下左上角的 Y（= min(layer.pos_y)）


# 上层有些场景需要明确意图（例如 DOMRestructure 想统计 bytes 节省）。
# 函数返回前并不读取统计数据，本模块只输出 ComposeResult，节省统计在调用方做。
def compose_layers(
    layers: List[ComposeLayer],
    images_dir: Path,
    *,
    max_canvas_px: int = 8192,
) -> Optional[ComposeResult]:
    """把多层 PNG 按位置 / 尺寸合成为一张 PNG。

    layers 顺序约定：**第一层 = 视觉最底层、最后一层 = 视觉最顶层**（与
    PIL alpha_composite 自然语义一致；与 CSS ``background-image: a, b, c``
    的"a 在最上"约定**相反**。调用方需自行 reverse 后再传入）。

    返回 None 的情况：
      - layers 为空或仅 1 层（无意义）
      - 某 PNG 不存在或无法读取
      - 计算出的画布尺寸 ≤ 0 或超过 ``max_canvas_px``
      - PIL 未安装

    成功时的副作用：把合成结果写入 ``images_dir / f"flat-{md5}.png"``；
    若同名文件已存在则不重写。
    """
    if len(layers) < 2:
        return None
    images_dir = Path(images_dir)
    if not images_dir.is_dir():
        return None

    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return None

    # 计算合成画布范围
    min_x = min(L.pos_x for L in layers)
    min_y = min(L.pos_y for L in layers)
    max_x = max(L.pos_x + L.size_w for L in layers)
    max_y = max(L.pos_y + L.size_h for L in layers)
    canvas_w = max_x - min_x
    canvas_h = max_y - min_y
    if canvas_w <= 0 or canvas_h <= 0:
        return None
    if canvas_w > max_canvas_px or canvas_h > max_canvas_px:
        return None

    canvas = Image.new('RGBA', (canvas_w, canvas_h), (0, 0, 0, 0))
    for L in layers:
        if not L.png_path.exists():
            return None
        try:
            im = Image.open(L.png_path).convert('RGBA')
        except Exception:
            return None
        if im.size != (L.size_w, L.size_h):
            im = im.resize((L.size_w, L.size_h), Image.LANCZOS)
        canvas.alpha_composite(im, (L.pos_x - min_x, L.pos_y - min_y))

    digest = hashlib.md5(canvas.tobytes()).hexdigest()[:8]
    out_name = f'flat-{digest}.png'
    out_path = images_dir / out_name
    if not out_path.exists():
        canvas.save(out_path, optimize=True)

    return ComposeResult(
        out_path=out_path,
        rel_url=f'images/{out_name}',
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        origin_x=min_x,
        origin_y=min_y,
    )


def parse_url_to_local_png(url_value: str, html_dir: Path) -> Optional[Path]:
    """从 ``url("images/xxx.png")`` 之类的 CSS 值解析出本地 PNG 物理路径。

    仅处理：
      - 相对路径 + .png 后缀
      - 不包含 data: / http(s): / ftp: 协议
      - 不含 ``..`` 上跳

    Returns:
        Path 或 None；返回 None 表示不是合规的本地 PNG。
    """
    import re
    m = re.search(
        r"""url\(\s*(?:"([^"]+)"|'([^']+)'|([^)]+?))\s*\)""", url_value
    )
    if not m:
        return None
    rel = (m.group(1) or m.group(2) or m.group(3) or '').strip()
    if not rel.lower().endswith('.png'):
        return None
    if '://' in rel or rel.startswith('data:'):
        return None
    if '..' in rel.split('/'):
        return None
    p = html_dir / rel
    return p if p.exists() else None


def estimate_bytes_saved(
    src_paths: List[Path], result: ComposeResult,
) -> int:
    """用于统计：原料文件总字节 - 合成图字节（正数 = 节省）"""
    old = sum(p.stat().st_size for p in src_paths if p.exists())
    new = result.out_path.stat().st_size if result.out_path.exists() else 0
    return old - new
