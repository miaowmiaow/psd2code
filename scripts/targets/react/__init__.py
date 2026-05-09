"""React target: PSD/IR -> React (Vite + JSX + CSS Module) project.

Importing :mod:`target` registers ReactTarget in the global target registry.
"""

# Importing ``target`` registers ReactTarget in the global registry.
from . import target  # noqa: F401

__all__ = ["target"]
