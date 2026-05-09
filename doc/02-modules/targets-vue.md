# `targets/vue/` 模块详解

> **本文解决什么**：说明 Vue target 的实现策略、目录结构、产物形态、
> 与 HTML target 的复用关系，以及对接细节。
> **不讨论什么**：PSD 解析、IR 定义、图片导出（见 `core/*` 相关文档）；
> 基础设计模式（见 `01-architecture/design-patterns.md`）。

---

## 一句话概括

Vue target **不独立实现**「IR → Vue」，而是在 HTML target 产物之上做
**「HTML → SFC `<template>` + CSS 改写」+「Vite 脚手架」** 两步加工。

原因：
- HTML target 已经完成了布局、效果、资产、布局优化等所有重活。
- Vue 的 `<template>` 与 HTML 语法**几乎等同**（`class` 不变、`<img>` 自闭合即可、
  注释格式相同），转换器比 React 的 `html_to_jsx` 简单得多。
- 把 Vue target 做成「HTML 的后置产物」，可以**零成本共享**未来 HTML target
  的所有改进（新效果、新布局规则等），避免多条线并行维护。

---

## 目录结构

```
scripts/targets/vue/
├── __init__.py                  # 触发 VueTarget 注册
├── target.py                    # @register("vue") class VueTarget
├── pipeline.py                  # build_vue_pipeline()
├── stages.py                    # HtmlToVueStage + VueScaffoldStage
└── codegen/
    ├── __init__.py
    ├── html_to_template.py     # BeautifulSoup 驱动的 HTML → <template>
    └── css_rewrite.py          # CSS url() 改写（类名保持不变）
```

## Stage 链

完全复用 HTML target 的前 4 段，尾部追加 2 段 Vue 专属 Stage：

```
LoadPsdStage(subdir_name="html") ← 复用 targets.html.pipeline，HTML 中间产物落 html/
ParseToIrStage                    ← 复用 targets.html.pipeline
HtmlCodegenStage                  ← 复用 targets.html.pipeline
LayoutOptimizeStage               ← 复用 targets.html.pipeline
HtmlToVueStage                    ← 本 target 新增
VueScaffoldStage                  ← 本 target 新增
```

复用代码 = `targets/vue/pipeline.py`：

```python
from targets.html.pipeline import (
    LoadPsdStage, ParseToIrStage, HtmlCodegenStage, LayoutOptimizeStage,
)
from .stages import HtmlToVueStage, VueScaffoldStage


def build_vue_pipeline(ctx):
    return Pipeline([
        LoadPsdStage(subdir_name="html"),
        ParseToIrStage(),
        HtmlCodegenStage(),
        LayoutOptimizeStage(),
        HtmlToVueStage(),
        VueScaffoldStage(),
    ])
```

> **硬约束**：`HtmlToVueStage` 之前的 4 段 **必须** 与 HTML target 完全一致。
> 不要复制一份再魔改，直接 `from targets.html.pipeline import ...` 即可。

## 产物形态

```
<output>/<psd_stem>/
├── html/                          # ← HTML 中间产物（与 React target 一致的命名习惯）
│   ├── images/
│   ├── index.html
│   ├── style.css
│   ├── index_optimized.html
│   ├── style_optimized.css
│   ├── main.js
│   ├── metadata.json
│   └── README.md
└── vue/                           # ← Vue target 新增
    ├── index.html                 # Vite 模板（挂载 #app）
    ├── package.json               # vue + vite + @vitejs/plugin-vue
    ├── vite.config.js
    ├── .gitignore
    ├── README.md                  # Vue 版本使用说明
    └── src/
        ├── main.js                # createApp(App).mount('#app')
        ├── App.vue                # 自动生成：<template>+<script setup>+<style>
        └── assets/images/         # 由 ../../html/images/ 复制
```

> `vue/` 是可独立发布的 Vite 项目：`cd vue && npm i && npm run dev` 即可预览。

## 关键实现

### 1. `codegen/html_to_template.py` — HTML → `<template>`

使用 BeautifulSoup 解析 HTML，递归重写成模板字符串。

**与 JSX 的关键差异**：

| 维度 | React (JSX) | Vue (`<template>`) |
| ---- | ----------- | ------------------ |
| 类属性 | `className` | `class`（**保持原名**） |
| void 元素 | 必须自闭合 `<img />` | 强制写自闭合 `<img />`（避免编译警告） |
| 数据属性 | `data-*` / `aria-*` 保留 | 同 |
| 文本花括号 | `{` → `{'{'}` | `{{` → `{ {`（避免被识别为插值） |
| 注释 | `<!-- -->` → `{/* */}` | `<!-- -->`（**原样保留**） |

**图片路径**：`src="images/xxx.png"` → `src="./assets/images/xxx.png"`，
同时把 `images/xxx.png` 记入 `VueTemplateResult.image_refs`。

### 2. `codegen/css_rewrite.py` — CSS 改写

只做一件事：

