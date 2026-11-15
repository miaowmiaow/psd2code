"""手工构造「最小合法 PSD」二进制，供 e2e 冒烟测试使用。

为什么不用真实设计稿当 fixture：
- ``.psd`` 体积大、含客户素材，不适合入库；
- 但 **PSD → HTML 的主链路**（psd-tools 读取 → LayerExporter 渲染 → IR →
  codegen → LayoutOptimizer → 落盘）必须能被 CI 覆盖，否则所有测试都只覆盖
  后处理阶段，"改坏了渲染"这类回归只能在本地手工发现。

因此这里按 PSD 文件格式（Adobe Photoshop File Format）拼一个最小但合法的
设计稿：40×30 画布、1 个组 + 3 个像素图层。psd-tools 可以直接 ``open()`` /
``composite()`` / 逐层导出。

图层结构（自底向上）：

    卡片（组，40×30）
      ├── 背景（PixelLayer，40×30 红）
      └── 图标（PixelLayer，10×10 蓝，位于 5,5）
    前景条（PixelLayer，40×6 绿，位于 y=24）

覆盖的代码路径：组渲染、图层 bbox 归一、PNG 导出与内容去重、中文图层名 →
语义类名（``背景`` → ``bg``）、``_naming_report`` 等。

格式要点（写错任何一处 psd-tools 都会解析失败，改动时请保持）：
- 头部 26 字节：``8BPS`` + version=1 + 6 字节保留 + channels + height + width
  + depth + color_mode；
- 「Layer and Mask Information」里的 layer records **自底向上**排列；
- 组用 ``lsct`` 附加信息表示：type=1 打开文件夹（组自身）、type=3 分隔符
  （组结束，名字为 ``</Layer group>``）；
- 每个通道数据用 compression=0（raw），长度 = 2 + w*h。
"""

from __future__ import annotations

import struct
from pathlib import Path

__all__ = ["build_minimal_psd"]

# 画布尺寸（与 fixture 断言强绑定，改动需同步 tests/test_e2e_smoke.py）
CANVAS_W = 40
CANVAS_H = 30


# ---------------------------------------------------------------------------
# 二进制拼装工具
# ---------------------------------------------------------------------------

def _pascal_string(name: str) -> bytes:
    """PSD Pascal 字符串（1 字节长度 + 内容），并按 4 字节对齐。"""
    raw = name.encode("macroman", "replace")[:255]
    data = bytes([len(raw)]) + raw
    pad = (4 - (len(data) % 4)) % 4
    return data + b"\x00" * pad


def _additional_luni(name: str) -> bytes:
    """``luni`` 附加信息：图层名的 UTF-16BE 版本（中文名靠它）。"""
    payload = struct.pack(">I", len(name)) + name.encode("utf-16-be")
    return b"8BIM" + b"luni" + struct.pack(">I", len(payload)) + payload


def _additional_lsct(section_type: int) -> bytes:
    """``lsct`` 附加信息：组 / 分隔符标记。1=打开文件夹，3=分隔符。"""
    payload = struct.pack(">I", section_type)
    return b"8BIM" + b"lsct" + struct.pack(">I", len(payload)) + payload


def _layer_record(
    name: str,
    bbox: tuple[int, int, int, int],
    plane_sizes: list[int],
    section_type: int | None = None,
) -> bytes:
    """单个图层记录（Layer record）。

    ``bbox`` = (top, left, bottom, right)；``plane_sizes`` 是各通道原始字节长度
    （通道 id 依次为 0/1/2/.../-1，最后一个固定为 alpha）。
    """
    top, left, bottom, right = bbox
    n_ch = len(plane_sizes)
    out = struct.pack(">4i", top, left, bottom, right)
    out += struct.pack(">H", n_ch)
    for idx, size in enumerate(plane_sizes):
        channel_id = -1 if idx == n_ch - 1 else idx
        out += struct.pack(">hI", channel_id, 2 + size)  # +2：compression 字段
    out += b"8BIM" + b"norm"
    out += struct.pack(">4B", 255, 0, 0, 0)  # opacity / clipping / flags / filler

    extra = struct.pack(">I", 0)  # layer mask data
    extra += struct.pack(">I", 0)  # blending ranges
    extra += _pascal_string(name) + _additional_luni(name)
    if section_type is not None:
        extra += _additional_lsct(section_type)
    out += struct.pack(">I", len(extra)) + extra
    return out


def _solid_rgba(w: int, h: int, rgb: tuple[int, int, int]) -> list[bytes]:
    """生成一个纯色图层的 4 个通道（R/G/B/A 各为平铺字节）。"""
    return [
        bytes([rgb[0]]) * (w * h),
        bytes([rgb[1]]) * (w * h),
        bytes([rgb[2]]) * (w * h),
        bytes([255]) * (w * h),
    ]


def _write_psd(path: Path, layers: list[tuple]) -> Path:
    """把 ``layers``（自底向上）序列化成 PSD 文件。"""
    records = b""
    channel_data = b""
    for name, bbox, planes, section_type in layers:
        records += _layer_record(name, bbox, [len(p) for p in planes], section_type)
        for plane in planes:
            channel_data += struct.pack(">H", 0) + plane  # compression=0 (raw)

    layer_info = struct.pack(">h", len(layers)) + records + channel_data
    if len(layer_info) % 2:  # layer info 长度需为偶数
        layer_info += b"\x00"
    layer_mask_info = (
        struct.pack(">I", len(layer_info)) + layer_info + struct.pack(">I", 0)
    )

    header = b"8BPS" + struct.pack(">H", 1) + b"\x00" * 6
    header += struct.pack(">HIIHH", 3, CANVAS_H, CANVAS_W, 8, 3)  # RGB / 8bit

    # 合成图（flattened）数据：raw planar
    composite = (
        bytes([220]) * (CANVAS_W * CANVAS_H)
        + bytes([40]) * (CANVAS_W * CANVAS_H)
        + bytes([40]) * (CANVAS_W * CANVAS_H)
    )

    blob = header
    blob += struct.pack(">I", 0)  # color mode data
    blob += struct.pack(">I", 0)  # image resources
    blob += struct.pack(">I", len(layer_mask_info)) + layer_mask_info
    blob += struct.pack(">H", 0) + composite

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    return path


def build_minimal_psd(path: str | Path) -> Path:
    """写出一份 40×30、含 1 组 3 图层的最小 PSD，返回文件路径。"""
    group_bbox = (0, 0, CANVAS_H, CANVAS_W)
    layers = [
        # 自底向上：先组结束分隔符，再组内子层，最后组开始（打开文件夹）
        ("</Layer group>", (0, 0, 0, 0), [], 3),
        ("背景", group_bbox, _solid_rgba(CANVAS_W, CANVAS_H, (200, 30, 30)), None),
        ("图标", (5, 5, 15, 15), _solid_rgba(10, 10, (30, 30, 200)), None),
        ("卡片", group_bbox, [], 1),
        ("前景条", (24, 0, 30, CANVAS_W), _solid_rgba(CANVAS_W, 6, (30, 200, 30)), None),
    ]
    return _write_psd(path, layers)
