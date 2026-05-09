# psd2code 项目文档

> 面向"接手维护者 + 协作 AI"的权威开发文档。阅读顺序从上至下，
> 由浅入深；每份文档均有"**本文解决的问题 / 不讨论什么**"的说明，
> 方便快速定位。

---

## 这是什么

`psd2code` 是 **PSD → 前端代码** 的编译器式工具链。

- 已实装：`target=html`（与历史 `psd2html` 字节级一致）、`target=react`
  （Vite + JSX，在 HTML 产物基础上做二次转换）、`target=vue`
  （Vite + Vue 3 SFC，同样基于 HTML 产物二次加工）。
- 架构已为 `target=mini-program` 等预留扩展点。
- 入口：`.codebuddy/skills/psd2code/psd_to_code.py`
- SKILL 说明：见 [`../SKILL.md`](../SKILL.md)

## 文档索引

### 1. 了解架构（所有新加入者必读）

| 文档 | 作用 |
| ---- | ---- |
| [01-architecture/overview.md](./01-architecture/overview.md) | 整体分层、package 职责、"前端 / 后端"边界 |
| [01-architecture/data-flow.md](./01-architecture/data-flow.md) | 从 `.psd` 到 `index.html` 的全链路数据流 |
| [01-architecture/design-patterns.md](./01-architecture/design-patterns.md) | 用到的设计模式（Pipeline / Strategy / Chain / Registry / Observer / Facade） |
| [01-architecture/directory-layout.md](./01-architecture/directory-layout.md) | 目录 → 模块 → 关键文件的映射表 |

### 2. 模块详解（按包组织）

| 文档 | 作用 |
| ---- | ---- |
| [02-modules/framework.md](./02-modules/framework.md) | `framework/`：Stage / Pipeline / Context / Hook |
| [02-modules/core-ir.md](./02-modules/core-ir.md) | `core/ir/`：pydantic IR 与 legacy adapter |
| [02-modules/core-psd.md](./02-modules/core-psd.md) | `core/psd/`：Loader / Parser / Classifier / TextExtractor |
| [02-modules/core-render.md](./02-modules/core-render.md) | `core/render/`：图层 & 效果像素渲染 |
| [02-modules/core-extract.md](./02-modules/core-extract.md) | `core/extract/`：LayerExporter + Handler 决策链 |
| [02-modules/targets-html.md](./02-modules/targets-html.md) | `targets/html/`：codegen / postprocess / emit |
| [02-modules/targets-react.md](./02-modules/targets-react.md) | `targets/react/`：HTML→JSX 转换 + Vite 脚手架 |
| [02-modules/targets-vue.md](./02-modules/targets-vue.md) | `targets/vue/`：HTML→SFC 转换 + Vite + Vue 3 脚手架 |
| [02-modules/semantic.md](./02-modules/semantic.md) | `semantic/` + `common/cn_dict.json`：图层名→kebab-token 多层流水线 |
| [02-modules/common-config.md](./02-modules/common-config.md) | `common/` 工具库 & `config/` 全局配置 |

### 3. 关键主题（跨模块的"为什么这样做"）

| 文档 | 作用 |
| ---- | ---- |
| [03-topics/ir-contract.md](./03-topics/ir-contract.md) | IR 作为 core/targets 契约的规则与演进策略 |
| [03-topics/group-rendering.md](./03-topics/group-rendering.md) | 组渲染：混合渲染 / 子组 composite / 效果溢出 |
| [03-topics/effects-rendering.md](./03-topics/effects-rendering.md) | 描边 / 阴影 / 发光 / 叠加的渲染流水线 |
| [03-topics/layout-optimizer.md](./03-topics/layout-optimizer.md) | 布局优化器：DOM 重构 + Flex 推断 + overflow 修复 |
| [03-topics/asset-extraction.md](./03-topics/asset-extraction.md) | 图片导出、命名与去重规则 |

