"""Global target registry.

Usage::

    from scripts.targets.registry import register

    @register("html")
    class HtmlTarget(Target):
        ...
"""

from __future__ import annotations

from typing import Callable, Type

from .base import Target

_REGISTRY: dict[str, Type[Target]] = {}


def register(name: str) -> Callable[[Type[Target]], Type[Target]]:
    """Class decorator to register a Target under ``name``."""

    def _wrap(cls: Type[Target]) -> Type[Target]:
        key = name.strip().lower()
        if not key:
            raise ValueError("Target name must be non-empty")
        if key in _REGISTRY and _REGISTRY[key] is not cls:
            raise ValueError(f"Target '{key}' already registered to {_REGISTRY[key]!r}")
        cls.name = key
        _REGISTRY[key] = cls
        return cls

    return _wrap


def get(name: str) -> Type[Target] | None:
    return _REGISTRY.get(name.strip().lower())


def list_targets() -> list[str]:
    return sorted(_REGISTRY.keys())
