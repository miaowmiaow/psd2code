"""Node IR: Group / Image / Text / Shape with discriminated union."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from .assets import AssetRef
from .effects import EffectSpec
from .styles import Style


class NodeKind(str, Enum):
    GROUP = "group"
    IMAGE = "image"
    TEXT = "text"
    SHAPE = "shape"


class _NodeBase(BaseModel):
    model_config = ConfigDict(frozen=False)

    id: str
    name: str = ""
    style: Style
    effects: List[EffectSpec] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)  # free-form hints (from classifier / heuristics)


class GroupNode(_NodeBase):
    kind: Literal[NodeKind.GROUP] = NodeKind.GROUP
    children: List["Node"] = Field(default_factory=list)
    # When set, the whole group has been flattened into a single image asset
    # (e.g. by background merger / effect bake). Targets should render the
    # merged image and ignore children for pixel output.
    merged_asset: Optional[AssetRef] = None


class ImageNode(_NodeBase):
    kind: Literal[NodeKind.IMAGE] = NodeKind.IMAGE
    asset: AssetRef


class TextNode(_NodeBase):
    kind: Literal[NodeKind.TEXT] = NodeKind.TEXT
    text: str = ""
    # Optional per-run styling for rich text; target may choose to ignore.
    runs: List[dict] = Field(default_factory=list)


class ShapeNode(_NodeBase):
    kind: Literal[NodeKind.SHAPE] = NodeKind.SHAPE
    # MVP: shapes are treated as images (rasterized). Keep placeholder here
    # for future vector export.
    asset: Optional[AssetRef] = None


Node = Annotated[
    Union[GroupNode, ImageNode, TextNode, ShapeNode],
    Field(discriminator="kind"),
]


# forward refs
GroupNode.model_rebuild()
