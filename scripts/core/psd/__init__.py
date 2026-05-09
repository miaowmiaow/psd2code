"""PSD parsing: loader / parser / classifier / text_extractor.

P3: :func:`parse_psd_to_ir` converts a PSD file into an IR :class:`Document`
while reusing the mature :class:`LayerExporter` for asset extraction.
"""

from .parser import parse_psd_to_ir

__all__ = ["parse_psd_to_ir"]
