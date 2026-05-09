"""EffectSpec family: stroke / shadow / glow / overlay."""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from .styles import Color


class _EffectBase(BaseModel):
    model_config = ConfigDict(frozen=False)

    enabled: bool = True
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)


class StrokeSpec(_EffectBase):
    kind: Literal["stroke"] = "stroke"
    size_px: float = Field(ge=0)
    color: Color
    position: Literal["outside", "center", "inside"] = "outside"


class DropShadowSpec(_EffectBase):
    kind: Literal["drop_shadow"] = "drop_shadow"
    color: Color
    distance_px: float = 0
    angle_deg: float = 0
    spread_px: float = 0
    blur_px: float = 0


class InnerShadowSpec(_EffectBase):
    kind: Literal["inner_shadow"] = "inner_shadow"
    color: Color
    distance_px: float = 0
    angle_deg: float = 0
    choke_px: float = 0
    blur_px: float = 0


class OuterGlowSpec(_EffectBase):
    kind: Literal["outer_glow"] = "outer_glow"
    color: Color
    spread_px: float = 0
    blur_px: float = 0


class InnerGlowSpec(_EffectBase):
    kind: Literal["inner_glow"] = "inner_glow"
    color: Color
    choke_px: float = 0
    blur_px: float = 0


class ColorOverlaySpec(_EffectBase):
    kind: Literal["color_overlay"] = "color_overlay"
    color: Color


class GradientStop(BaseModel):
    model_config = ConfigDict(frozen=False)
    position: float = Field(ge=0.0, le=1.0)
    color: Color


class GradientOverlaySpec(_EffectBase):
    kind: Literal["gradient_overlay"] = "gradient_overlay"
    gradient_type: Literal["linear", "radial"] = "linear"
    angle_deg: float = 0
    stops: list[GradientStop] = Field(default_factory=list)


EffectSpec = Union[
    StrokeSpec,
    DropShadowSpec,
    InnerShadowSpec,
    OuterGlowSpec,
    InnerGlowSpec,
    ColorOverlaySpec,
    GradientOverlaySpec,
]
