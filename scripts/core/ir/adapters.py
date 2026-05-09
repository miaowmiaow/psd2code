"""IR → legacy adapters.

Bridge the new pydantic IR back to the dict-based format consumed by the
existing HTML code generator. This allows the P3 pipeline to treat IR as
the authoritative representation while keeping byte-identical output.
"""

from __future__ import annotations

from typing import Any

from .document import Document
from .nodes import GroupNode, Node


def to_legacy_layers(doc: Document) -> list[dict[str, Any]]:
    """Return the legacy ``list[dict]`` layer tree.

    If the document was constructed by ``core.psd.parser.parse_psd_to_ir``
    the original legacy tree is preserved under ``doc.root.meta['legacy_roots']``
    and returned as-is (zero information loss). Otherwise, we reconstruct a
    best-effort legacy tree from IR fields (used by synthetic/non-PSD inputs).
    """
    legacy_roots = (doc.root.meta or {}).get("legacy_roots")
    if isinstance(legacy_roots, list):
        return legacy_roots

    # Fallback: synthesize from IR nodes.
    return [_legacy_from_node(c) for c in doc.root.children]


def _legacy_from_node(node: Node) -> dict[str, Any]:
    legacy = (node.meta or {}).get("legacy")
    if isinstance(legacy, dict):
        return legacy

    bbox = node.style.bbox
    base: dict[str, Any] = {
        "id": node.id,
        "name": node.name,
        "left": bbox.left,
        "top": bbox.top,
        "width": bbox.width,
        "height": bbox.height,
        "opacity": node.style.opacity,
    }
    if isinstance(node, GroupNode):
        base["type"] = "group"
        base["children"] = [_legacy_from_node(c) for c in node.children]
    else:
        # node.kind is an enum value in pydantic v2 via discriminator.
        base["type"] = getattr(node.kind, "value", str(node.kind))
    return base
