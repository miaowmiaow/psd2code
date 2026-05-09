"""PipelineContext: carries shared state across all pipeline stages.

Intentionally permissive: stages may attach arbitrary intermediate artifacts
under ``ctx.artifacts[<key>]`` without strict typing, while well-known fields
(psd, ir document, output_dir, ...) are first-class attributes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from .hooks import NullHook, PipelineHook

if TYPE_CHECKING:  # avoid hard dep at import time
    from psd_tools import PSDImage  # type: ignore
    from scripts.core.ir.document import Document


@dataclass
class PipelineContext:
    # ---- inputs ----
    psd_path: Path
    # ``output_dir`` 是**当前 target 产物子目录**，例如 ``output/<psd>/html/``。
    # ``project_root`` 是**项目总根**（所有 target 共享），例如 ``output/<psd>/``。
    # 在 LoadPsdStage 运行前，``output_dir`` 被 CLI 解释为 "--output 指定的基础目录"
    # （也可能为 None，表示使用配置的默认 base）；运行后会被替换为真正的 target 子目录。
    # 约定：LoadPsdStage 负责把 output_dir 推进到 <base>/<psd_stem>/<subdir>/，并同时
    # 回填 ``project_root`` = output_dir.parent。
    output_dir: Optional[Path] = None
    project_root: Optional[Path] = None
    target_name: str = "html"
    verbose: bool = False

    # ---- core artifacts ----
    psd: Optional["PSDImage"] = None
    ir: Optional["Document"] = None

    # ---- target-specific & free-form artifacts ----
    artifacts: dict[str, Any] = field(default_factory=dict)

    # ---- observer hook ----
    hook: PipelineHook = field(default_factory=NullHook)

    # ---- helpers ----
    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"[psd2code] {msg}")

    def set(self, key: str, value: Any) -> None:
        self.artifacts[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.artifacts.get(key, default)
