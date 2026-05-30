"""IR (Intermediate Representation) for psd2code.

All data classes are pydantic models with validation. The IR is the single
contract between ``core`` (PSD parsing) and ``targets`` (code generation).

Current status (P3 milestone)
-----------------------------
The **structural** fields (id / name / kind / bbox / children / opacity) are
fully populated by :func:`core.psd.parser.parse_psd_to_ir`.  However, the
**rich styling** fields defined in :mod:`.styles` and :mod:`.effects` are
**NOT yet populated** by the parser:

- ``Style.font``, ``Style.background_color``, ``Style.border_radius_px``,
  ``Style.z_index`` — always default / ``None``
- ``Node.effects`` — always empty ``[]``

Instead, the full legacy dict for each PSD layer is preserved verbatim in
``node.meta["legacy"]``, and downstream targets recover it via
:func:`.adapters.to_legacy_layers`.  This means the typed IR currently acts
as a **structural envelope** rather than a complete data contract.

P5 migration plan
~~~~~~~~~~~~~~~~~
A future pass (P5+) will:

1. Lift ``font``, ``background_color``, ``effects``, ``z_index``, etc. from
   the legacy dict into the corresponding typed IR fields.
2. Update ``HtmlCodegenStage`` (and React / Vue stages) to consume typed IR
   directly instead of falling back through ``to_legacy_layers()``.
3. Remove (or deprecate) ``node.meta["legacy"]`` and ``to_legacy_layers()``.

Until that migration is complete, **treat ``to_legacy_layers()`` as the
canonical data source** for downstream code generation.
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
