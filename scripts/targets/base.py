"""Target: abstract base class for a concrete output format."""

from __future__ import annotations

from abc import ABC, abstractmethod

from framework import Pipeline, PipelineContext


class Target(ABC):
    """A concrete output target (e.g. html / vue / react).

    Subclasses assemble a :class:`Pipeline` of :class:`Stage` objects by
    implementing :meth:`build_pipeline`.
    """

    name: str = ""

    @abstractmethod
    def build_pipeline(self, ctx: PipelineContext) -> Pipeline:  # pragma: no cover
        raise NotImplementedError

    def run(self, ctx: PipelineContext) -> PipelineContext:
        pipeline = self.build_pipeline(ctx)
        return pipeline.run(ctx)
