#!/usr/bin/env python3
"""psd2code 统一 CLI 入口

用法：
    python3 psd_to_code.py <psd_path> [--target html|vue|react] [--output <dir>]

默认 target = html，与原 psd2html 输出一致。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 禁止生成 __pycache__/*.pyc（保持工作区干净）
sys.dont_write_bytecode = True

# 确保可以以脚本方式直接运行：把 scripts/ 加入 sys.path，所有子包都以顶级包形式导入
SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# 触发 target 自注册
from targets import registry as _registry  # noqa: E402,F401
from targets import html as _html_target  # noqa: E402,F401  (register HtmlTarget)
from targets import react as _react_target  # noqa: E402,F401  (register ReactTarget)
from targets import vue as _vue_target  # noqa: E402,F401  (register VueTarget)
from framework.context import PipelineContext  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="psd_to_code",
        description="Convert PSD to frontend code (HTML / Vue / React ...)",
    )
    parser.add_argument("psd_path", type=str, help="Path to .psd file")
    parser.add_argument(
        "--target",
        type=str,
        default="html",
        help="Output target: html (default), vue, react, ...",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output directory (default: configured per-target output base)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging",
    )
    parser.add_argument(
        "--no-css-pretty",
        action="store_true",
        help="Disable CssPretty (DOM-order sort + property grouping + multi-line merge groups). "
             "Falls back to dict_to_css mechanical rendering. Useful for CI baselines.",
    )
    parser.add_argument(
        "--css-style",
        type=str,
        choices=("compact", "expanded"),
        default="compact",
        help=(
            "CSS output style for style_optimized.css. "
            "'compact' (default) ≈ figma-grade density: no property grouping, "
            "no provenance comments, single-line section markers, ≤6-prop rules inline. "
            "'expanded' = dev-friendly verbose layout (PSD provenance comment per rule, "
            "section headers, property grouping with blank-line dividers)."
        ),
    )
    parser.add_argument(
        "--no-smart-merge",
        action="store_true",
        help=(
            "Disable LayoutOptimizer-stage multi-url background inline composition: "
            "(1) DOMRestructure multi-url background inline composition; "
            "(2) background_flatten textual fallback. "
            "These only collapse multiple background-image url() layers into "
            "a single PNG on the SAME container (no DOM child removal), so "
            "the side effect is small and they remain ON by default. "
            "The parsing stage (LayerExporter) is already pure (1 PSD layer = "
            "1 layer_info / 1 PNG, no decorative merging at all), so this flag "
            "only affects HTML post-processing. Useful for 1:1 PSD-layer debugging."
        ),
    )
    parser.add_argument(
        "--enable-image-layer-flatten",
        action="store_true",
        help=(
            "Enable LayoutOptimizer step 1.2 ImageLayerFlatten "
            "(container + image-children composition into a single PNG, "
            "deleting all child DOM nodes). "
            "DISABLED by default since 2026-05-27 because it merges DOM "
            "nodes too aggressively (e.g. raster shape + rasterized type-layer "
            "+ pixel are flattened into one PNG, losing per-element editability). "
            "Only enable when you know the page is purely decorative."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    psd_path = Path(args.psd_path).expanduser().resolve()
    if not psd_path.exists():
        print(f"[psd2code] Error: PSD file not found: {psd_path}", file=sys.stderr)
        return 2
    if psd_path.suffix.lower() != ".psd":
        print(f"[psd2code] Warning: file does not have .psd extension: {psd_path}", file=sys.stderr)

    target_name = args.target.strip().lower()
    target_cls = _registry.get(target_name)
    if target_cls is None:
        available = ", ".join(_registry.list_targets()) or "<none>"
        print(
            f"[psd2code] Error: unknown target '{target_name}'. Available: {available}",
            file=sys.stderr,
        )
        return 2

    target = target_cls()
    ctx = PipelineContext(
        psd_path=psd_path,
        output_dir=Path(args.output).expanduser().resolve() if args.output else None,
        target_name=target_name,
        verbose=args.verbose,
    )
    # 把 CLI 开关挂到 artifacts，供 LayoutOptimizeStage 消费
    if args.no_css_pretty:
        ctx.set("css_pretty_enabled", False)
    ctx.set("css_pretty_style", args.css_style)
    # --no-smart-merge：仅 LayoutOptimizeStage 会读这个值（解析阶段已不做合图）
    if args.no_smart_merge:
        ctx.set("smart_merge", False)
    # --enable-image-layer-flatten：显式打开 ImageLayerFlatten（默认关闭）
    if args.enable_image_layer_flatten:
        ctx.set("image_layer_flatten_enabled", True)

    print(f"[psd2code] target={target_name}  psd={psd_path}")
    try:
        result_ctx = target.run(ctx)
    except Exception as exc:  # noqa: BLE001
        print(f"[psd2code] Pipeline failed: {exc}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

    out_dir = result_ctx.output_dir
    project_root = result_ctx.project_root
    if project_root and project_root != out_dir:
        print(f"[psd2code] Done. Project: {project_root} (target output: {out_dir})")
    else:
        print(f"[psd2code] Done. Output: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
