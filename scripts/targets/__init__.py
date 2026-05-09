"""Target plugin system.

Each output format (html/vue/react/...) is a Target subclass registered
via ``@register("<name>")``. The CLI chooses target by ``--target``.
"""

from . import registry
from .base import Target

__all__ = ["Target", "registry"]
