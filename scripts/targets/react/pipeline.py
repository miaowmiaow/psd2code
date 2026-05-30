"""React-specific pipeline assembly.

Stage chain (前 5 段直接复用 HTML target；通过给 ``LoadPsdStage`` 传入
``subdir_name="html"``，让中间的 HTML 产物落到 ``<psd>/html/``，与真正的
``<psd>/react/`` 同级）：

    output/<psd>/
    ├── html/    ← 由前 5 个 stage 产出（HtmlCodegen + Prune + LayoutOptimize）
    └── react/   ← 由 HtmlToReactStage + ReactScaffoldStage 产出

两者各自独立，互不交叠。

    1. LoadPsdStage(subdir_name="html") — 打开 .psd，准备 ``<psd>/html/``
    2. ParseToIrStage      — PSD → IR + 导出所有图片到 ``html/images/``
    3. HtmlCodegenStage    — IR → html/index.html / html/style.css / …
    4. PrunePreOptimizeStage — 剔除被遮挡 / 全透明图层（必须在 LayoutOptimize 之前）
    5. LayoutOptimizeStage — 产出 html/index_optimized.html / html/style_optimized.css
    6. HtmlToReactStage    — HTML/CSS → JSX + CSS + 复制图片到 ``react/src/assets/images/``
    7. ReactScaffoldStage  — 生成 Vite 脚手架到 ``react/``
"""

from __future__ import annotations

from framework import Pipeline, PipelineContext
from targets.html.pipeline import (
    HtmlCodegenStage,
    LayoutOptimizeStage,
    LoadPsdStage,
    ParseToIrStage,
    PrunePreOptimizeStage,
)

from .stages import HtmlToReactStage, ReactScaffoldStage


def build_react_pipeline(ctx: PipelineContext) -> Pipeline:
    return Pipeline([
        # React target 的前 5 段在生成 HTML 中间产物，因此显式落到 html/ 子目录，
        # 避免把 html 产物塞进 react/ 造成目录混杂。
        LoadPsdStage(subdir_name="html"),
        ParseToIrStage(),
        HtmlCodegenStage(),
        PrunePreOptimizeStage(),
        LayoutOptimizeStage(),
        HtmlToReactStage(),
        ReactScaffoldStage(),
    ])
