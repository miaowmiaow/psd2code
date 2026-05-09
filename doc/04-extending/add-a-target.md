# 扩展：新增一个 Target

> 例：把同一 PSD 输出到 **Vue 单文件组件**（`--target vue`）。
>
> 已实装的两个参考目标：
> - `targets/html/` —— **"直接从 IR 生成"** 的模板（完整走一遍 Stage 链）
> - `targets/react/` —— **"基于 HTML 产物二次加工"** 的模板（复用前 4 段 Stage）
>
> 新 target 可从中任选一种模式。

---

## 两种典型模式

### 模式 A：直接从 IR 生成（如 HTML target）

适用：目标格式与 HTML 差异较大，需要完全自定义渲染。
代价：要从头实现所有布局、效果、资产处理。

### 模式 B：基于 HTML 产物二次加工（如 React target）

适用：目标格式与 HTML 共享布局思路（绝对定位 / 类名 / 图片 `src`），
只是语法糖不同（JSX / Vue SFC / 小程序 WXML）。
收益：**零成本**继承 HTML target 的所有改进（新效果、新布局规则）。
代价：受限于 HTML 的表达能力；极端定制化需求难以做。

> 大多数前端框架（React / Vue / Solid / Svelte / 小程序）都建议走 **模式 B**。

---

## 以 Vue 为例（模式 B，推荐）

## 目标

```bash
python3 psd_to_code.py input.psd --target vue
# 产物：output/<psd_stem>/vue/   （Vite + Vue 3 项目）
```

## 步骤

### 1. 新建目录

```
scripts/targets/vue/
├── __init__.py          # from . import target （触发注册）
├── target.py            # @register("vue") class VueTarget
├── pipeline.py          # 复用 html 前 4 段 + 新增 2 段
├── stages.py            # HtmlToVueStage + VueScaffoldStage
└── codegen/
    ├── __init__.py
    ├── html_to_template.py   # HTML → <template> 片段
    └── css_rewrite.py        # CSS url() 改写（images → assets/images）
```

### 2. 实装 Target

`scripts/targets/vue/target.py`:

```python
from framework import Pipeline, PipelineContext
from targets.base import Target
from targets.registry import register
from .pipeline import build_vue_pipeline


@register("vue")
class VueTarget(Target):
    def build_pipeline(self, ctx: PipelineContext) -> Pipeline:
        return build_vue_pipeline(ctx)
```

`scripts/targets/vue/__init__.py`:

```python
from . import target  # 触发 @register 注册
__all__ = ["target"]
```

### 3. 实装 Pipeline（复用 HTML 前 4 段）

`scripts/targets/vue/pipeline.py`:

```python
from framework import Pipeline, PipelineContext
from targets.html.pipeline import (
    LoadPsdStage, ParseToIrStage, HtmlCodegenStage, LayoutOptimizeStage,
)
from .stages import HtmlToVueStage, VueScaffoldStage


def build_vue_pipeline(ctx: PipelineContext) -> Pipeline:
    return Pipeline([
        LoadPsdStage(),
        ParseToIrStage(),
        HtmlCodegenStage(),
        LayoutOptimizeStage(),
        HtmlToVueStage(),
        VueScaffoldStage(),
    ])
```

> **硬约束**：直接 `from targets.html.pipeline import ...` 复用，**不要**
> 把 Stage 类复制一份再改。否则未来 HTML target 的改进无法自动惠及你。

### 4. 实装转换 Stage

`scripts/targets/vue/stages.py`:

```python
from framework import Stage
from pathlib import Path
import shutil
from .codegen.html_to_template import html_to_vue_template
from .codegen.css_rewrite import rewrite_css


class HtmlToVueStage(Stage):
    name = "html_to_vue"

    def run(self, ctx):
        html_path = ctx.get("html_path")
        if not html_path: return ctx
        html_path = Path(html_path)
        css_path = html_path.with_name(
            html_path.name.replace("_optimized.html", "_optimized.css")
            if "_optimized" in html_path.name else "style.css"
        )

        template = html_to_vue_template(html_path.read_text("utf-8"))
        css = rewrite_css(css_path.read_text("utf-8"))

        vue_dir = Path(ctx.output_dir) / "vue"
        src = vue_dir / "src"
        src.mkdir(parents=True, exist_ok=True)
        (src / "App.vue").write_text(
            f'<template>\n{template}\n</template>\n\n'
            f'<style>\n{css}\n</style>\n',
            encoding="utf-8",
        )
        # 复制 images
        ...
        ctx.set("vue_dir", str(vue_dir))
        return ctx


class VueScaffoldStage(Stage):
    name = "vue_scaffold"

    def run(self, ctx):
        # 写 package.json / vite.config.js / index.html / main.js
        ...
        return ctx
```

> Vue 的 `<template>` 与 HTML 语法高度一致（`class` 不变、`<img>` 可
> 不自闭合），所以 `html_to_template` 比 React 的 `html_to_jsx` **简单得多**。
> 大多数情况只需要拷贝 `#canvas` 子树字符串 + 做一遍图片路径改写即可。

### 5. 入口触发注册

`psd_to_code.py`:

```python
from targets import html as _html_target  # noqa
from targets import react as _react_target  # noqa
from targets import vue as _vue_target  # noqa  ← 新增
```

### 6. 回归验证

- `python3 psd_to_code.py sample.psd --target html`  → 与旧 baseline diff 零差异
- `python3 psd_to_code.py sample.psd --target react` → 不受影响（静态检查 + 抽样运行）
- `python3 psd_to_code.py sample.psd --target vue`   → 产物能 `npm run dev` 启动

### 7. 更新文档

- 在 `doc/README.md` 的"5 分钟上手"里加 `--target vue` 示例。
- 在 `SKILL.md` 的触发词里加 "psd 转 vue"。
- 新增 `doc/02-modules/targets-vue.md`，格式参考 `targets-react.md`。

---

## 模式 A（直接从 IR 生成）骨架

仅当你需要完全跳开 HTML 时使用。例如输出为 Figma JSON、PDF、或
其他与 HTML 差异巨大的格式。

```python
class CustomCodegenStage(Stage):
    name = "custom_codegen"
    def run(self, ctx):
        from .codegen.generator import CustomGenerator
        gen = CustomGenerator(ctx.ir, ctx.output_dir, ctx.psd_path.stem)
        out_path = gen.generate()
        ctx.set("custom_path", out_path)
        return ctx


def build_custom_pipeline(ctx):
    return Pipeline([
        LoadPsdStage(),
        ParseToIrStage(),   # 仍然复用
        CustomCodegenStage(),
    ])
```

**必读：** 尽量从 IR 的一等字段取数据；只有 IR 没有的字段才去
`node.meta['legacy']` 兜底（并在 PR 里记录，作为后续 IR 提升的候选）。

## Checklist

- [ ] 目录结构落位（参考 `targets/react/` 或 `targets/html/`）
- [ ] `@register("<name>")` 装饰器就位
- [ ] `targets/<name>/__init__.py` 通过 `from . import target` 触发注册
- [ ] **模式 B**：`from targets.html.pipeline import ...` 复用前 4 段 Stage
- [ ] **模式 A**：Codegen 主读 IR，只在必要时用 `meta['legacy']`
- [ ] 入口 `psd_to_code.py` 加一行 import 触发注册
- [ ] baseline diff 通过（`--target html` 不受影响）
- [ ] 新产物手动验证可运行（`npm run dev` 能跑起来）
- [ ] 文档：`doc/02-modules/targets-<name>.md` + README 索引
