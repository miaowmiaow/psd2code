"""Stage: abstract single-purpose unit in a Pipeline."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .context import PipelineContext


class Stage(ABC):
    """A pipeline stage. Subclasses implement :meth:`run`.

    Conventions:
    - A stage MUST be idempotent w.r.t. the inputs it relies on.
    - A stage SHOULD only read/write well-defined keys of ``ctx``.
    - A stage MAY skip itself by returning ``ctx`` unchanged.
    """

    name: str = ""

    def __init__(self, name: str | None = None) -> None:
        if name is not None:
            self.name = name
        elif not self.name:
            self.name = self.__class__.__name__

    @abstractmethod
    def run(self, ctx: PipelineContext) -> PipelineContext:  # pragma: no cover - abstract
        raise NotImplementedError
