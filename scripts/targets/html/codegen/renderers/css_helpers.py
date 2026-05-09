# -*- coding: utf-8 -*-
"""NodeRenderer 共用的 CSS 生成辅助函数。

抽取这一层的目的：
  - image / text / group 三个 Renderer 历史上各自维护一套完全一致的
    "position / left / top / width / height / opacity / [mix-blend-mode] / z-index"
    样板字符串；任何一个字段改动（格式、顺序、单位）都必须同时修改 3 处。
  - 这是方案 1 陷阱（"同一概念三份实现"）的典型。统一到 `position_css_lines` 后，
    后续新增 Renderer 或调整字段格式只需改一处。

层内并未抽象出更复杂的 CSSGenerator 类，保持函数式：调用方拼接字符串时
仍然控制最终顺序与缩进，避免"为了复用而过度抽象"。
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

# PNG 文件实际像素尺寸缓存：避免 codegen 阶段对同一文件重复 open。
# key = 绝对路径字符串；value = (width, height) 或 None（读取失败）
_png_size_cache: dict[str, tuple[int, int] | None] = {}


def read_png_size(path: Path | str) -> tuple[int, int] | None:
    """读取 PNG 文件的真实像素尺寸（解析 IHDR 头，无需 PIL）。

    用于判断"PNG 像素 == CSS 容器尺寸"时是否可以省略 `background-size`：
    - 完全相等时省略 → 浏览器走默认 `auto`，1:1 像素映射，最大限度保留
      原 PNG 的边缘抗锯齿质量（避免被 100% 100% 触发的二次缩放管道破坏）。
    - 不相等时调用方应保留 `background-size: 100% 100%` 作为兜底拉伸。

    解析失败（文件不存在 / 不是合法 PNG）返回 None；调用方按"不省略"处理。
    """
    key = str(path)
    if key in _png_size_cache:
        return _png_size_cache[key]
    try:
        with open(path, 'rb') as fp:
            head = fp.read(24)
        # PNG 签名 8 字节 + IHDR(长度4 + 'IHDR' + width4 + height4 ...)
        if len(head) >= 24 and head[:8] == b'\x89PNG\r\n\x1a\n' and head[12:16] == b'IHDR':
            w, h = struct.unpack('>II', head[16:24])
            _png_size_cache[key] = (w, h)
            return (w, h)
    except OSError:
        pass
    _png_size_cache[key] = None
    return None


def position_css_lines(
    layer: dict[str, Any],
    *,
    width: int | float | None = None,
    height: int | float | None = None,
    left: int | float | None = None,
    top: int | float | None = None,
    include_blend: bool = True,
    indent: str = '    ',
) -> str:
    """生成图层定位相关的 CSS 行（含末尾换行）。

    覆盖字段（固定顺序）：
      position / left / top / width / height / opacity / [mix-blend-mode] / z-index

    Args:
        layer:         图层字典，需含 left/top/width/height/opacity/z_index；
                       `blend_mode` 为可选，`include_blend=True` 时读取。
        width/height/left/top:
                       显式覆盖对应字段。典型用例：
                         - TextRenderer 用 width=layer['width']+2 防字符裁剪
                         - GroupRenderer 在溢出检测后用扩展后的 width/height
                       传 None 则取 layer 的同名字段。
        include_blend: 是否写入 mix-blend-mode。GroupRenderer 历史上不写该字段。
        indent:        每行前缀缩进（匹配 CSS 块内缩进，默认 4 空格）。

    Returns:
        拼接好的多行字符串，每行以 `\\n` 结尾。
    """
    w = layer['width'] if width is None else width
    h = layer['height'] if height is None else height
    l = layer['left'] if left is None else left
    t = layer['top'] if top is None else top
    lines = [
        f'{indent}position: absolute;\n',
        f'{indent}left: {l}px;\n',
        f'{indent}top: {t}px;\n',
        f'{indent}width: {w}px;\n',
        f'{indent}height: {h}px;\n',
        f'{indent}opacity: {layer["opacity"]};\n',
    ]
    if include_blend:
        lines.append(f'{indent}mix-blend-mode: {layer["blend_mode"]};\n')
    lines.append(f'{indent}z-index: {layer["z_index"]};\n')
    return ''.join(lines)


def semantic_css_class(class_name: str) -> str:
    """从多类字符串中提取首个语义类（CSS 选择器用）。

    class_name 形如 "btn__27 layer-group" / "bg__3 layer"，CSS 选择器只针对
    首个 token（role 类不参与选择）。抽取出来避免 3 处 renderer 重复写
    `class_name.split()[0]`。
    """
    return class_name.split()[0]
