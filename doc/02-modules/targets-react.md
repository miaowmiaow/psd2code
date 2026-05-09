# `targets/react/` 模块详解

> **本文解决什么**：说明 React target 的实现策略、目录结构、产物形态、
> 与 HTML target 的复用关系，以及对接细节。
> **不讨论什么**：PSD 解析、IR 定义、图片导出（见 `core/*` 相关文档）；
> 基础设计模式（见 `01-architecture/design-patterns.md`）。

---

## 一句话概括

React target **不独立实现**「IR → React」，而是在 HTML target 产物之上做
**「HTML → JSX + CSS 改写」+「Vite 脚手架」** 两步加工。

原因：
- HTML target 已经完成了布局、效果、资产、布局优化等所有重活。
- JSX 与 HTML 的差异仅在**属性名与语法糖**层面（`class` → `className`、
  自闭合、属性值表达式）。
- 把 React target 做成「HTML 的后置产物」，可以**零成本共享**未来 HTML target
  的所有改进（新的效果渲染、新的布局规则等），避免两条线并行维护。

---

## 目录结构

```
scripts/targets/react/
├── __init__.py                  # 触发 ReactTarget 注册
├── target.py                    # @register("react") class ReactTarget
├── pipeline.py                  # build_react_pipeline()
├── stages.py                    # HtmlToReactStage + ReactScaffoldStage
└── codegen/
    ├── __init__.py
    ├── html_to_jsx.py           # BeautifulSoup 驱动的 HTML → JSX
    └── css_to_module.py         # CSS url() 改写（类名保持不变）
```

## Stage 链

完全复用 HTML target 的前 4 段，尾部追加 2 段 React 专属 Stage：

```
LoadPsdStage          ← 复用 targets.html.pipeline
ParseToIrStage        ← 复用 targets.html.pipeline
HtmlCodegenStage      ← 复用 targets.html.pipeline
LayoutOptimizeStage   ← 复用 targets.html.pipeline
HtmlToReactStage      ← 本 target 新增
ReactScaffoldStage    ← 本 target 新增
```

复用代码 = `targets/react/pipeline.py`：

```python
from targets.html.pipeline import (
    LoadPsdStage, ParseToIrStage, HtmlCodegenStage, LayoutOptimizeStage,
)
from .stages import HtmlToReactStage, ReactScaffoldStage


def build_react_pipeline(ctx):
    return Pipeline([
        LoadPsdStage(),
        ParseToIrStage(),
        HtmlCodegenStage(),
        LayoutOptimizeStage(),
        HtmlToReactStage(),
        ReactScaffoldStage(),
    ])
```

> **硬约束**：`HtmlToReactStage` 之前的 4 段 **必须** 与 HTML target 完全一致。
> 不要复制一份再魔改，直接 `from targets.html.pipeline import ...` 即可。

## 产物形态

```
<output>/<psd_stem>/
├── images/                        # 原始图片（HTML target 产出）
├── index.html                     # HTML target 原始产物（保留，便于对照）
├── style.css
├── index_optimized.html
├── style_optimized.css
├── main.js
├── metadata.json
├── README.md                      # （HTML target 产出）
└── react/                         # ← React target 新增
    ├── index.html                 # Vite 模板（挂载 #root）
    ├── package.json               # react + react-dom + vite + @vitejs/plugin-react
    ├── vite.config.js
    ├── .gitignore
    ├── README.md                  # React 版本使用说明
    └── src/
        ├── main.jsx               # ReactDOM.createRoot(...).render(<App />)
        ├── App.jsx                # 自动生成：从 index_optimized.html 转换
        ├── App.css                # 自动生成：从 style_optimized.css 改写（图片路径）
        └── assets/images/         # 由 ../../images/ 复制
```

> `react/` 是可独立发布的 Vite 项目：`cd react && npm i && npm run dev` 即可预览。

## 关键实现

### 1. `codegen/html_to_jsx.py` — HTML → JSX

使用 BeautifulSoup 解析 HTML，递归重写成 JSX 字符串。

**属性映射表** `HTML_TO_JSX_ATTR`：
- `class` → `className`
- `for` → `htmlFor`
- `tabindex` → `tabIndex`、`readonly` → `readOnly`、`maxlength` → `maxLength` …
- `data-*` / `aria-*` 保持原名
- SVG 常见属性（`stroke-width` → `strokeWidth` 等）

**自闭合**：`VOID_ELEMENTS = {"img", "input", "br", "hr", "link", "meta", ...}`。

**className 策略**：保留字符串形式 `className="section-foo__image"`，
**不用 CSS Module**。原因见下。

**文本转义**：
- `{` / `}` → `{'{'}` / `{'}'}`（单遍替换，避免嵌套）
- `<` / `>` → `&lt;` / `&gt;`（防御性）
- 注释 `<!-- x -->` → `{/* x */}`

