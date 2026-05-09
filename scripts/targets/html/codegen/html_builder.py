# -*- coding: utf-8 -*-
"""HTML / CSS / JS 文本构建器。

把 CodegenContext 中收集到的 css_rules / 画布尺寸等信息，
拼装成最终的 style.css / index.html / main.js 字符串。
"""

from typing import Any

from .context import CodegenContext
from .version import __version__


class HtmlBuilder:
    """独立构建器，不继承任何 mixin。"""

    def __init__(self, ctx: CodegenContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------
    # CSS
    # ------------------------------------------------------------------

    def build_css(self) -> str:
        ctx = self.ctx
        css = f'''/* PSD2HTML v{__version__} — {ctx.psd_name} */
/* BEM + 语义化命名 */

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
    width: 100vw;
    min-height: 100vh;
    overflow-x: hidden;
    overflow-y: auto;
    background: #f0f0f0;
}}

#canvas {{
    position: relative;
    width: {ctx.psd_width}px;
    height: {ctx.psd_height}px;
    margin: 0 auto;
    background: #fff;
    overflow: hidden;
    transform-origin: top center;
}}

@media screen and (max-width: {ctx.psd_width}px) {{
    #canvas {{
        transform: scale(calc(100vw / {ctx.psd_width}));
    }}
}}

/* ========== 图层样式 ========== */
'''
        css += '\n'.join(ctx.css_rules)
        css += '\n'
        return css

    # ------------------------------------------------------------------
    # HTML
    # ------------------------------------------------------------------

    def build_html(self, layers_html: str) -> str:
        ctx = self.ctx
        return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>{ctx.psd_name}</title>
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <div id="canvas">
{layers_html}    </div>

    <script src="main.js"></script>
</body>
</html>
'''

    # ------------------------------------------------------------------
    # JS
    # ------------------------------------------------------------------

    def build_js(self) -> str:
        ctx = self.ctx
        js = f'// PSD2HTML v{__version__} — {ctx.psd_name}\n'
        js += "console.log('PSD2HTML 页面加载完成');\n\n"

        js += '''/**
 * 多语言工具：批量替换带 data-i18n-key 的文本
 * @param {Object} dict  { key: translatedText }
 */
function setLanguage(dict) {
    document.querySelectorAll('[data-i18n-key]').forEach(function(el) {
        var key = el.getAttribute('data-i18n-key');
        if (dict[key] !== undefined) {
            el.textContent = dict[key];
        }
    });
}
'''

        return js


# ------------------------------------------------------------------
# 向后兼容：mixin 风格（仍可作为多继承注入，方法委托 self._html_builder）
# ------------------------------------------------------------------

class HtmlBuilderMixin:
    def _build_css(self) -> str:
        return self._html_builder.build_css()

    def _build_html(self, layers_html: str) -> str:
        return self._html_builder.build_html(layers_html)

    def _build_js(self) -> str:
        return self._html_builder.build_js()

    def _text_style_css(self, layer: dict[str, Any]) -> str:
        # 仅保留给少数旧测试调用；实际渲染中已由 TextRenderer 自带
        from .renderers.text_renderer import _text_style_css
        return _text_style_css(layer)
