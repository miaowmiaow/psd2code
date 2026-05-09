# -*- coding: utf-8 -*-
"""React target 专属 Stages。

两个 Stage：
  * :class:`HtmlToReactStage`  —— 把 HTML target 产出的 index_optimized.html +
    style_optimized.css 转成 JSX（App.jsx）+ CSS（App.css），并复制图片资源。
  * :class:`ReactScaffoldStage` —— 生成 Vite 项目脚手架（package.json、
    vite.config.js、index.html、main.jsx、README.md）。

两个 Stage 分开是为了让「转换」与「脚手架」两个职责可以单独替换：
未来如果切到 Next.js 或 CRA，只要替换 ReactScaffoldStage 即可。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from framework import PipelineContext, Stage

from .codegen import css_to_module, html_to_jsx


# ---------------------------------------------------------------------------
# Stage 5: HTML -> React
# ---------------------------------------------------------------------------

class HtmlToReactStage(Stage):
    """Consume optimized HTML/CSS and emit JSX + CSS + images for a React project.

    依赖的 artifacts：
      * ``html_path``：由 :class:`LayoutOptimizeStage` 写入的优化 HTML 路径。
        若 LayoutOptimizeStage 回退（未生成 optimized），则仍会指向原始 index.html。
      * ``output_dir``：HTML 中间产物子目录（如 ``output/<psd>/html/``）。
      * ``project_root``：项目根（如 ``output/<psd>/``），由 LoadPsdStage 回填。

    产出的 artifacts：
      * ``react_dir``：React 项目目录（``<project_root>/react`` —— 即与 ``html/``
        同级的 ``react/``，而不是 ``html/react/``，保证两份产物结构上互相独立）。
      * ``react_jsx_path``：``<react_dir>/src/App.jsx``。
      * ``react_css_path``：``<react_dir>/src/App.css``。
    """

    name = "html_to_react"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        assert ctx.output_dir is not None, "output_dir must be set"

        html_path = ctx.get("html_path")
        if not html_path:
            ctx.log("html_to_react: skipped (no html_path)")
            return ctx

        html_path = Path(html_path)
        css_path = self._resolve_css_path(html_path)

        if not html_path.exists() or not css_path.exists():
            ctx.log(f"html_to_react: skipped (missing {html_path} or {css_path})")
            return ctx

        html_content = html_path.read_text(encoding="utf-8")
        css_content = css_path.read_text(encoding="utf-8")

        # 1) 转换
        jsx_result = html_to_jsx(html_content)
        css_result = css_to_module(css_content)

        # 2) 目标目录：react/ 与 html/ 同级（基于 project_root 定位，不再用 output_dir.parent）
        project_root = ctx.project_root or Path(ctx.output_dir).parent
        react_dir = project_root / "react"
        src_dir = react_dir / "src"
        assets_img_dir = src_dir / "assets" / "images"
        # 清理历史内容，避免旧图/旧代码残留
        if react_dir.exists():
            shutil.rmtree(react_dir)
        src_dir.mkdir(parents=True, exist_ok=True)
        assets_img_dir.mkdir(parents=True, exist_ok=True)

        # 3) 写入 App.jsx / App.css
        jsx_path = src_dir / "App.jsx"
        css_out_path = src_dir / "App.css"

        # 画布尺寸（用于包裹容器/根 div 的 width/height）
        psd = ctx.psd
        canvas_w = getattr(psd, "width", None) if psd is not None else None
        canvas_h = getattr(psd, "height", None) if psd is not None else None

        jsx_path.write_text(
            _render_app_jsx(jsx_result.jsx, canvas_w, canvas_h),
            encoding="utf-8",
        )
        css_out_path.write_text(css_result.css, encoding="utf-8")

        # 4) 复制/同步 images/
        src_images = Path(ctx.output_dir) / "images"
        if src_images.exists():
            self._copy_images(src_images, assets_img_dir)

        ctx.set("react_dir", str(react_dir))
        ctx.set("react_jsx_path", str(jsx_path))
        ctx.set("react_css_path", str(css_out_path))
        ctx.log(
            f"react: wrote App.jsx ({len(jsx_result.jsx)} chars), "
            f"App.css ({len(css_result.css)} chars), "
            f"images refs={len(jsx_result.image_refs | css_result.image_refs)}"
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
# Stage 6: React scaffold
# ---------------------------------------------------------------------------

class ReactScaffoldStage(Stage):
    """Generate a minimal Vite + React project skeleton around App.jsx."""

    name = "react_scaffold"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        react_dir = ctx.get("react_dir")
        if not react_dir:
            ctx.log("react_scaffold: skipped (no react_dir)")
            return ctx

        react_dir = Path(react_dir)
        src_dir = react_dir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)

        psd_name = ctx.psd_path.stem
        pkg_name = _sanitize_npm_name(psd_name) or "psd-react-app"

        # main.jsx
        (src_dir / "main.jsx").write_text(_MAIN_JSX, encoding="utf-8")

        # index.html (Vite template)
        (react_dir / "index.html").write_text(
            _INDEX_HTML.format(title=psd_name),
            encoding="utf-8",
        )

        # package.json
        (react_dir / "package.json").write_text(
            _PACKAGE_JSON.format(name=pkg_name),
            encoding="utf-8",
        )

        # vite.config.js
        (react_dir / "vite.config.js").write_text(_VITE_CONFIG, encoding="utf-8")

        # .gitignore
        (react_dir / ".gitignore").write_text(_GITIGNORE, encoding="utf-8")

        # README
        (react_dir / "README.md").write_text(
            _README.format(psd_name=psd_name, pkg_name=pkg_name),
            encoding="utf-8",
        )

        ctx.log(f"react: scaffolded Vite project at {react_dir}")
        # 把 ctx.output_dir 切到 react/，让 CLI / 下游观察到的 "target 主产物"
        # 反映 react target 的最终交付目录，而不是残留在中间的 html/ 上。
        ctx.output_dir = react_dir
        return ctx


# ---------------------------------------------------------------------------
# Scaffold templates
# ---------------------------------------------------------------------------

def _render_app_jsx(jsx_body: str, canvas_w: int | None, canvas_h: int | None) -> str:
    """Wrap the converted JSX body in an App component."""
    # jsx_body 已经是 <div id="canvas"> ... </div>（由 html_to_jsx 直接保留根节点），
    # 我们不再额外包一层，避免破坏现有 #canvas 的 CSS 规则。
    return (
        "// Auto-generated by psd2code (react target)\n"
        "// Do not edit by hand; re-run psd_to_code.py --target react to regenerate.\n"
        "import './App.css';\n"
        "\n"
        "export default function App() {\n"
        "  return (\n"
        "    <>\n"
        f"{_reindent(jsx_body, 6)}"
        "    </>\n"
        "  );\n"
        "}\n"
    )


def _reindent(text: str, spaces: int) -> str:
    pad = " " * spaces
    lines = text.splitlines()
    out = []
    for ln in lines:
        if ln.strip() == "":
            out.append("")
        else:
            out.append(pad + ln)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


_MAIN_JSX = """\
// Auto-generated by psd2code (react target)
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
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
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
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
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  }},
  "devDependencies": {{
    "@vitejs/plugin-react": "^4.3.1",
    "vite": "^5.4.0"
  }}
}}
"""


_VITE_CONFIG = """\
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
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
# {psd_name} — React 版本

