"""ReactTarget: assembles the react-specific pipeline.

架构策略：最大化复用。
    - 前 4 个 Stage（LoadPsd / ParseToIr / HtmlCodegen / LayoutOptimize）完全复用
      ``targets.html.pipeline``，产出 index_optimized.html + style_optimized.css。
    - 在其尾部追加 2 个 React 专属 Stage：
        5) HtmlToReactStage  —— HTML → JSX（属性驼峰化、自闭合、class→className），
                                 CSS → CSS Module（类名保持不变）。
        6) ReactScaffoldStage —— 生成 Vite 项目脚手架（package.json / vite.config.js
                                  / index.html / main.jsx / README.md），并把 PSD 导出
                                  的 images/ 复制或软链到 src/assets/images/。

这样保证 React 输出与 HTML 输出是**同一份 DOM 结构**的两种表达，
布局质量、效果渲染、资产去重等能力完全一致。
"""

from __future__ import annotations

from framework import Pipeline, PipelineContext
from targets.base import Target
from targets.registry import register

from .pipeline import build_react_pipeline


@register("react")
class ReactTarget(Target):
    """Produce a minimal Vite + React project under ``<output>/react/``.

    The project renders the PSD layout pixel-for-pixel using absolute
    positioning (inherited from the HTML target), wrapped in a single
    ``<App />`` component with CSS Module styling.
    """

    def build_pipeline(self, ctx: PipelineContext) -> Pipeline:
        return build_react_pipeline(ctx)
