# 架构总览

> **本文解决什么**：让你在 10 分钟内理解 psd2code 的分层模型和各 package 的职责。
> **不讨论什么**：具体类的实现细节（见 `02-modules/` 系列）。

---

## 一句话定位

psd2code 是一个**编译器式**工具链：

```
PSD 源 ──► IR（pydantic 强约束契约）──► 目标代码（HTML / Vue / React / ...）
       前端（core）                    后端（targets）
```

"前端 / 后端" 借用编译器术语：

- **前端（core）**：只关心 PSD → IR，不知道任何目标格式的存在。
- **后端（targets）**：只关心 IR → 代码，不关心 PSD 长什么样。

这条边界 **必须严格遵守**：`core/*` 不得 import `targets/*`；
`targets/*` 不得直接拿 psd-tools 对象，必须通过 IR。

---

## 顶层目录

```
psd2code/
├── SKILL.md                        # Skill 元信息（触发词 / 简介）
├── psd_to_code.py                  # 统一 CLI 入口
├── doc/                            # 你正在看的文档
├── output/                         # 运行产物（被 .gitignore 忽略）
└── scripts/
    ├── common/     # 通用工具（拼音命名、CSS/HTML 工具、图像工具）
    ├── config/     # 全局配置 + 版本号
    ├── core/       # 【前端】PSD → IR（产物无关）
    ├── framework/  # 通用流水线抽象（Stage / Pipeline / Context / Hook）
    ├── semantic/   # 图层名 → kebab-token 的三层命名解析子系统
    │               # （Layer2 角色推断 / Layer1 清洗 / Fallback 拼音兜底）
    └── targets/    # 【后端】按产物分包（当前 html；未来 vue/react/...）
```

## 核心 package 职责一览

| Package | 职责 | 是否允许 import psd-tools | 是否允许 import targets |
| ------- | ---- | ------------------------ | ----------------------- |
| `common/`       | 纯工具（无业务状态）        | 否 | 否 |
| `config/`       | 全局配置 + 版本号          | 否 | 否 |
| `framework/`    | 流水线抽象（业务无关）      | 否 | 否 |
| `semantic/`     | 图层名 → 语义 token 解析（三层仲裁） | 否 | 否 |
| `core/ir/`      | pydantic IR 数据类         | 否 | 否 |
| `core/psd/`     | PSD 加载 / 解析 / 分类     | **是** | 否 |
| `core/render/`  | 像素渲染（图层 + 效果）    | **是** | 否 |
| `core/extract/` | 资源导出（图片 → 磁盘）    | **是** | 否 |
| `targets/html/` | IR → HTML/CSS/JS 生成     | 否（通过 IR 拿到一切） | 仅自身 |
| `targets/react/`| HTML → JSX + Vite 脚手架   | 否（通过 HTML target 的产物） | **可 import `targets.html.pipeline`**（复用前 4 段 Stage） |

> 违反此表即"架构层穿透"，务必避免。

## 三大核心抽象

### 1. IR (Intermediate Representation)

- 定义：`scripts/core/ir/*`
- 形式：pydantic `BaseModel`，带字段校验（如 `bbox.right >= bbox.left`）。
- 根节点：`Document`（画布尺寸 + `root: GroupNode` + `assets`）
- 节点类型：`GroupNode` / `ImageNode` / `TextNode` / `ShapeNode`（discriminated union）
- **意义**：core / targets 之间的唯一契约。任何 target 都从 IR 出发。
- 详见：[`../02-modules/core-ir.md`](../02-modules/core-ir.md) 和 [`../03-topics/ir-contract.md`](../03-topics/ir-contract.md)

### 2. Pipeline / Stage / Context

- 定义：`scripts/framework/*`
- `PipelineContext`：贯穿全链路的状态容器（输入、产物、目标名、hook）。
- `Stage`：单一职责的处理节点，输入输出都是 `PipelineContext`。
- `Pipeline`：顺序执行一组 Stage，支持 Observer Hook（见 `framework/hooks.py`）。
- 详见：[`../02-modules/framework.md`](../02-modules/framework.md)

### 3. Target + Registry

- 定义：`scripts/targets/base.py`、`scripts/targets/registry.py`
- `Target`：一个产物对应一个子类，通过 `@register("html")` 装饰器自动注册。
- 入口 `psd_to_code.py` 只做一件事：按 `--target html` 去 registry 查类并执行。
- 新增 target 的方法：见 [`../04-extending/add-a-target.md`](../04-extending/add-a-target.md)

## HTML target 的 Stage 链（当前实装）

```
LoadPsdStage      （打开 PSD、准备输出目录）
       ↓
ParseToIrStage    （PSD → IR；副作用：导出所有图片到磁盘）
       ↓
HtmlCodegenStage  （IR → index.html / style.css / main.js / metadata.json / README.md）
       ↓
LayoutOptimizeStage（DOM 重构 + Flex 推断 → *_optimized.html / *_optimized.css）
```

> 所有 Stage 都写入 `ctx.artifacts`，不直接改全局状态。

## React target 的 Stage 链

```
LoadPsdStage          ↘
ParseToIrStage         │  完全复用 targets/html/pipeline 的前 4 段
HtmlCodegenStage       │
LayoutOptimizeStage   ↗
       ↓
HtmlToReactStage      （优化后的 HTML/CSS → App.jsx + App.css + 复制 images）
       ↓
ReactScaffoldStage    （写入 Vite 脚手架：package.json / vite.config.js / main.jsx / ...）
```

> React target 故意设计为「HTML 产物的后置加工」，未来 HTML target 的每个改进
> 都能自动惠及 React。细节见 [`../02-modules/targets-react.md`](../02-modules/targets-react.md)。

## 与旧 `psd2html` skill 的关系

- `psd2html` 是 psd2code 的前身，代码仍保留用于过渡期回归验证。
- psd2code 当前 `target=html` 的输出与 `psd2html` 是**字节级一致**的。
- 新功能只在 psd2code 中迭代。

## 下一步

- 想了解数据怎么从 PSD 流到 HTML：读 [`data-flow.md`](./data-flow.md)
- 想了解用了什么设计模式：读 [`design-patterns.md`](./design-patterns.md)
- 想动手改代码：先读 [`../05-conventions/known-pitfalls.md`](../05-conventions/known-pitfalls.md)
