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
import json
from pathlib import Path

from .version import __version__
from .escape import _esc  # noqa: F401  (re-export for backward compatibility)
from .naming import SimpleNamer  # noqa: F401
from .context import CodegenContext
from .html_builder import HtmlBuilder
from .layer_renderer import LayerRenderer


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

        # 写入 style.css
        css_path = self.ctx.output_dir / 'style.css'
        css_path.write_text(self._html_builder.build_css(), encoding='utf-8')

        # 写入 main.js
        js_path = self.ctx.output_dir / 'main.js'
        js_path.write_text(self._html_builder.build_js(), encoding='utf-8')

        # 写入 index.html
        html_path = self.ctx.output_dir / 'index.html'
        html_path.write_text(
            self._html_builder.build_html(layers_html), encoding='utf-8',
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

## 多语言使用
文本图层带有 `data-i18n-key` 属性，可用 JS 批量替换：

```javascript
const i18n = {{
  'zh': {{ '用户名': '用户名', '确认': '确认' }},
  'en': {{ '用户名': 'Username', '确认': 'Confirm' }},
}};

function setLang(lang) {{
  document.querySelectorAll('[data-i18n-key]').forEach(el => {{
    const key = el.getAttribute('data-i18n-key');
    if (i18n[lang] && i18n[lang][key]) {{
      el.textContent = i18n[lang][key];
    }}
  }});
}}
```

## 预览
```bash
python3 -m http.server 8000
# 访问 http://localhost:8000
```

---
**生成工具**: PSD2HTML v{__version__}
"""
        (self.ctx.output_dir / 'README.md').write_text(md, encoding='utf-8')
