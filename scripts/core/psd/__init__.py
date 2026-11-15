"""PSD parsing: loader / parser / classifier / text_extractor.

P3: :func:`parse_psd_to_ir` converts a PSD file into an IR :class:`Document`
while reusing the mature :class:`LayerExporter` for asset extraction.

导入约定（2026-11）：本包 **不再** 在 ``__init__`` 里急切导入 ``parser``。

原因：``core.extract.layer_exporter`` 需要 ``core.psd.text_extractor``，
而导入 ``core.psd.text_extractor`` 会先执行本包的 ``__init__``。若此处急切
``from .parser import parse_psd_to_ir``，就会形成

    layer_exporter → core.psd.__init__ → parser → layer_exporter（半初始化）

的循环导入，导致 ``from core.extract.layer_exporter import LayerExporter``
在"先导入 layer_exporter"的场景（单测、脚本、其他 target）直接 ImportError。

改为惰性导出：``from core.psd import parse_psd_to_ir`` 仍然可用，但只在使用时
才真正加载 ``parser``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查器 / IDE
    from .parser import parse_psd_to_ir as parse_psd_to_ir

__all__ = ["parse_psd_to_ir"]


def __getattr__(name: str) -> Any:  # noqa: ANN401 - 模块级惰性代理
    if name == "parse_psd_to_ir":
        from .parser import parse_psd_to_ir

        # 缓存到模块字典，后续访问不再走 __getattr__
        globals()[name] = parse_psd_to_ir
        return parse_psd_to_ir
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
