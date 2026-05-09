# -*- coding: utf-8 -*-
"""Vue target 专属 Stages。

两个 Stage：
  * :class:`HtmlToVueStage`   —— 把 HTML target 产出的 index_optimized.html +
    style_optimized.css 转成 SFC（App.vue），并复制图片资源到 ``vue/src/assets/images/``。
  * :class:`VueScaffoldStage` —— 生成 Vite 项目脚手架（package.json、
    vite.config.js、index.html、main.js、README.md）。

两个 Stage 分开是为了让「转换」与「脚手架」两个职责可以单独替换：
未来如果切到 Nuxt 或 vite-plugin-vue2，只需替换 VueScaffoldStage 即可。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from framework import PipelineContext, Stage

from .codegen import html_to_vue_template, rewrite_css


# ---------------------------------------------------------------------------
# Stage 5: HTML -> Vue
# ---------------------------------------------------------------------------

class HtmlToVueStage(Stage):
    """Consume optimized HTML/CSS and emit App.vue + images for a Vue project.

    依赖的 artifacts：
      * ``html_path``：由 :class:`LayoutOptimizeStage` 写入的优化 HTML 路径。
        若 LayoutOptimizeStage 回退（未生成 optimized），则仍会指向原始 index.html。
      * ``output_dir``：HTML 中间产物子目录（如 ``output/<psd>/html/``）。
      * ``project_root``：项目根（如 ``output/<psd>/``），由 LoadPsdStage 回填。

    产出的 artifacts：
      * ``vue_dir``：Vue 项目目录（``<project_root>/vue`` —— 即与 ``html/``
        同级的 ``vue/``，而不是 ``html/vue/``，保证两份产物结构上互相独立）。
      * ``vue_sfc_path``：``<vue_dir>/src/App.vue``。
    """

    name = "html_to_vue"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        assert ctx.output_dir is not None, "output_dir must be set"

        html_path = ctx.get("html_path")
        if not html_path:
            ctx.log("html_to_vue: skipped (no html_path)")
            return ctx

        html_path = Path(html_path)
        css_path = self._resolve_css_path(html_path)

        if not html_path.exists() or not css_path.exists():
            ctx.log(f"html_to_vue: skipped (missing {html_path} or {css_path})")
            return ctx

        html_content = html_path.read_text(encoding="utf-8")
        css_content = css_path.read_text(encoding="utf-8")

        # 1) 转换
        tpl_result = html_to_vue_template(html_content)
        css_result = rewrite_css(css_content)

        # 2) 目标目录：vue/ 与 html/ 同级（基于 project_root 定位）
        project_root = ctx.project_root or Path(ctx.output_dir).parent
        vue_dir = project_root / "vue"
        src_dir = vue_dir / "src"
        assets_img_dir = src_dir / "assets" / "images"
        # 清理历史内容，避免旧图/旧代码残留
        if vue_dir.exists():
            shutil.rmtree(vue_dir)
        src_dir.mkdir(parents=True, exist_ok=True)
        assets_img_dir.mkdir(parents=True, exist_ok=True)

        # 3) 写入 App.vue
        sfc_path = src_dir / "App.vue"
        sfc_path.write_text(
            _render_app_sfc(tpl_result.template, css_result.css),
            encoding="utf-8",
        )

        # 4) 复制/同步 images/
        src_images = Path(ctx.output_dir) / "images"
        if src_images.exists():
            self._copy_images(src_images, assets_img_dir)

        ctx.set("vue_dir", str(vue_dir))
        ctx.set("vue_sfc_path", str(sfc_path))
        ctx.log(
            f"vue: wrote App.vue (template={len(tpl_result.template)} chars, "
            f"style={len(css_result.css)} chars), "
            f"images refs={len(tpl_result.image_refs | css_result.image_refs)}"
        )
        return ctx

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_css_path(html_path: Path) -> Path:
        """Pick the CSS file matching the given HTML file."""
        if html_path.name.endswith("_optimized.html"):
            return html_path.with_name("style_optimized.css")
        return html_path.with_name("style.css")

    @staticmethod
    def _copy_images(src_dir: Path, dst_dir: Path) -> None:
        """Copy all images from ``src_dir`` into ``dst_dir`` (flat)."""
        for p in src_dir.iterdir():
            if p.is_file():
                shutil.copy2(p, dst_dir / p.name)


# ---------------------------------------------------------------------------
# Stage 6: Vue scaffold
# ---------------------------------------------------------------------------

class VueScaffoldStage(Stage):
    """Generate a minimal Vite + Vue 3 project skeleton around App.vue."""

    name = "vue_scaffold"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        vue_dir = ctx.get("vue_dir")
        if not vue_dir:
            ctx.log("vue_scaffold: skipped (no vue_dir)")
            return ctx

        vue_dir = Path(vue_dir)
        src_dir = vue_dir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)

        psd_name = ctx.psd_path.stem
        pkg_name = _sanitize_npm_name(psd_name) or "psd-vue-app"

        # main.js
        (src_dir / "main.js").write_text(_MAIN_JS, encoding="utf-8")

        # index.html (Vite template)
        (vue_dir / "index.html").write_text(
            _INDEX_HTML.format(title=psd_name),
            encoding="utf-8",
        )

        # package.json
        (vue_dir / "package.json").write_text(
            _PACKAGE_JSON.format(name=pkg_name),
            encoding="utf-8",
        )

        # vite.config.js
        (vue_dir / "vite.config.js").write_text(_VITE_CONFIG, encoding="utf-8")

        # .gitignore
        (vue_dir / ".gitignore").write_text(_GITIGNORE, encoding="utf-8")

        # README
        (vue_dir / "README.md").write_text(
            _README.format(psd_name=psd_name, pkg_name=pkg_name),
            encoding="utf-8",
        )

        ctx.log(f"vue: scaffolded Vite project at {vue_dir}")
        # 把 ctx.output_dir 切到 vue/，让 CLI / 下游观察到的 "target 主产物"
        # 反映 vue target 的最终交付目录，而不是残留在中间的 html/ 上。
        ctx.output_dir = vue_dir
        return ctx


# ---------------------------------------------------------------------------
# Scaffold templates
# ---------------------------------------------------------------------------

def _render_app_sfc(template_body: str, css_body: str) -> str:
    """Wrap the converted template + CSS into an App.vue SFC.

    使用 ``<script setup>`` 但留空（无业务逻辑），保留为后续扩展点。
    ``<style>`` 不加 ``scoped``，因为 HTML target 已通过 BEM 类名保证全局唯一，
    而且 CSS 大量使用属性选择器（``[class*="__image"]``），scoped 会破坏其匹配。
    """
    return (
        "<!-- Auto-generated by psd2code (vue target) -->\n"
        "<!-- Do not edit by hand; re-run psd_to_code.py --target vue to regenerate. -->\n"
        "<template>\n"
        f"{template_body}"
        "</template>\n"
        "\n"
        "<script setup>\n"
        "// 自动生成的 SFC。如需添加交互逻辑，请在此或派生组件中实现。\n"
        "</script>\n"
        "\n"
        "<style>\n"
        f"{css_body}"
        "</style>\n"
    )


_MAIN_JS = """\
// Auto-generated by psd2code (vue target)
import { createApp } from 'vue';
import App from './App.vue';

