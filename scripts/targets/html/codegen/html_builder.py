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
    /* 字体兜底：PSD 里的设计字体通常不可用，若不声明则浏览器退回衬线 Times，
       中文落到宋体，字宽/字形都与设计稿差异明显（实测同一段文本会多折一行）。
       这里优先系统无衬线字体，宽度最接近设计稿。 */
    font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
}}

/* 视口容器：
   - 窄屏时裁掉"缩放前的 {ctx.psd_width}px 溢出"，并承载 fitCanvas() 写入的视觉高度；
   - 宽屏时给 #canvas 的 ``margin: 0 auto`` 提供居中参照。 */
#canvas-wrap {{
    position: relative;
    width: 100%;
    overflow: hidden;
}}

#canvas {{
    position: relative;
    width: {ctx.psd_width}px;
    height: {ctx.psd_height}px;
    margin: 0 auto;
    background: #fff;
    overflow: hidden;
    /* 锚点必须是 top left：窄屏下 #canvas 宽于视口，``margin: 0 auto`` 会退化为 0，
       盒子左缘贴在 x=0 且不会居中；若用 top center，缩放后内容会被整体右移
       （偏移 = 宽/2 × (1 - scale)），表现为左侧一大块空白、右侧被裁掉。 */
    transform-origin: top left;
}}

/* 窄屏等比缩放由 main.js 的 fitCanvas() 完成（见下方"为什么不用 CSS"说明） */

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
    <div id="canvas-wrap">
    <div id="canvas">
{layers_html}    </div>
    </div>

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

        js += f'''
/**
 * 窄屏等比缩放：设计稿 {ctx.psd_width}×{ctx.psd_height}，视口更窄时整体缩小。
 *
 * 为什么不用纯 CSS（历史 bug，勿回退）：
 *   `transform: scale(calc(100vw / {ctx.psd_width}))` 的 calc 结果是**长度**，
 *   而 scale() 只接受 <number>，浏览器会整条丢弃声明；Chrome 实测
 *   getComputedStyle(canvas).transform === 'none'，即页面在窄屏被裁切而非缩放。
 *   `scale(calc(100vw / {ctx.psd_width}px))` 依赖"长度 ÷ 长度"，属非标准用法。
 *   因此用 JS 算比例：可移植、可断言、可随 resize 更新。
 */
(function fitCanvas() {{
    var DESIGN_WIDTH = {ctx.psd_width};
    var DESIGN_HEIGHT = {ctx.psd_height};
    var canvas = document.getElementById('canvas');
    if (!canvas) return;
    var wrap = document.getElementById('canvas-wrap');

    function apply() {{
        var viewport = document.documentElement.clientWidth || window.innerWidth;
        var scale = Math.min(1, viewport / DESIGN_WIDTH);
        if (scale < 1) {{
            canvas.style.transform = 'scale(' + scale + ')';
            // transform 不改变布局尺寸：把视口容器压到视觉高度，避免底部多出
            // 一段可滚动空白；canvas 自身的 {ctx.psd_width}×{ctx.psd_height} 布局尺寸
            // 超出部分由 #canvas-wrap 的 overflow: hidden 裁掉。
            if (wrap) wrap.style.height = Math.round(DESIGN_HEIGHT * scale) + 'px';
        }} else {{
            canvas.style.transform = '';
            if (wrap) wrap.style.height = '';
        }}
    }}

    apply();
    window.addEventListener('resize', apply);
    window.addEventListener('orientationchange', apply);
}})();
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