```
url("images/xxx.png")  →  url("./assets/images/xxx.png")
```

顺带收集所有 `.xxx` 类名与图片引用到 `CssRewriteResult`。

### 3. 为什么 `<style>` 不加 `scoped`？

理由与 React target「不用 CSS Module」相同：
1. HTML target 生成的类名按 BEM 规则产出（如 `section-foo__image`），
   **已经全局唯一**，scoped 提供的隔离价值很小。
2. 样式表中大量使用属性选择器（`[class*="__image"]`、`[class$="-container"]`），
   scoped 会给所有元素加 `[data-v-xxx]` 哈希，**改变实际选择器结构**，
   极易导致部分规则失配。
3. `<script setup>` 留空，无业务逻辑泄漏，全局类名不会污染父组件。

如果未来真的需要隔离，可在 `VueScaffoldStage` 派生分支里把 `<style>`
改成 `<style scoped>`，并同步在转换前对 CSS 做选择器降级处理。

### 4. `stages.HtmlToVueStage`

职责：
1. 读取 `ctx.get("html_path")`（由 LayoutOptimizeStage 写入）。
2. 根据 HTML 文件名匹配对应 CSS（`index_optimized.html` ↔
   `style_optimized.css`；回退到 `index.html` ↔ `style.css`）。
3. 调用 `html_to_vue_template` / `rewrite_css`。
4. 写 `vue/src/App.vue`（拼成完整 SFC：template + script setup + style）。
5. 把 `<output>/html/images/` 复制到 `vue/src/assets/images/`（flat 拷贝）。
6. artifact：`vue_dir` / `vue_sfc_path`。

### 5. `stages.VueScaffoldStage`

模板化写入：
- `package.json`（包名由 `_sanitize_npm_name(psd_stem)` 派生，依赖 `vue@^3.4`）
- `vite.config.js`（仅 `@vitejs/plugin-vue`，`server.port=5173`）
- `index.html`（Vite 模板，`<div id="app">`）
- `src/main.js`（`createApp(App).mount('#app')`）
- `.gitignore` / `README.md`

> **为什么拆成两个 Stage？**
> 将来如果切到 Nuxt / vite-plugin-vue2，只需要替换 `VueScaffoldStage`，
> `HtmlToVueStage` 不动。

## artifact 列表

| Key | 来源 | 含义 |
| --- | --- | --- |
| `html_path` | `LayoutOptimizeStage` | 优化后的 HTML 路径（输入给本 target） |
| `vue_dir` | `HtmlToVueStage` | Vue 项目根目录 |
| `vue_sfc_path` | `HtmlToVueStage` | App.vue 绝对路径 |

## 运行

```bash
python3 psd_to_code.py path/to/file.psd --target vue
```

首次使用 Vue 产物：

```bash
cd output/<psd_stem>/vue
npm install
npm run dev     # http://localhost:5173
npm run build   # 产物在 dist/
```

## 扩展点

### 需要把模板拆成多个组件怎么办？

当前 App.vue 是**一个大 SFC**（与 HTML target 的"一个大画布"同构）。
如果要拆分，建议在 `HtmlToVueStage` 后面再加一个 `SplitComponentsStage`：
读 IR 中的 group 树，为每个命名组单独生成一个 `<GroupName />.vue`，
App.vue 里做组合。

关键：**不要动前 4 段 Stage**；这是与 HTML / React target 共享的基础设施。

### 需要 TypeScript 版本怎么办？

在 `VueScaffoldStage` 里根据 `ctx.get("vue_ts")` flag 切换：
`package.json` 加 `typescript` / `vue-tsc`，`<script setup>` 改为
`<script setup lang="ts">`，模板转换本身不需要改动。

### 需要 Vue 2 版本怎么办？

复制 `VueTarget`，改名 `Vue2Target`，复用 `HtmlToVueStage`（模板语法兼容），
替换 `VueScaffoldStage`：依赖 `vue@^2.7` + `@vitejs/plugin-vue2`，
`main.js` 改为 `new Vue({ render: h => h(App) }).$mount('#app')`。

### 需要生成 Nuxt 项目怎么办？

复制 `VueTarget`，改名 `NuxtTarget`，复用 `HtmlToVueStage`，替换
`VueScaffoldStage` 为 `NuxtScaffoldStage`（写 `pages/index.vue` 而不是
`src/App.vue`，加 `nuxt.config.ts`）。

## 相关文档

- 如何新增 target：[`../04-extending/add-a-target.md`](../04-extending/add-a-target.md)
- 如何新增 Stage：[`../04-extending/add-a-stage.md`](../04-extending/add-a-stage.md)
- HTML target 细节（本 target 的基础）：[`./targets-html.md`](./targets-html.md)
- React target（同模式参考）：[`./targets-react.md`](./targets-react.md)
- 布局优化器（Vue 也完全依赖其产物）：[`../03-topics/layout-optimizer.md`](../03-topics/layout-optimizer.md)
