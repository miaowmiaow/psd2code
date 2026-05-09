"""AssetRef: reference to an extracted binary asset (image, etc.)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AssetRef(BaseModel):
    """A reference to an exported asset on disk.

    ``src`` is the URL/path relative to the output root that a target
    (e.g. html) can use directly.
    """

    model_config = ConfigDict(frozen=False)

    kind: Literal["image", "font", "video", "other"] = "image"
    src: str  # relative path, e.g. "images/bg.png"
    absolute_path: Optional[Path] = None
    width: Optional[int] = None
    height: Optional[int] = None
    format: Optional[str] = None  # "png" | "jpg" | "webp" ...
    sha1: Optional[str] = None
    extra: dict = Field(default_factory=dict)