**图片路径**：`src="images/xxx.png"` → `src="./assets/images/xxx.png"`，
同时把 `images/xxx.png` 记入 `JsxResult.image_refs`。

### 2. `codegen/css_to_module.py` — CSS 改写

**不做 CSS Module 哈希**。只做一件事：

```
url("images/xxx.png")  →  url("./assets/images/xxx.png")
```

顺带收集所有 `.xxx` 类名与图片引用到 `CssModuleResult`。

### 3. 为什么不用 CSS Module？

本项目的类名由 HTML target 在生成阶段按 BEM 规则产出（如
`section-foo__image`），**已经全局唯一**，不需要哈希避免冲突。

而如果启用 CSS Module，会遇到两个麻烦：
1. 样式表中大量使用属性选择器（`[class*="__image"]`、`[class$="-container"]`），
   这类选择器**不会**匹配 CSS Module 哈希后的类名。要么把整份 CSS 用
   `:global { ... }` 包住（那样 `styles['foo']` 就拿不到映射了），
   要么逐条改选择器（工作量巨大且容易出错）。
2. `className="foo bar"` 在 CSS Module 下要写成
   `className={cn(styles, 'foo', 'bar')}`，JSX 可读性下降。

综合权衡，**全局 CSS + BEM 命名** 是成本最低且无行为差异的选择。
Vite 按路由/组件自动做代码拆分，并不会因为 CSS 是全局的而变差。

### 4. `stages.HtmlToReactStage`

职责：
1. 读取 `ctx.get("html_path")`（由 LayoutOptimizeStage 写入）。
2. 根据 HTML 文件名匹配对应 CSS（`index_optimized.html` ↔
   `style_optimized.css`；回退到 `index.html` ↔ `style.css`）。
3. 调用 `html_to_jsx` / `css_to_module`。
4. 写 `react/src/App.jsx` / `react/src/App.css`。
5. 把 `<output>/images/` 复制到 `react/src/assets/images/`（flat 拷贝）。
6. artifact：`react_dir` / `react_jsx_path` / `react_css_path`。

### 5. `stages.ReactScaffoldStage`

模板化写入：
- `package.json`（包名由 `_sanitize_npm_name(psd_stem)` 派生）
- `vite.config.js`（仅 `@vitejs/plugin-react`，`server.port=5173`）
- `index.html`（Vite 模板，`<div id="root">`）
- `src/main.jsx`（`ReactDOM.createRoot(...).render(<App />)`）
- `.gitignore` / `README.md`

> **为什么拆成两个 Stage？**
> 将来如果切到 Next.js / CRA / Remix，只需要替换 ReactScaffoldStage，
> HtmlToReactStage 不动。

## artifact 列表

| Key | 来源 | 含义 |
| --- | --- | --- |
| `html_path` | `LayoutOptimizeStage` | 优化后的 HTML 路径（输入给本 target） |
| `react_dir` | `HtmlToReactStage` | React 项目根目录 |
| `react_jsx_path` | `HtmlToReactStage` | App.jsx 绝对路径 |
| `react_css_path` | `HtmlToReactStage` | App.css 绝对路径 |

## 运行

```bash
python3 psd_to_code.py path/to/file.psd --target react
```

首次使用 React 产物：

```bash
cd output/<psd_stem>/react
npm install
npm run dev     # http://localhost:5173
npm run build   # 产物在 dist/
```

## 扩展点

### 需要把 JSX 拆成多组件怎么办？

当前 App.jsx 是**一个大组件**（与 HTML target 的"一个大画布"同构）。
如果要拆分，建议在 `HtmlToReactStage` 后面再加一个
`SplitComponentsStage`：读 IR 中的 group 树，为每个命名组单独生成一个
`<GroupName />.jsx`，App.jsx 里做组合。

关键：**不要动前 4 段 Stage**；这是与 HTML target 共享的基础设施。

### 需要 TypeScript 版本怎么办？

在 `ReactScaffoldStage` 里根据 `ctx.get("react_ts")` flag 切换：
`package.json` 加 `typescript` / `@types/react`，把 `.jsx` 写成 `.tsx`，
`html_to_jsx` 本身不需要改动（JSX 语法 TS 兼容）。

### 需要生成 Next.js 项目怎么办？

复制 `ReactTarget`，改名 `NextTarget`，复用 `HtmlToReactStage`，替换
`ReactScaffoldStage` 为 `NextScaffoldStage`（写 `app/page.jsx` 而不是
`src/App.jsx`）。

## 相关文档

- 如何新增 target：[`../04-extending/add-a-target.md`](../04-extending/add-a-target.md)
- 如何新增 Stage：[`../04-extending/add-a-stage.md`](../04-extending/add-a-stage.md)
- HTML target 细节（本 target 的基础）：[`./targets-html.md`](./targets-html.md)
- 布局优化器（React 也完全依赖其产物）：[`../03-topics/layout-optimizer.md`](../03-topics/layout-optimizer.md)
