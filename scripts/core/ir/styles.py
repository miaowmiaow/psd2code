"""Style primitives: BBox, Color, FontStyle, Style."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BBox(BaseModel):
    """Axis-aligned bounding box in PSD pixel space (top-left origin)."""

    model_config = ConfigDict(frozen=False)

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @field_validator("right")
    @classmethod
    def _check_right(cls, v: int, info) -> int:  # noqa: ANN001
        left = info.data.get("left")
        if left is not None and v < left:
            raise ValueError(f"right ({v}) must be >= left ({left})")
        return v

    @field_validator("bottom")
    @classmethod
    def _check_bottom(cls, v: int, info) -> int:  # noqa: ANN001
        top = info.data.get("top")
        if top is not None and v < top:
            raise ValueError(f"bottom ({v}) must be >= top ({top})")
        return v


class Color(BaseModel):
    """RGBA color, each channel 0-255 except alpha 0-1."""

    model_config = ConfigDict(frozen=False)

    r: int = Field(ge=0, le=255)
    g: int = Field(ge=0, le=255)
    b: int = Field(ge=0, le=255)
    a: float = Field(default=1.0, ge=0.0, le=1.0)

    def to_css(self) -> str:
        if self.a >= 0.999:
            return f"rgb({self.r}, {self.g}, {self.b})"
        return f"rgba({self.r}, {self.g}, {self.b}, {self.a:g})"


class FontStyle(BaseModel):
    """Typography-related style."""

    model_config = ConfigDict(frozen=False)

    family: Optional[str] = None
    size_px: Optional[float] = None
    weight: Optional[int] = None  # 100-900
    italic: bool = False
    line_height_px: Optional[float] = None
    letter_spacing_px: Optional[float] = None
    align: Optional[Literal["left", "center", "right", "justify"]] = None
    color: Optional[Color] = None


class Style(BaseModel):
    """Presentational style attached to a Node.

    Mirrors CSS-ish properties; ``extra`` is a free-form dict for target-specific hints.
    """

    model_config = ConfigDict(frozen=False)

    bbox: BBox
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    visible: bool = True
    border_radius_px: Optional[float] = None
    z_index: Optional[int] = None
    background_color: Optional[Color] = None
    font: Optional[FontStyle] = None
    extra: dict = Field(default_factory=dict)
