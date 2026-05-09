"""VueTarget: assembles the vue-specific pipeline.

架构策略：与 React target 一致，最大化复用 HTML target 前 4 段。
    - 前 4 个 Stage（LoadPsd / ParseToIr / HtmlCodegen / LayoutOptimize）完全复用
      ``targets.html.pipeline``，产出 index_optimized.html + style_optimized.css。
    - 在其尾部追加 2 个 Vue 专属 Stage：
        5) HtmlToVueStage    —— HTML → Vue ``<template>``（Vue 与 HTML 高度同源，
                                  保留 ``class``/``<img>``，仅做图片路径改写、
                                  注释格式转换、自闭合规范化），CSS → SFC ``<style>``
                                  内嵌（保持全局作用域，与 HTML target 完全一致）。
        6) VueScaffoldStage  —— 生成 Vite + Vue 3 项目脚手架（package.json /
                                  vite.config.js / index.html / main.js / README），
                                  并把 PSD 导出的 images/ 复制到 src/assets/images/。

这样保证 Vue 输出与 HTML / React 输出是**同一份 DOM 结构**的不同语法表达，
布局质量、效果渲染、资产去重等能力完全一致。
"""

from __future__ import annotations

from framework import Pipeline, PipelineContext
from targets.base import Target
from targets.registry import register

from .pipeline import build_vue_pipeline


@register("vue")
class VueTarget(Target):
    """Produce a minimal Vite + Vue 3 project under ``<output>/vue/``.

    The project renders the PSD layout pixel-for-pixel using absolute
    positioning (inherited from the HTML target), wrapped in a single
    ``<App />`` SFC.
    """

    def build_pipeline(self, ctx: PipelineContext) -> Pipeline:
        return build_vue_pipeline(ctx)
