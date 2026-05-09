"""HtmlTarget: assembles the html-specific pipeline."""

from __future__ import annotations

from framework import Pipeline, PipelineContext
from targets.base import Target
from targets.registry import register

from .pipeline import build_html_pipeline


@register("html")
class HtmlTarget(Target):
    """Default target. Produces index.html + images/ compatible with the
    legacy psd2html output.
    """

    def build_pipeline(self, ctx: PipelineContext) -> Pipeline:
        return build_html_pipeline(ctx)
