"""剥离开发者无关的元数据，并旁路生成 layer_map.json。

背景
----
``index.html`` 中每个图层 div 都带 ``data-name="形状 17"`` / ``data-type="image"`` /
``id="layer-5"`` 或 ``id="group-26"`` 三类元数据属性。这些属性：
  * 对最终接管页面的开发者来说**纯粹是噪音**（开发者会自定义 id / 不关心 PSD 原名）；
  * 对 LayoutOptimizer 内部 transformer 是**运行时必需**的（``dom_restructure`` /
    ``flex_applier`` / ``sibling_group_detector`` 会按 ``data-name`` / ``data-type``
    判断）；
  * 对 AI 助手排查 "为什么某图层位置不对 / 哪个图层对应原 PSD 的什么名" 是高价值
    定位锚点。

策略
----
* ``index.html``（未优化版）保留所有属性不动，作为诊断参照。
* ``index_optimized.html``（最终交付物）剥离 ``data-name`` / ``data-type`` /
  ``id="layer-*"`` / ``id="group-*"`` 四类属性。
* 旁路写出 ``layer_map.json``，提供**双向**索引：
  - ``by_class[<首类名>]`` → ``{layer_id, name, type, all_classes}``
  - ``by_layer_id[<layer-N | group-N>]`` → ``{class, name, type, all_classes}``
  AI 排查时既可以用 "类名" 也可以用 "layer-id" 任一锚点反查。

注意：必须在 LayoutOptimizer 跑完之后、HTML 落盘之前执行；不要在生成阶段就不写
属性，否则会破坏 LayoutOptimizer 的运行时判断。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Tuple

from bs4 import BeautifulSoup


# 要剥离的 HTML 属性
# - data-name / data-type：图层元数据（PSD 原名 / image|text|shape|group），
#   会同时收集到 layer_map.json 反查表里
# - data-virtual / data-bg-absorbed / data-i18n-key：LayoutOptimizer 流水线
#   内部各 transformer 协作用的"工艺标记"（虚拟 wrapper 类型 / 背景吸收标记 /
#   文本 i18n key），优化结束后对开发者无价值，不进 layer_map（开发者不需要反查）
_METADATA_ATTRS = ("data-name", "data-type")
_INTERNAL_ATTRS = ("data-virtual", "data-bg-absorbed", "data-i18n-key")
_STRIP_ATTRS = _METADATA_ATTRS + _INTERNAL_ATTRS
# id 形如 layer-12 / group-26 才剥（其他自定义 id 如 #canvas 保留）
_LAYER_ID_RE = re.compile(r"^(?:layer|group)-\d+$")


def strip_and_collect(html_text: str) -> Tuple[str, Dict[str, Any]]:
    """剥离 dev metadata，并收集 layer_map。

    Args:
        html_text: 优化版 HTML 文本（LayoutOptimizer 之后、落盘之前）。

    Returns:
        (cleaned_html, layer_map_dict)
        cleaned_html: 剥离 data-name/data-type/id="layer-*" 后的 HTML。
        layer_map_dict: 适合 ``json.dump`` 的字典，含 ``by_class`` / ``by_layer_id``
                        两个反向索引。
    """
    soup = BeautifulSoup(html_text, "html.parser")

    by_class: Dict[str, Dict[str, Any]] = {}
    by_layer_id: Dict[str, Dict[str, Any]] = {}

    for el in soup.find_all(True):
        # 收集元数据（属性可能因 LayoutOptimizer 重组已不全，按存在性逐个取）
        layer_id = el.get("id")
        if layer_id and not _LAYER_ID_RE.match(layer_id):
            # 自定义 id（非 "layer-N" 格式），不剥也不收
            layer_id = None

        name = el.get("data-name")
        dtype = el.get("data-type")

        # 至少要有 layer_id 或 (name + class) 才记录到 map
        classes = el.get("class") or []
        first_cls = classes[0] if classes else None

        if layer_id or name or dtype:
            entry: Dict[str, Any] = {}
            if layer_id:
                entry["layer_id"] = layer_id
            if name:
                entry["name"] = name
            if dtype:
                entry["type"] = dtype
            if classes:
                entry["class"] = first_cls
                if len(classes) > 1:
                    entry["all_classes"] = list(classes)

            # 索引 by_class（首类名）
            if first_cls and first_cls not in by_class:
                by_class[first_cls] = {
                    k: v for k, v in entry.items() if k != "class"
                }
            # 索引 by_layer_id
            if layer_id and layer_id not in by_layer_id:
                by_layer_id[layer_id] = {
                    k: v for k, v in entry.items() if k != "layer_id"
                }

        # 实际剥离属性
        for attr in _STRIP_ATTRS:
            if attr in el.attrs:
                del el.attrs[attr]
        if layer_id:
            del el.attrs["id"]

    layer_map = {
        "version": 1,
        "description": (
            "PSD 图层元数据反查表。AI 排查时按首类名（by_class）或 layer_id "
            "（by_layer_id）双向反查 PSD 原始图层名 / 类型。"
        ),
        "by_class": by_class,
        "by_layer_id": by_layer_id,
    }

    return str(soup), layer_map


def write_layer_map(layer_map: Dict[str, Any], out_path: Path) -> None:
    """把 layer_map 落盘到 ``layer_map.json``（utf-8, indent=2, ensure_ascii=False）。"""
    out_path.write_text(
        json.dumps(layer_map, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def strip_optimized_html_inplace(html_path: Path) -> Path:
    """便捷入口：读取优化版 HTML，原地剥离 + 同目录写 layer_map.json。

    Returns:
        layer_map.json 的 Path。
    """
    html_text = html_path.read_text(encoding="utf-8")
    cleaned, layer_map = strip_and_collect(html_text)
    html_path.write_text(cleaned, encoding="utf-8")
    map_path = html_path.parent / "layer_map.json"
    write_layer_map(layer_map, map_path)
    return map_path


__all__ = ["strip_and_collect", "write_layer_map", "strip_optimized_html_inplace"]
