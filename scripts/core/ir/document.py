"""Document: the root of the IR tree."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .assets import AssetRef
from .nodes import GroupNode, Node
from .styles import BBox


class Document(BaseModel):
    """Root IR document.

    A Document represents a full PSD canvas. Its ``root`` is a GroupNode that
    contains the full layer tree after normalization.
    """

    model_config = ConfigDict(frozen=False)

    # canvas
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    canvas: Optional[BBox] = None

    # source metadata
    source_psd: Optional[str] = None
    title: Optional[str] = None

    # layer tree
    root: GroupNode

    # resource catalog (deduplicated)
    assets: List[AssetRef] = Field(default_factory=list)

    # free-form metadata (debug, stats, ...)
    meta: dict = Field(default_factory=dict)

    # ---- convenience ----
    def iter_nodes(self):
        """Pre-order traversal over all nodes (root first)."""
        yield self.root
        stack: list[Node] = list(self.root.children)
        while stack:
            n = stack.pop()
            yield n
            if isinstance(n, GroupNode):
                stack.extend(reversed(n.children))
