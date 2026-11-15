"""
React-specific pipeline assembly.

Stage chain（前 6 段直接复用 HTML target 的标准前缀链
``targets.html.pipeline.build_html_prefix_stages``，通过
``subdir_name="html"`` 让中间 HTML 产物落到 ``<psd>/html/``，与真正的
``<psd>/react/`` 同级）：

    output/<psd>/
    ├── html/    ← 由前缀链产出（Codegen + BgNesting + Prune + LayoutOptimize）
    └── react/   ← 由 HtmlToReactStage + ReactScaffoldStage 产出

两者各自独立，互不交叠。

    1. LoadPsdStage(subdir_name="html") — 打开 .psd，准备 ``<psd>/html/``
    2. ParseToIrStage         — PSD → IR + 导出所有图片到 ``html/images/``
    3. HtmlCodegenStage       — IR → html/index.html / html/style.css / …
    4. BgNestingRestructureStage — 背景图层嵌套重组
    5. PrunePreOptimizeStage  — 剔除被遮挡 / 全透明图层（必须在 LayoutOptimize 之前）
    6. LayoutOptimizeStage    — 产出 html/index_optimized.html / html/style_optimized.css
                                （含背景层吸收，步骤 2.9）
    7. HtmlToReactStage    — HTML/CSS → JSX + CSS + 复制图片到 ``react/src/assets/images/``
    8. ReactScaffoldStage  — 生成 Vite 脚手架到 ``react/``
"""

from __future__ import annotations

from framework import Pipeline, PipelineContext
from targets.html.pipeline import build_html_prefix_stages

from .stages import HtmlToReactStage, ReactScaffoldStage


def build_react_pipeline(ctx: PipelineContext) -> Pipeline:
    # HTML 中间产物统一落到 html/ 子目录（与 react/ 平级），避免目录混杂。
    # 前缀链由 targets.html.pipeline.build_html_prefix_stages 提供，与
    # --target html 完全一致：任何 HTML 侧优化升级都自动惠及本 target。
    return Pipeline([
        *build_html_prefix_stages(subdir_name="html"),
        HtmlToReactStage(),
        ReactScaffoldStage(),
    ])
