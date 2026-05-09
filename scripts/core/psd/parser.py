"""PSD → IR parser (P3, minimal adapter).

This first-cut parser reuses the mature :class:`LayerExporter` from
``core.extract`` to produce the legacy ``list[dict]`` layer tree, then wraps
it into a pydantic :class:`Document` IR.

- IR captures structural info (id / name / kind / bbox / children).
- The full legacy dict for each node is preserved in ``node.meta['legacy']``,
  so any target can recover a fully-faithful representation via
  :func:`core.ir.adapters.to_legacy_layers` — guaranteeing zero regression
  during the migration.

Future iterations (P5+) will lift more properties (style, effects, asset
refs) into typed IR fields and drop the ``legacy`` escape hatch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from psd_tools import PSDImage  # type: ignore[import-untyped]

from core.ir import (
    AssetRef,
    BBox,
    Document,
    GroupNode,
    ImageNode,
    Node,
    NodeKind,
    ShapeNode,
    Style,
    TextNode,
)
from core.extract.layer_exporter import LayerExporter


# ---------------------------------------------------------------------------
# legacy-dict -> IR node
# ---------------------------------------------------------------------------

def _bbox_from_legacy(d: dict[str, Any]) -> BBox:
    left = int(d.get("left", 0))
    top = int(d.get("top", 0))
    width = int(d.get("width", 0))
    height = int(d.get("height", 0))
    right = left + max(0, width)
    bottom = top + max(0, height)
    return BBox(left=left, top=top, right=right, bottom=bottom)


def _kind_from_legacy(d: dict[str, Any]) -> str:
    t = str(d.get("type", "")).lower()
    if t == "group":
        return NodeKind.GROUP.value
    if t == "text":
        return NodeKind.TEXT.value
    if t == "shape":
        return NodeKind.SHAPE.value
    return NodeKind.IMAGE.value  # default: image


_LEGACY_ID_COUNTER: dict[str, int] = {"n": 0}


def _next_auto_id() -> str:
    _LEGACY_ID_COUNTER["n"] += 1
    return f"node-{_LEGACY_ID_COUNTER['n']}"


def _node_from_legacy(d: dict[str, Any]) -> Node:
    kind = _kind_from_legacy(d)
    bbox = _bbox_from_legacy(d)
    style = Style(bbox=bbox, opacity=float(d.get("opacity", 1.0)))
    node_id = str(d.get("id") or _next_auto_id())
    name = str(d.get("name", ""))
    meta = {"legacy": d}

    if kind == NodeKind.GROUP.value:
        children = [_node_from_legacy(c) for c in d.get("children", [])]
        return GroupNode(id=node_id, name=name, style=style, children=children, meta=meta)

    if kind == NodeKind.TEXT.value:
        return TextNode(
            id=node_id,
            name=name,
            style=style,
            text=str(d.get("text", "")),
            meta=meta,
        )

    if kind == NodeKind.SHAPE.value:
        return ShapeNode(id=node_id, name=name, style=style, meta=meta)

    # image (default): try to attach AssetRef if src present
    src = d.get("src") or d.get("image") or d.get("path")
    if src:
        asset = AssetRef(
            kind="image",
            src=str(src),
            width=bbox.width or None,
            height=bbox.height or None,
        )
    else:
        asset = AssetRef(kind="image", src="")
    return ImageNode(id=node_id, name=name, style=style, asset=asset, meta=meta)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def parse_psd_to_ir(
    psd_path: str | Path,
    output_dir: str | Path,
    psd: Optional[PSDImage] = None,
    smart_merge: bool = True,
) -> tuple[Document, LayerExporter, list[dict[str, Any]]]:
    """Parse a PSD file into an IR :class:`Document`.

    This performs the full layer export (writes images to ``output_dir/images``)
    and returns both the IR and the underlying legacy artifacts so downstream
    stages can reuse them without re-parsing the PSD.

    Args:
        smart_merge: 是否启用 PSD 图层级「智能合图」（装饰组合成单 PNG +
            画布底部连续背景合并）。False 时所有组逐层导出、底部背景不合并。
            透传给 :class:`LayerExporter`。

    Returns:
        (document, layer_exporter, legacy_layers_tree)
    """
    psd_path = Path(psd_path)
    output_dir = Path(output_dir)
    if psd is None:
        psd = PSDImage.open(str(psd_path))  # type: ignore[misc]

    exporter = LayerExporter(psd, output_dir, smart_merge=smart_merge)
    legacy_tree = exporter.export_layers(psd)
    exporter.verify_export()

    # Reset the counter so repeated calls in a single process still produce
    # deterministic node-id sequences.
    _LEGACY_ID_COUNTER["n"] = 0

    # Wrap top-level legacy nodes into a virtual root group covering the full canvas.
    children: list[Node] = [_node_from_legacy(d) for d in legacy_tree]
    root = GroupNode(
        id="root",
        name=psd_path.stem,
        style=Style(bbox=BBox(left=0, top=0, right=int(psd.width), bottom=int(psd.height))),
        children=children,
        meta={"legacy_roots": legacy_tree},
    )
    doc = Document(
        width=int(psd.width),
        height=int(psd.height),
        root=root,
        source_psd=str(psd_path),
        title=psd_path.stem,
        meta={
            "exported_count": exporter.exported_count,
            "skipped_count": exporter.skipped_count,
        },
    )
    return doc, exporter, legacy_tree
