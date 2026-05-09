"""Vue target: PSD/IR -> Vue (Vite + SFC) project.

Importing :mod:`target` registers VueTarget in the global target registry.
"""

# Importing ``target`` registers VueTarget in the global registry.
from . import target  # noqa: F401

__all__ = ["target"]
