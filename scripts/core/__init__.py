"""Core (frontend of the compiler): PSD -> IR.

This package is target-agnostic. It contains:

- ``ir``: pydantic IR data classes (Document / Node / Style / Effect / Asset)
- ``psd``: PSD file loading, parsing, classification, text extraction
- ``render``: pixel rendering (layer renderer + effect renderers)
- ``extract``: resource extraction (exporter, group merger, dedup, ...)
"""