本目录由 `psd2code` 的 `react` target 自动生成。

## 快速开始

```bash
cd react
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
└── react/               # 本目录
    ├── index.html       # Vite 模板
    ├── package.json
    ├── vite.config.js
    └── src/
        ├── main.jsx     # 入口：挂载 <App />
        ├── App.jsx      # 自动生成：PSD → JSX（根节点为 #canvas）
        ├── App.css      # 自动生成：全局 CSS（图片路径已改写到 ./assets/images/）
        └── assets/images/   # 自动复制：来自 ../../html/images/
```

## 重新生成

```bash
python3 psd_to_code.py path/to/your.psd --target react
```

> 注意：App.jsx / App.css / assets/images/ 会被覆盖。
> 自定义逻辑请在 App.jsx 之外的文件中编写，或 fork 目录保留副本。

## 设计说明

- 结构与 HTML target 完全一致：根节点为 ``<div id="canvas">``，
  所有图层以 BEM 类名 + 绝对定位排布。
- 未使用 CSS Module，因为 HTML target 已保证类名全局唯一，
  并且样式表大量使用属性选择器（如 ``[class*="__image"]``），
  这类选择器与 CSS Module 的默认哈希机制不兼容。
- 重复组 / 列表的展开已在 HTML target 阶段完成，JSX 中都是实例化后的节点。

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
    # 折叠连续的 '-'，修剪首尾
    out = "".join(buf)
    while "--" in out:
        out = out.replace("--", "-")
    out = out.strip("-_")
    return out or "app"