createApp(App).mount('#app');
"""


_INDEX_HTML = """\
<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no" />
    <title>{title}</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.js"></script>
  </body>
</html>
"""


_PACKAGE_JSON = """\
{{
  "name": "{name}",
  "private": true,
  "version": "0.0.1",
  "type": "module",
  "scripts": {{
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  }},
  "dependencies": {{
    "vue": "^3.4.0"
  }},
  "devDependencies": {{
    "@vitejs/plugin-vue": "^5.0.0",
    "vite": "^5.4.0"
  }}
}}
"""


_VITE_CONFIG = """\
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
  },
});
"""


_GITIGNORE = """\
node_modules
dist
.DS_Store
*.log
.vite
"""


_README = """\
# {psd_name} — Vue 版本

本目录由 `psd2code` 的 `vue` target 自动生成。

## 快速开始

```bash
cd vue
npm install        # 或 pnpm install / yarn
npm run dev        # 本地预览
npm run build      # 生产构建，产物在 dist/
```

## 目录结构

```
output/<psd>/
├── html/                # HTML 版本（由 psd2code 同一次调用生成，供对照/降级使用）
│   ├── index.html
│   ├── style.css
│   ├── index_optimized.html
│   ├── style_optimized.css
│   └── images/
└── vue/                 # 本目录
    ├── index.html       # Vite 模板
    ├── package.json
    ├── vite.config.js
    └── src/
        ├── main.js      # 入口：挂载 <App />
        ├── App.vue      # 自动生成：PSD → SFC（template + style 全局）
        └── assets/images/   # 自动复制：来自 ../../html/images/
```

## 重新生成

```bash
python3 psd_to_code.py path/to/your.psd --target vue
```

> 注意：App.vue / assets/images/ 会被覆盖。
> 自定义逻辑请在 App.vue 之外的文件中编写，或 fork 目录保留副本。

## 设计说明

- 结构与 HTML target 完全一致：根节点为 ``<div id="canvas">``，
  所有图层以 BEM 类名 + 绝对定位排布。
- ``<style>`` **未加 ``scoped``**，因为 HTML target 已保证类名全局唯一，
  并且样式表大量使用属性选择器（如 ``[class*="__image"]``），scoped 会破坏匹配。
- ``<script setup>`` 留空，作为后续接入交互的扩展点。
- 重复组 / 列表的展开已在 HTML target 阶段完成，模板中都是实例化后的节点。

（由 psd2code 自动生成）
"""


def _sanitize_npm_name(name: str) -> str:
    """Turn an arbitrary string into a valid npm package name fragment.

    npm 包名规范要求仅允许 ASCII 小写字母、数字、``-``、``_``、``.``。
    非 ASCII 字符（如中文）会被替换为 ``-``，并折叠连续分隔符。
    """
    lowered = name.lower()
    buf = []
    for ch in lowered:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch in "-_":
            buf.append(ch)
        else:
            buf.append("-")
    out = "".join(buf)
    while "--" in out:
        out = out.replace("--", "-")
    out = out.strip("-_")
    return out or "app"
