"""HTML-specific pipeline assembly.

P3: Multi-stage, IR-driven pipeline. IR is the authoritative representation
between ``core`` (PSD parsing + asset extraction) and ``targets/html``
(codegen + postprocess + emit).

Stage chain（由 ``build_html_prefix_stages`` 统一提供，React / Vue 直接复用）:
    1. LoadPsdStage              — open the .psd (psd-tools)
    2. ParseToIrStage            — PSD → IR (+ export all images to disk)
    3. HtmlCodegenStage          — IR → HTML/CSS/metadata/README
    4. BgNestingRestructureStage — 背景图层嵌套重组（在剔除 / 优化之前）
    5. PrunePreOptimizeStage     — 基于 index.html 剔除被遮挡 / 全透明图层
                                   （直接覆盖 index.html / style.css）
    6. LayoutOptimizeStage       — postprocess the raw HTML/CSS
                                   （含步骤 2.9 背景层吸收，在 CssDedup 之前）

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

from common.log_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# postprocess 链内部传递"已解析结果"的 ctx 键
#
# 契约（很重要）：
#   * 只在**成功写盘之后**写入——ctx 里的对象必须与磁盘上的 index.html /
#     style.css 内容一致，否则下游会看到"内存已改、磁盘未改"两种真相；
#   * 下游 stage 命中即免去一次重新解析，未命中（键缺失 / 值为 None）时
#     必须回退到"读盘 + 解析"，保证 stage 可以单独运行（测试 / 其它 target）；
#   * 对象只在 postprocess 链内使用，最后一个消费者负责置 None 释放。
# ---------------------------------------------------------------------------
_CTX_HTML_SOUP = "html_soup"
_CTX_CSS_RULES = "html_css_rules"
_CTX_CSS_HEADER = "html_css_header"



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
        from core.ir.typed_ir_cache import TypedIRCache  # Day 18 优化
        from targets.html.codegen.html_generator import HTMLGenerator  # type: ignore

        assert ctx.ir is not None and ctx.psd is not None and ctx.output_dir is not None
        exporter = ctx.get("layer_exporter")
        assert exporter is not None, "ParseToIrStage must run first"

        # 优化4-Day18：构建类型化 IR 缓存
        # 一次性遍历树，构建样式/效果缓存，供后续代码生成和多 target 复用
        ir_cache = TypedIRCache(ctx.ir)
        ctx.set("ir_cache", ir_cache)
        ctx.log(f"IR cache built: {ir_cache.dump_summary()}")

        # 继续使用原有的 to_legacy_layers（兼容模式）
        # 后续可升级为直接消费 TypedIRCache，这里仅作为过渡
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
# Stage 3.5: Background Nesting Restructure — 背景图层嵌套重组
# ---------------------------------------------------------------------------

class BgNestingRestructureStage(Stage):
    """识别背景图层并将同级兄弟元素拉入背景图层内部
    
    流程：
    1. 识别全覆盖背景图层（90% 覆盖 + image/layer-group 类型）
    2. 构建背景图层间的嵌套关系
    3. 将其他元素按包含关系拉入对应的背景图层
    4. 添加 data-bg-nested 标记
    5. 保存修改后的 HTML
    
    输入：HtmlCodegenStage 生成的 index.html
    输出：修改后的 index.html（已重组背景图层）
    """
    
    name = "bg_nesting_restructure"
    
    def run(self, ctx: PipelineContext) -> PipelineContext:
        from bs4 import BeautifulSoup  # type: ignore
        from targets.html.postprocess.bg_nesting_restructure.restructurer import (  # type: ignore
            restructure_by_bg_nesting_in_soup,
        )
        
        html_path = ctx.get("html_path")
        if not html_path:
            ctx.log("bg_nesting_restructure: skipped (no html_path)")
            return ctx
        
        html_path = Path(html_path)
        if not html_path.exists():
            ctx.log("bg_nesting_restructure: skipped (html not found)")
            return ctx
        
        try:
            from common.css_utils import (  # type: ignore
                dict_to_css,
                extract_global_css_header,
                parse_css_to_dict,
            )

            logger.info("\n🔄 应用背景图层嵌套重组...")

            # 读取 HTML 和 CSS
            html_content = html_path.read_text(encoding="utf-8")
            css_path = html_path.parent / "style.css"

            css_content = ""
            css_header = ""
            css_rules: dict[str, dict[str, str]] | None = None
            if css_path.exists():
                css_content = css_path.read_text(encoding="utf-8")
                css_header = extract_global_css_header(css_content)
                css_rules = parse_css_to_dict(css_content)

            # 执行背景图层嵌套重组（HTML + CSS 规则联动）。
            # 直接在 soup 上原地重组：后续 stage 复用同一个 DOM，省掉一次解析。
            soup = BeautifulSoup(html_content, "html.parser")
            restructure_by_bg_nesting_in_soup(
                soup,
                css_content=css_content,
                css_rules=css_rules,
                debug=False,
            )
            restructured_html = str(soup)

            # 写回 HTML
            html_path.write_text(restructured_html, encoding="utf-8")

            # 仅在落盘成功后把 DOM 交给下游：ctx 里的对象必须与磁盘文件同步，
            # 否则后续 stage 会在"内存已改、磁盘未改"的两种真相之间摇摆。
            ctx.set(_CTX_HTML_SOUP, soup)

            # 写回 CSS（如果存在）
            if css_path.exists() and css_rules is not None:
                css_path.write_text(
                    dict_to_css(css_rules, header=css_header),
                    encoding="utf-8",
                )

            # 把解析结果留给后续 stage 复用：同一份 CSS 在 prune / layout_optimize
            # 里各解析一次是纯重复工作（lex/parse 约占总耗时 8%）。
            if css_rules is not None:
                ctx.set(_CTX_CSS_RULES, css_rules)
                ctx.set(_CTX_CSS_HEADER, css_header)

            ctx.log(f"✅ 背景图层嵌套重组完成: {html_path}")
            return ctx
        except Exception as e:  # noqa: BLE001
            ctx.log(f"❌ 背景图层嵌套重组失败: {e}")
            import traceback
            traceback.print_exc()
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
            # 复用上游 stage 解析好的 CSS（未命中时再自行解析）
            css_rules = ctx.get(_CTX_CSS_RULES)
            css_header = ctx.get(_CTX_CSS_HEADER, "")
            if css_rules is None:
                css_content = css_path.read_text(encoding="utf-8")
                css_header = extract_global_css_header(css_content)
                css_rules = parse_css_to_dict(css_content)

            # 复用上游 stage 解析好的 DOM；缺失时自行解析。
            # 注意：解析结果在写盘成功之后才放回 ctx（见下方），避免 prune 中途
            # 失败却把"半改的 DOM"留给下游，导致 index.html 与优化版不一致。
            soup = ctx.get(_CTX_HTML_SOUP)
            if soup is None:
                from bs4 import BeautifulSoup  # type: ignore
                soup = BeautifulSoup(html_content, "html.parser")

            html_pruned, css_rules_pruned, prune_stats = prune_index_html(
                html_content=html_content,
                css_rules=css_rules,
                html_dir=html_path.parent,
                soup=soup,
            )

            pruned_n = prune_stats.get("occluded_layers_pruned", 0)
            trimmed_n = prune_stats.get("trimmed_layers", 0)
            if pruned_n > 0 or trimmed_n > 0:
                # 写回原文件（覆盖）：让 index.html 与 index_optimized.html
                # 在"被剔除图层"这一维度上保持一致。
                # 也包括 trim 操作改写的 CSS url 引用。
                html_path.write_text(html_pruned, encoding="utf-8")
                css_path.write_text(
                    dict_to_css(css_rules_pruned, header=css_header),
                    encoding="utf-8",
                )
                ctx.log(
                    f"pre-optimize prune: 剔除 {pruned_n} 个图层 "
                    f"(节省 {prune_stats.get('occluded_bytes_saved', 0) / 1024:.1f} KB)"
                    + (f", 裁剪 {trimmed_n} 个图层" if trimmed_n > 0 else "")
                )
            else:
                ctx.log("pre-optimize prune: 无可剔除图层")

            ctx.set("prune_stats", prune_stats)
            # 回写：prune 可能改写 css_rules（trim 后的 url / 被删规则的类）
            ctx.set(_CTX_CSS_RULES, css_rules_pruned)
            ctx.set(_CTX_CSS_HEADER, css_header)
            # 落盘（或确认无需落盘）之后才把 DOM 交给下游
            ctx.set(_CTX_HTML_SOUP, soup)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"⚠️  被遮挡图层剔除失败（保留原始 index.html）: {e}")
            # DOM 可能已被部分修改、但磁盘仍是旧内容 → 丢弃缓存，强制下游重解析
            ctx.set(_CTX_HTML_SOUP, None)
            ctx.set(_CTX_CSS_RULES, None)
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
            logger.info("\n🎨 应用智能布局优化...")
            # 上游已解析过 DOM 时不再读盘：optimize_layout 会直接在该 DOM 上
            # 原地优化，html_content 只是它缺失时的解析源（避免两个真源）。
            soup = ctx.get(_CTX_HTML_SOUP)
            html_content = "" if soup is not None else html_path.read_text(encoding="utf-8")
            # 复用上游 stage 的 CSS 解析结果（未命中时再读盘解析）
            css_rules = ctx.get(_CTX_CSS_RULES)
            css_header = ctx.get(_CTX_CSS_HEADER)
            from common.css_utils import extract_global_css_header  # type: ignore
            from targets.html.postprocess.layout_optimizer.transformers.css_pretty import (  # type: ignore
                CssPrettyConfig,
            )
            from targets.html.postprocess.layout_optimizer.transformers.image_layer_flatten import (  # type: ignore
                FlattenConfig,
            )
            if css_rules is None or css_header is None:
                css_content = css_path.read_text(encoding="utf-8")
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
            strict = bool(ctx.get("strict", False))
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
            # 复用上游 DOM：layout 优化全程在同一份 soup 上原地进行，省一次解析。
            html_opt, css_opt, stats = optimize_layout(
                html_content,
                css_rules,
                global_header=css_header,
                pretty_config=pretty_cfg,
                images_dir=(html_path.parent / "images") if smart_merge else None,
                flatten_config=FlattenConfig(enabled=image_layer_flatten_enabled),
                strict=strict,
                soup=soup,
            )

            # 背景层吸收已并入 LayoutOptimizer（步骤 2.9）；这里仅把子统计
            # 透出到 ctx，保持与原 BackgroundLayerAbsorptionStage 的 artifacts 兼容。
            if stats.get("bg_absorption"):
                ctx.set("bg_absorption_stats", stats["bg_absorption"])

            self._emit_optimized_files(
                ctx, html_opt, css_opt, stats,
                html_path, css_path, css_header, smart_merge, dict_to_css,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"⚠️  布局优化失败（保留原始版本）: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # DOM 只在 postprocess 链内复用；最后一个消费者负责释放，
            # 避免 ctx 在整个转换周期里持有整棵 BeautifulSoup 树。
            ctx.set(_CTX_HTML_SOUP, None)

        return ctx

    # ------------------------------------------------------------------
    # Helper: 将优化产物 + sidecar 元数据写入磁盘
    # ------------------------------------------------------------------

    @staticmethod
    def _emit_optimized_files(
        ctx: PipelineContext,
        html_opt: str,
        css_opt,  # noqa: ANN001 — Dict[str, Dict[str, str]]
        stats: dict,
        html_path: Path,
        css_path: Path,
        css_header: str,
        smart_merge: bool,
        dict_to_css,  # noqa: ANN001 — callable
    ) -> None:
        """将优化后的 HTML/CSS 及 sidecar 文件写入磁盘。

        从 ``run()`` 拆出的纯 IO 助手，方便独立测试 + 降低 ``run()`` 行数。
        """
        import json as _json

        import re as _re

        html_opt_path = html_path.with_name(html_path.stem + "_optimized.html")
        css_opt_path = css_path.with_name(css_path.stem + "_optimized.css")
        # index.html 里的 CSS 链接带内容指纹（``style.css?v=xxx``），这里按正则
        # 换成优化版样式表并**丢弃旧版本号**（后面会用优化版 CSS 重新算）。
        html_opt = _re.sub(
            r'href="style\.css(?:\?[^"]*)?"',
            'href="style_optimized.css"',
            html_opt,
        )

        # 剥离 dev metadata（data-name / data-type / id="layer-*"）→ layer_map.json
        # 注意：这里必须走「字符串 → 重新解析」的路径。直接在 LayoutOptimizer 的
        # soup 上剥离会保留其排版中的一处空白文本节点，导致 index_optimized.html
        # 与既有产物相差一个空行（实测）；如需省这次解析，必须先解决该空白差异。
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
        alias_map = stats.get("_class_alias_map") or {}
        alias_path: Path | None = None
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
                    logger.info(
                        f"   - 背景合成: {bg_stats['rules_flattened']} 规则 "
                        f"(折叠 {bg_stats['layers_collapsed']} 层, "
                        f"节省 {bg_stats['bytes_saved'] / 1024:.1f} KB)"
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"⚠️  背景合成失败（保留多层 CSS）: {e}")
                import traceback
                traceback.print_exc()

        css_opt_path.write_text(css_text, encoding="utf-8")

        # 避免预览器缓存旧 CSS 导致“新 HTML + 旧 CSS”错配。
        import hashlib as _hashlib
        css_version = _hashlib.md5(css_text.encode("utf-8")).hexdigest()[:8]
        html_opt = _re.sub(
            r'href="style_optimized\.css(?:\?[^"]*)?"',
            f'href="style_optimized.css?v={css_version}"',
            html_opt,
            count=1,
        )
        html_opt_path.write_text(html_opt, encoding="utf-8")

        logger.info("✅ 布局优化完成！")
        logger.info(f"   原始版本: {html_path}")
        logger.info(f"   优化版本: {html_opt_path}")
        logger.info(f"   元数据映射: {map_path}")
        if alias_map:
            logger.info(f"   类名别名表: {alias_path} ({len(alias_map)} 条)")
        logger.info(
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
                logger.info(f"   映射报告: {mapping_path}")
            if image_index_path:
                logger.info(f"   图片索引: {image_index_path}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"⚠️  mapping report 生成失败: {e}")

        # 优化统计 + 失败明细落盘：这是排查"某步骤被静默跳过"的唯一证据
        # （LayoutOptimizer 容错模式只打 warning，不留文件；CI 需要可断言的产物）
        try:
            layout_payload = {
                "version": 1,
                "failures": stats.get("_failures", []),
                "counters": {
                    k: v for k, v in stats.items() if not k.startswith("_")
                },
            }
            stats_path = html_opt_path.parent / "_layout_stats.json"
            stats_path.write_text(
                _json.dumps(layout_payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"⚠️  优化统计落盘失败: {e}")

        ctx.set("html_path", str(html_opt_path))
        ctx.set("layout_stats", stats)


# ---------------------------------------------------------------------------
# Pipeline assembly
# ---------------------------------------------------------------------------

def build_html_prefix_stages(subdir_name: str | None = None) -> list[Stage]:
    """HTML 中间产物的标准 Stage 链（所有 target 共用）。

    React / Vue target 都是「先生成 HTML 产物、再二次转换」，因此这条链路必须
    由三端共享 —— 任何一次 HTML 侧优化升级都会自动惠及 React / Vue。

    ``subdir_name``：HTML 中间产物落盘子目录。``None`` 时取 ``ctx.target_name``
    （``--target html`` → ``html/``）；React / Vue 需显式传 ``"html"``，避免把
    中间产物写进各自的 ``react/`` / ``vue/`` 目录。

    注意：背景层吸收（BackgroundLayerAbsorption）已收进
    ``LayoutOptimizer``（步骤 2.9，在 CssDedup 之前），因此这里不再有
    独立的吸收 Stage。
    """
    return [
        LoadPsdStage(subdir_name=subdir_name),
        ParseToIrStage(),
        HtmlCodegenStage(),
        BgNestingRestructureStage(),  # 背景图层嵌套重组（在优化前）
        PrunePreOptimizeStage(),
        LayoutOptimizeStage(),
    ]


def build_html_pipeline(ctx: PipelineContext) -> Pipeline:
    return Pipeline(build_html_prefix_stages())
