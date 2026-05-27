"""HTML-specific pipeline assembly.

P3: Multi-stage, IR-driven pipeline. IR is the authoritative representation
between ``core`` (PSD parsing + asset extraction) and ``targets/html``
(codegen + postprocess + emit).

Stage chain:
    1. LoadPsdStage              — open the .psd (psd-tools)
    2. ParseToIrStage            — PSD → IR (+ export all images to disk)
    3. HtmlCodegenStage          — IR → HTML/CSS/metadata/README
    4. PrunePreOptimizeStage     — 基于 index.html 剔除被遮挡 / 全透明图层
                                   （直接覆盖 index.html / style.css）
    5. LayoutOptimizeStage       — postprocess the raw HTML/CSS

为何 Prune 在 Optimize 之前（2026-05-27）：
- 剔除若发生在 LayoutOptimizer 内部或之后，DOMRestructure / FlexApplier 等
  基于"子节点集合"做布局推断的 transformer 看到的子集合与"未优化版"不同，
  flex 流重算导致兄弟节点位置偏移（实测 4% 像素差异）。
- 剔除前置后，所有下游 transformer 看到的从一开始就是"剔除后的可见图层
  集合"，envelope/对齐/flex 流推断与最终浏览器视觉天然一致。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from framework import Pipeline, PipelineContext, Stage


# ---------------------------------------------------------------------------
# Stage 1: Load PSD
# ---------------------------------------------------------------------------

class LoadPsdStage(Stage):
    """通用的 Load 阶段：打开 PSD 并把 ``ctx.output_dir`` 规范化到
    ``<base>/<psd_stem>/<subdir>/``，同时回填 ``ctx.project_root =
    <base>/<psd_stem>/``。

    ``subdir_name`` 的解析优先级：
      1. 构造参数显式传入（例如 React 管线里复用本 Stage 生成 HTML 中间产物时，
         会显式传 ``subdir_name="html"``，让中间产物落到 ``html/`` 下）；
      2. 否则使用 ``ctx.target_name``（例如 ``--target html`` 得到 ``html/``，
         ``--target vue`` 得到 ``vue/``）；
      3. 都不可用时回退到 ``"out"``（不应该出现，仅做兜底）。

    这样每个 target 的产物自动落在以 target 名命名的子目录下，跨 target 产物
    互不覆盖；未来新增 target 无需重写 Load 逻辑。
    """

    name = "load_psd"

    def __init__(self, subdir_name: str | None = None) -> None:
        self._subdir_name = subdir_name

    def run(self, ctx: PipelineContext) -> PipelineContext:
        from psd_tools import PSDImage  # type: ignore[import-untyped]
        from config import Config  # type: ignore

        base = Path(ctx.output_dir) if ctx.output_dir else Path(Config.OUTPUT_BASE_DIR)
        project_root = base / ctx.psd_path.stem
        subdir = self._subdir_name or ctx.target_name or "out"
        out = project_root / subdir

        # 只清理当前 target 的子目录，兄弟 target 产物（如 html/ 对于 react 运行）保持不动
        if out.exists():
            shutil.rmtree(out)
            ctx.log(f"cleared existing output: {out}")
        out.mkdir(parents=True, exist_ok=True)

        ctx.project_root = project_root
        ctx.output_dir = out

        # Open PSD (lazy; psd-tools reads on demand)
        ctx.psd = PSDImage.open(str(ctx.psd_path))  # type: ignore[misc]
        ctx.log(f"opened PSD: {ctx.psd_path} [{ctx.psd.width}x{ctx.psd.height}]")
        return ctx


# ---------------------------------------------------------------------------
# Stage 2: Parse PSD -> IR (exports all images as side effect)
# ---------------------------------------------------------------------------

class ParseToIrStage(Stage):
    name = "parse_to_ir"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        from common.utils import reset_image_counter, reset_filename_registry  # type: ignore
        from core.psd.parser import parse_psd_to_ir
        from semantic.name_resolver import get_default_resolver  # type: ignore

        # Reset global image counter so filenames are deterministic across runs.
        reset_image_counter()
        reset_filename_registry()

        # 启用语义命名报告（codegen 阶段 SimpleNamer/utils 都会走默认 resolver，
        # 末尾在 HtmlCodegenStage 里一次性写到 _naming_report.md）
        get_default_resolver().enable_report()

        assert ctx.psd is not None, "LoadPsdStage must run before ParseToIrStage"
        assert ctx.output_dir is not None, "output_dir must be set by LoadPsdStage"

        # 解析阶段不做合图：1 PSD 图层 = 1 layer_info（叶图层）或 group_info（组）。
        # CLI --no-smart-merge 仅影响下游 LayoutOptimizeStage，不影响这里。
        doc, exporter, legacy_tree = parse_psd_to_ir(
            psd_path=ctx.psd_path, output_dir=ctx.output_dir, psd=ctx.psd,
        )
        # Validate IR round-trip (pydantic has already validated during construction).
        ctx.ir = doc
        ctx.set("layer_exporter", exporter)
        ctx.set("legacy_layers", legacy_tree)
        ctx.log(f"IR built: {sum(1 for _ in doc.iter_nodes())} nodes, {exporter.exported_count} layers exported")
        return ctx


# ---------------------------------------------------------------------------
# Stage 3: IR -> HTML / CSS / metadata / README
# ---------------------------------------------------------------------------

class HtmlCodegenStage(Stage):
    name = "html_codegen"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        from core.ir import to_legacy_layers
        from targets.html.codegen.html_generator import HTMLGenerator  # type: ignore

        assert ctx.ir is not None and ctx.psd is not None and ctx.output_dir is not None
        exporter = ctx.get("layer_exporter")
        assert exporter is not None, "ParseToIrStage must run first"

        layers = to_legacy_layers(ctx.ir)
        gen = HTMLGenerator(ctx.psd.width, ctx.psd.height, ctx.output_dir, ctx.psd_path.stem)
        html_path = gen.generate_html(layers)
        gen.generate_metadata(layers, exporter.exported_count, exporter.skipped_count)
        gen.generate_readme(exporter.exported_count, exporter.skipped_count)

        # 写出 _naming_report.md：所有图层 → token 的来源/置信度对照
        try:
            from semantic.name_resolver import get_default_resolver  # type: ignore
            report_md = get_default_resolver().dump_report_md()
            report_path = Path(ctx.output_dir) / "_naming_report.md"
            report_path.write_text(report_md, encoding="utf-8")
            ctx.log(f"naming report written: {report_path}")
        except Exception as e:  # noqa: BLE001
            # 报告写失败不应影响主流程
            ctx.log(f"naming report skipped: {e}")

        ctx.set("html_generator", gen)
        ctx.set("html_path", html_path)
        ctx.log(f"html written: {html_path}")
        return ctx


# ---------------------------------------------------------------------------
# Stage 4: Pre-optimize Prune — 基于 index.html 剔除被遮挡 / 全透明图层
# ---------------------------------------------------------------------------

class PrunePreOptimizeStage(Stage):
    """剔除 index.html 中"视觉上看不见"的图层（被完全遮挡 / 自身全透明）。

    输入：``html_path`` 指向的 ``index.html`` + 同目录 ``style.css``。
    输出：**直接覆盖** ``index.html`` 与 ``style.css``（让用户看到的
    "未优化版"也是已剔除的产物，保持与 ``index_optimized.html`` 视觉一致）。

    必须跑在 ``LayoutOptimizeStage`` 之前。详见 transformers/
    ``occluded_layer_pruner.py`` 模块文档。
    """

    name = "prune_pre_optimize"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        from common.css_utils import (  # type: ignore
            dict_to_css,
            extract_global_css_header,
            parse_css_to_dict,
        )
        from targets.html.postprocess.layout_optimizer.transformers.occluded_layer_pruner import (  # type: ignore
            prune_index_html,
        )

        html_path = ctx.get("html_path")
        if not html_path:
            ctx.log("prune_pre_optimize: skipped (no html_path)")
            return ctx
        html_path = Path(html_path)
        css_path = html_path.parent / "style.css"
        if not (html_path.exists() and css_path.exists()):
            ctx.log("prune_pre_optimize: skipped (html/css missing)")
            return ctx

        try:
            html_content = html_path.read_text(encoding="utf-8")
            css_content = css_path.read_text(encoding="utf-8")
            css_header = extract_global_css_header(css_content)
            css_rules = parse_css_to_dict(css_content)

            html_pruned, css_rules_pruned, prune_stats = prune_index_html(
                html_content=html_content,
                css_rules=css_rules,
                html_dir=html_path.parent,
            )

            pruned_n = prune_stats.get("occluded_layers_pruned", 0)
            if pruned_n > 0:
                # 写回原文件（覆盖）：让 index.html 与 index_optimized.html
                # 在"被剔除图层"这一维度上保持一致。
                html_path.write_text(html_pruned, encoding="utf-8")
                css_path.write_text(
                    dict_to_css(css_rules_pruned, header=css_header),
                    encoding="utf-8",
                )
                ctx.log(
                    f"pre-optimize prune: 剔除 {pruned_n} 个图层 "
                    f"(节省 {prune_stats.get('occluded_bytes_saved', 0) / 1024:.1f} KB)"
                )
            else:
                ctx.log("pre-optimize prune: 无可剔除图层")

            ctx.set("prune_stats", prune_stats)
        except Exception as e:  # noqa: BLE001
            print(f"⚠️  被遮挡图层剔除失败（保留原始 index.html）: {e}")
            import traceback
            traceback.print_exc()

        return ctx


# ---------------------------------------------------------------------------
# Stage 5: Postprocess — layout optimization
# ---------------------------------------------------------------------------

class LayoutOptimizeStage(Stage):
    name = "layout_optimize"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        from common.css_utils import dict_to_css, parse_css_to_dict  # type: ignore
        from targets.html.postprocess.layout_optimizer import optimize_layout  # type: ignore

        html_path = ctx.get("html_path")
        if not html_path:
            ctx.log("layout_optimize: skipped (no html_path)")
            return ctx
        html_path = Path(html_path)
        css_path = html_path.parent / "style.css"
        if not (html_path.exists() and css_path.exists()):
            ctx.log("layout_optimize: skipped (html/css missing)")
            return ctx

        try:
            print("\n🎨 应用智能布局优化...")
            html_content = html_path.read_text(encoding="utf-8")
            css_content = css_path.read_text(encoding="utf-8")
            from common.css_utils import extract_global_css_header  # type: ignore
            from targets.html.postprocess.layout_optimizer.transformers.css_pretty import (  # type: ignore
                CssPrettyConfig,
            )
            from targets.html.postprocess.layout_optimizer.transformers.image_layer_flatten import (  # type: ignore
                FlattenConfig,
            )
            css_header = extract_global_css_header(css_content)
            css_rules = parse_css_to_dict(css_content)
            # CLI --no-css-pretty 关闭美化，仅做语义优化（dict_to_css 输出）
            pretty_enabled = ctx.get("css_pretty_enabled", True)
            # CLI --css-style 选择 compact（默认，紧凑接近 figma）/ expanded（开发友好详尽）
            pretty_style = ctx.get("css_pretty_style", "compact")
            # CLI --no-smart-merge：关闭 LayoutOptimizer 链路的"多层背景内联合成"
            #   (DOMRestructure 把容器内多张装饰背景合成为单张 PNG，
            #    不删除任何 DOM 子节点，副作用小，默认开启)
            # ⚠️ ImageLayerFlatten（步骤 1.2）默认关闭（FlattenConfig dataclass 默认
            #   enabled=False），它会把容器内 N 个 image 子合成单图并删除子 DOM，
            #   过于粗暴。需启用须显式 --enable-image-layer-flatten。
            smart_merge = bool(ctx.get("smart_merge", True))
            image_layer_flatten_enabled = bool(ctx.get("image_layer_flatten_enabled", False))
            # style_optimized.css 是最终交付物，默认不写任何注释（章节标题 /
            # 版块切分 / 合并组数量注释全部关闭）。映射信息通过 layer_map.json /
            # class_alias_map.json / _mapping_report.md 三个 sidecar 文件提供。
            pretty_cfg = CssPrettyConfig(
                enabled=bool(pretty_enabled),
                style=str(pretty_style),
                file_skeleton=False,
                section_comments=False,
                merge_group_comment=False,
            )
            html_opt, css_opt, stats = optimize_layout(
                html_content,
                css_rules,
                global_header=css_header,
                pretty_config=pretty_cfg,
                images_dir=(html_path.parent / "images") if smart_merge else None,
                flatten_config=FlattenConfig(enabled=image_layer_flatten_enabled),
            )

            html_opt_path = html_path.with_name(html_path.stem + "_optimized.html")
            css_opt_path = css_path.with_name(css_path.stem + "_optimized.css")
            html_opt = html_opt.replace('href="style.css"', 'href="style_optimized.css"')

            # 剥离 dev metadata（data-name / data-type / id="layer-*"）→ layer_map.json
            from targets.html.postprocess.strip_dev_metadata import (  # type: ignore
                strip_and_collect,
                write_layer_map,
            )
            html_opt, layer_map = strip_and_collect(html_opt)
            map_path = html_opt_path.parent / "layer_map.json"
            write_layer_map(layer_map, map_path)

            # class_alias_map.json：原 ``__<layer_id>`` 类名 → 新精简类名
            # （SemanticClassRename 产出）。开发者在优化版里看到 ``.nickname-3``
            # 想回查 PSD 图层时的"类名→类名"反查入口；同时也是"优化版 class 与
            # 原始 style.css class" 的桥梁。
            import json as _json
            alias_map = stats.get("_class_alias_map") or {}
            if alias_map:
                alias_path = html_opt_path.parent / "class_alias_map.json"
                # 按新类名自然序排序，便于阅读
                sorted_alias = dict(
                    sorted(alias_map.items(), key=lambda kv: (kv[1], kv[0]))
                )
                alias_payload = {
                    "version": 1,
                    "description": (
                        "优化版类名别名表：key 是原始 ``<base>__<layer_id>`` "
                        "（与 style.css 一致），value 是 style_optimized.css 中的"
                        "新精简类名。通过 layer_map.json.by_class[value] 可进一步"
                        "反查 PSD 图层元数据。"
                    ),
                    "aliases": sorted_alias,
                }
                alias_path.write_text(
                    _json.dumps(alias_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            html_opt_path.write_text(html_opt, encoding="utf-8")
            # CssPretty 优先：开发者友好的排版（DOM 序 + 属性分段 + 合并组多行）。
            # 失败时降级到 dict_to_css（机械字典渲染）。
            pretty_css = stats.get("_pretty_css") or ""
            if pretty_css:
                css_text = pretty_css
            else:
                merge_groups = stats.get("_css_merge_groups") or None
                css_text = dict_to_css(
                    css_opt, header=css_header, merge_groups=merge_groups
                )

            # 多层 url() 背景合成（CSS 文本最终态后处理）
            # smart_merge=False 时跳过，保持多 url 背景原样
            if smart_merge:
                try:
                    from targets.html.postprocess.background_flatten import (  # type: ignore
                        flatten_multi_url_backgrounds,
                    )
                    images_dir = html_opt_path.parent / "images"
                    css_text, bg_stats = flatten_multi_url_backgrounds(
                        css_text, images_dir
                    )
                    stats["bg_flatten"] = bg_stats
                    if bg_stats.get("rules_flattened"):
                        print(
                            f"   - 背景合成: {bg_stats['rules_flattened']} 规则 "
                            f"(折叠 {bg_stats['layers_collapsed']} 层, "
                            f"节省 {bg_stats['bytes_saved'] / 1024:.1f} KB)"
                        )
                except Exception as e:  # noqa: BLE001
                    print(f"⚠️  背景合成失败（保留多层 CSS）: {e}")
                    import traceback
                    traceback.print_exc()

            css_opt_path.write_text(css_text, encoding="utf-8")

            print(f"✅ 布局优化完成！")
            print(f"   原始版本: {html_path}")
            print(f"   优化版本: {html_opt_path}")
            print(f"   元数据映射: {map_path}")
            if alias_map:
                print(f"   类名别名表: {alias_path} ({len(alias_map)} 条)")
            print(
                f"   统计: DOM 重构 {stats['dom_restructured']} 个, "
                f"flex 应用 {stats['flex_applied']} 个"
            )

            # 三向映射 + 图片索引（class ↔ image ↔ PSD layer），失败不阻断流程
            try:
                from targets.html.postprocess.mapping_report import (  # type: ignore
                    write_mapping_reports,
                )
                mapping_path, image_index_path = write_mapping_reports(html_opt_path.parent)
                if mapping_path:
                    print(f"   映射报告: {mapping_path}")
                if image_index_path:
                    print(f"   图片索引: {image_index_path}")
            except Exception as e:  # noqa: BLE001
                print(f"⚠️  mapping report 生成失败: {e}")

            ctx.set("html_path", str(html_opt_path))
            ctx.set("layout_stats", stats)
        except Exception as e:  # noqa: BLE001
            print(f"⚠️  布局优化失败（保留原始版本）: {e}")
            import traceback
            traceback.print_exc()

        return ctx


# ---------------------------------------------------------------------------
# Pipeline assembly
# ---------------------------------------------------------------------------

def build_html_pipeline(ctx: PipelineContext) -> Pipeline:
    return Pipeline([
        LoadPsdStage(),
        ParseToIrStage(),
        HtmlCodegenStage(),
        PrunePreOptimizeStage(),
        LayoutOptimizeStage(),
    ])