### 4. 扩展指南（"我要新增 XX"）

| 文档 | 作用 |
| ---- | ---- |
| [04-extending/add-a-target.md](./04-extending/add-a-target.md) | 新增一个产物（Vue / React / 小程序） |
| [04-extending/add-a-stage.md](./04-extending/add-a-stage.md) | 新增一个 Stage（给现有 target 插后处理） |
| [04-extending/add-a-layer-handler.md](./04-extending/add-a-layer-handler.md) | 新增一条图层导出决策分支 |
| [04-extending/add-an-effect.md](./04-extending/add-an-effect.md) | 新增一种效果渲染器 |

### 5. 开发约定

| 文档 | 作用 |
| ---- | ---- |
| [05-conventions/coding-style.md](./05-conventions/coding-style.md) | 代码风格 / import / 类型 / 日志 |
| [05-conventions/testing-and-validation.md](./05-conventions/testing-and-validation.md) | 回归验证（baseline diff）的标准流程 |
| [05-conventions/known-pitfalls.md](./05-conventions/known-pitfalls.md) | 【必读】硬约束与踩坑点（渲染分支不可拆、子组必须 composite 等） |
| [05-conventions/ai-handoff.md](./05-conventions/ai-handoff.md) | 给协作 AI 的上手 checklist |

---

## 5 分钟上手

```bash
# 1. HTML 产物（默认）
python3 .codebuddy/skills/psd2code/psd_to_code.py path/to/file.psd

# 2. React 产物（Vite 项目，在 HTML 产物之上再加工）
python3 .codebuddy/skills/psd2code/psd_to_code.py path/to/file.psd --target react

# 3. Vue 产物（Vite + Vue 3 SFC，同样基于 HTML 产物二次加工）
python3 .codebuddy/skills/psd2code/psd_to_code.py path/to/file.psd --target vue

# 4. 输出
# .codebuddy/skills/psd2code/output/<psd_stem>/
#     ├── html/                   # HTML 版本（任何 target 都会先产出，作为中间/对照产物）
#     │   ├── index.html
#     │   ├── style.css
#     │   ├── index_optimized.html
#     │   ├── style_optimized.css
#     │   ├── main.js
#     │   ├── metadata.json
#     │   ├── README.md
#     │   └── images/
#     ├── react/                  # 仅 --target react 产出
#     │   ├── package.json / vite.config.js / index.html
#     │   └── src/App.jsx, App.css, main.jsx, assets/images/
#     └── vue/                    # 仅 --target vue 产出
#         ├── package.json / vite.config.js / index.html
#         └── src/App.vue, main.js, assets/images/
```

运行 React 产物：

```bash
cd output/<psd_stem>/react
npm install && npm run dev    # http://localhost:5173
```

## 重要的硬约束（请务必阅读）

在开始任何改动前，**必须**读：[05-conventions/known-pitfalls.md](./05-conventions/known-pitfalls.md)。

关键几条：

1. **子组渲染必须用 `composite(viewport=...)`**，不能退回到手动递归渲染+裁切。
2. **组级效果溢出要走"手动扩展 + composite 覆盖"混合渲染**，不能单走一边。
3. **HTML 输出必须与 `psd2html` 基线字节一致**（baseline diff 零差异），
   改动前后都要跑 `diff -rq`。
4. **`LayerExporter` 内部的渲染分支不要重构拆分**（历史经验，已知容易引入像素级回归）。
5. `__pycache__` 已在入口处全局禁用（`sys.dont_write_bytecode=True`）。

---

## 文档维护规则

- 每次引入新的 Stage / Target / Handler / 硬约束，**同步更新** 对应章节。
- 用"**本文解决什么 / 不讨论什么**"头注帮助读者快速定位。
- 代码块统一用三反引号 + 语言标签；文件路径用反引号。
- 所有示例路径以 `.codebuddy/skills/psd2code/scripts/` 为基准。
