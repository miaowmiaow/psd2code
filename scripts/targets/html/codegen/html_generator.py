# -*- coding: utf-8 -*-
"""HTML 生成器（Composition 版）。

设计模式：
- Composition：不再使用 mixin 多继承，通过持有独立组件完成职责分工
  * CodegenContext   —— 共享状态容器（css_rules / namer / 画布信息）
  * LayerRenderer    —— 图层树递归渲染（内部用 Strategy + RendererRegistry 分派）
  * HtmlBuilder      —— HTML / CSS / JS 字符串拼装
- Strategy + Registry：见 renderers/ 子包（GroupRenderer / ImageRenderer / TextRenderer）

向后兼容：
- `HTMLGenerator`、`SimpleNamer`、`_esc`、`__version__` 仍从本模块可见
- 旧代码依赖的 `self.namer`、`self._css_rules`、`self._render_layer(...)`
  等通过 property/delegate 继续可用
"""

from typing import Any
import hashlib
import json
from pathlib import Path

from .version import __version__
from .escape import _esc  # noqa: F401  (re-export for backward compatibility)
from .naming import SimpleNamer  # noqa: F401
from .context import CodegenContext
from .html_builder import HtmlBuilder
from .layer_renderer import LayerRenderer


def _content_version(text: str) -> str:
    """内容指纹（md5 前 8 位），用作 ``?v=`` 缓存版本号。"""
    return hashlib.md5(text.encode('utf-8')).hexdigest()[:8]


class HTMLGenerator:
    """HTML 生成器。

    职责：编排 CodegenContext + LayerRenderer + HtmlBuilder 完成三件事：
      1) generate_html   —— 写入 index.html / style.css / main.js
      2) generate_metadata —— 写入 metadata.json
      3) generate_readme —— 写入 README.md
    """

    def __init__(self, psd_width: int, psd_height: int, output_dir: Path, psd_name: str):
        self.ctx = CodegenContext(
            psd_width=psd_width,
            psd_height=psd_height,
            output_dir=output_dir,
            psd_name=psd_name,
        )
        # 构造协作组件
        self._layer_renderer = LayerRenderer(self.ctx)
        self._html_builder = HtmlBuilder(self.ctx)

    # ------------------------------------------------------------------
    # 向后兼容的属性（旧代码可能直接访问 self.namer / self._css_rules 等）
    # ------------------------------------------------------------------

    @property
    def namer(self) -> SimpleNamer:
        return self.ctx.namer

    @property
    def psd_width(self) -> int:
        return self.ctx.psd_width

    @property
    def psd_height(self) -> int:
        return self.ctx.psd_height

    @property
    def output_dir(self) -> Path:
        return self.ctx.output_dir

    @property
    def psd_name(self) -> str:
        return self.ctx.psd_name

    @property
    def _css_rules(self) -> list[str]:
        return self.ctx.css_rules

    # Mixin-style delegation（兼容旧测试/脚本）
    def _render_layer(self, layer, indent=2, parent=None, siblings=None):
        return self._layer_renderer.render(layer, indent, parent, siblings)

    def _build_css(self) -> str:
        return self._html_builder.build_css()

    def _build_html(self, layers_html: str) -> str:
        return self._html_builder.build_html(layers_html)

    def _build_js(self) -> str:
        return self._html_builder.build_js()

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def generate_html(self, layers_tree: list[dict[str, Any]]) -> str:
        """生成 index.html + style.css + main.js，返回 html 文件路径"""
        self.ctx.reset()

        layers_html = ''
        for layer in layers_tree:
            layers_html += self._layer_renderer.render(
                layer, indent=2, parent=None, siblings=layers_tree,
            )

        # CSS / JS 各生成一次：内容既落盘，也用来算缓存版本号
        css_text = self._html_builder.build_css()
        js_text = self._html_builder.build_js()

        # 写入 style.css
        css_path = self.ctx.output_dir / 'style.css'
        css_path.write_text(css_text, encoding='utf-8')

        # 写入 main.js
        js_path = self.ctx.output_dir / 'main.js'
        js_path.write_text(js_text, encoding='utf-8')

        # 写入 index.html（CSS/JS 带内容指纹，避免浏览器复用上一轮的同名文件）
        html_path = self.ctx.output_dir / 'index.html'
        html_path.write_text(
            self._html_builder.build_html(
                layers_html,
                css_version=_content_version(css_text),
                js_version=_content_version(js_text),
            ),
            encoding='utf-8',
        )
        return str(html_path)

    def generate_metadata(
        self,
        layers_tree: list[dict[str, Any]],
        exported: int,
        skipped: int,
    ) -> None:
        """生成 metadata.json"""
        data = {
            'version': __version__,
            'psd_name': self.ctx.psd_name,
            'canvas': {'width': self.ctx.psd_width, 'height': self.ctx.psd_height},
            'stats': {'exported': exported, 'skipped': skipped, 'total': exported + skipped},
            'layers': layers_tree,
        }
        path = self.ctx.output_dir / 'metadata.json'
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')

    def generate_readme(self, exported: int, skipped: int) -> None:
        """生成 README.md"""
        md = f"""# {self.ctx.psd_name} — PSD2HTML

## 转换统计
- ✅ 成功导出: {exported} 个图层
- 🚫 已跳过: {skipped} 个图层
- 📐 画布尺寸: {self.ctx.psd_width} x {self.ctx.psd_height}

## 文件说明
```
{self.ctx.psd_name}/
├── index.html      # 主页面（保留完整层级结构）
├── style.css       # 样式表
├── main.js         # 脚本（多语言工具）
├── images/         # 图层图片（已去重）
├── metadata.json   # 元数据
└── README.md       # 本文件
```

## 文字图层
文字一律栅格化为图片（PNG 背景），HTML 中不保留可编辑文本节点；
对应 div 带 `data-type="text"` 标记（优化版会剥离到 `layer_map.json`），
用于区分"文字"与普通装饰图片。需要改文案时请替换 `images/` 下对应 PNG。

`main.js` 里的 `setLanguage()` 仅对页面中手动插入的 `[data-i18n-key]` 节点生效。

## 预览
```bash
python3 -m http.server 8000
# 访问 http://localhost:8000
```

---
**生成工具**: PSD2HTML v{__version__}
"""
        (self.ctx.output_dir / 'README.md').write_text(md, encoding='utf-8')
