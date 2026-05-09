"""IR (Intermediate Representation) for psd2code.

All data classes are pydantic models with validation. The IR is the single
contract between ``core`` (PSD parsing) and ``targets`` (code generation).
"""

from .assets import AssetRef
from .effects import (
    EffectSpec,
    StrokeSpec,
    DropShadowSpec,
    InnerShadowSpec,
    OuterGlowSpec,
    InnerGlowSpec,
    ColorOverlaySpec,
    GradientOverlaySpec,
)
from .styles import BBox, FontStyle, Color, Style
from .nodes import Node, GroupNode, ImageNode, TextNode, ShapeNode, NodeKind
from .document import Document
from .adapters import to_legacy_layers

__all__ = [
    "AssetRef",
    "BBox",
    "Color",
    "FontStyle",
    "Style",
    "EffectSpec",
    "StrokeSpec",
    "DropShadowSpec",
    "InnerShadowSpec",
    "OuterGlowSpec",
    "InnerGlowSpec",
    "ColorOverlaySpec",
    "GradientOverlaySpec",
    "Node",
    "GroupNode",
    "ImageNode",
    "TextNode",
    "ShapeNode",
    "NodeKind",
    "Document",
    "to_legacy_layers",
]
