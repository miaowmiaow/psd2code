---
name: psd2code
description: 将 PSD 设计稿转换为多种前端代码（HTML / React / Vue）。使用编译器式架构：core 负责 PSD→IR 解析、IR 由 pydantic 定义并校验，targets 按产物可插拔注册。具备组级效果像素级渲染、智能 Flex 布局推断、装饰背景剥离、中文短标题字号自适应、文本/图片去重等能力。触发词：psd 转 html、psd 转 react、psd 转 vue、psd to code、设计稿转代码。
---

# psd2code Skill

## 概述

`psd2code` 是 **PSD → 前端代码** 的编译器式工具链，提供**多 target 可插拔**的产物生成能力。

- ✅ `target=html`：原生 HTML/CSS/JS（含 absolute 原版 + Flex 优化版双产物）
- ✅ `target=react`：Vite + React 18 项目（在 HTML 产物之上二次转换）
- ✅ `target=vue`：Vite + Vue 3 SFC 项目（同样基于 HTML 产物二次加工）
- 🧩 架构为 `target=mini-program` 等预留清晰扩展点

> **开发者 / 协作 AI 请先读 [`doc/README.md`](./doc/README.md)** —— 完整的架构、模块、扩展指南、硬约束。
> 修改代码前务必阅读 [`doc/05-conventions/known-pitfalls.md`](./doc/05-conventions/known-pitfalls.md)。

## 何时使用

- 用户提供 `.psd` 文件并要求转换为前端代码（HTML / React / Vue）。
- 用户希望 PSD 还原为可运行的页面代码，并保留图层结构、样式、文字、特效等信息。
- 触发词：`psd 转 html`、`psd to html`、`psd to code`、`psd 转代码`、`设计稿转代码`、`psd 转 react`、`psd to react`、`psd 转 vue`、`psd to vue`。

## 核心能力

### 1. 图层解析与分类
- 自动识别 PSD 中的 **组（Group）/ 文本（Type）/ 形状（Shape）/ 像素图（Pixel）/ 智能对象（SmartObject）/ 调整层** 等图层类型。
- **形状层**保留矢量信息（圆角矩形、圆形、椭圆等）以便用 CSS 还原（如 `border-radius`）。
- 旋转/倾斜的文本图层自动**降级为图片**导出，保证视觉一致性。

### 2. 文本提取
- 从 PSD `engine_dict` 解析 **字体大小、行距、颜色、对齐方式**。
- **`transform.scale` 字号修正**：自动用图层 transform 矩阵的缩放因子还原渲染时的真实字号。
- **纵向视觉兜底**：字号 ≥ `bbox.height × 0.85` 时压回，避免浏览器字宽差异导致溢出。
- **横向中文兜底**：纯中文短标题（CJK ≥ 90%、≤ 12 字）按 `width / 字数 × 0.95` 限制字号，避免单行被挤换行。
- 文本含描边/阴影/发光等效果时自动降级为图片，由效果渲染器处理。

### 3. 像素级图层与效果渲染
- **效果渲染器**：`描边 / 投影 / 内阴影 / 外发光 / 内发光 / 颜色叠加 / 渐变叠加 / 图案叠加`。
- **组渲染混合策略**：
  - 子组（嵌套 Group）必须用 `composite(viewport=...)` 调用 PS 原生合成引擎，正确复现组级效果裁切。
  - 组级效果溢出（如圆角矩形 8px 外描边）走"手动扩展画布 + composite 覆盖内部区域"混合渲染：
    - 外部区域用手动逐层渲染保留溢出效果；
    - 内部区域用 PS 原生 composite 输出，达到像素级匹配。
- **效果溢出检测**：自动扩展导出 bbox 容纳描边/阴影像素，避免边缘裁切。

### 4. 资源提取与优化
- **图片去重**：相同像素内容的图层合并为同一张图片文件（哈希比对）。
- **背景合并**：同组内多张全屏背景层自动合并为父容器的 `background-image: url(...), url(...)`，按**视觉 z 序**排列（第一个 url = 最上层）。
- **空容器跳过**：纯结构组不生成图片资源。
- **语义命名流水线（多层）**：CSS 类名 / 图片文件名共用一套 token 抽取流水线
  （`semantic/`），按置信度从高到低：
  - **Layer 2** (DOM 角色, 0.6~0.95)：按钮误判降级 / shape 按钮强化 / 大背景补全 / 纯文本容器
  - **Layer 1** (扩展词典, 0.85)：查 `common/cn_dict.json`（约 470 条，含活动/电商/游戏/社交业务词）
  - **Fallback** (legacy 关键词 + 拼音, 0.5)：`你的预测` → `nideyuce__152`
  - 每次转换写出 `_naming_report.md`：列出每个图层 → token 的来源与命中规则
  - 加新业务词只改 [`common/cn_dict.json`](./scripts/common/cn_dict.json)，不动代码
  - 详见：[`doc/02-modules/semantic.md`](./doc/02-modules/semantic.md)

### 5. 布局优化器（LayoutOptimizer，7 段流水线）
将 absolute 定位的原始版本智能转为 **Flex 布局** 的优化版本。

**Step 1：DOM 重构**（`dom_restructure.py`）
- 按空间包含关系调整 DOM 父子结构（如把"内容组"移入"底框图层"内部）。
- **背景剥离**三规则：完全包含型 / 主轴覆盖型 / 双轴主导覆盖型（≥ 80%）。
- **高瘦跨行装饰剥离**：4 条 AND 判据（高度比 ≥ 2.0、aspect ≥ 0.8、跨 ≥ 2 行、X 投影重叠 ≥ 0.8）→ 单独成 `v-stack`。
- 多行/多列切分使用**主导重叠率算法**（≥ 50% 才算同行），对"高瘦+矮元素混排"健壮；伪多行装饰回退为 stack。
- **容器背景吸收 pass**：DOM 重构后再扫一遍，把同尺寸大背景剥到父 `background-image`，多张本地 PNG 直接合成单图（透传 `images_dir`）。
- **Stack→Col 反向升级**：N=2 时按"X 重叠 ≥ 0.95 且 Y 间距 ≤ 50px"双强信号判 v-col。

**Step 1.2：图层扁平化（统一通道）**（`image_layer_flatten.py`，2026-04-30 重构）
- 历史上"合并图片以减少 DOM/CSS/PNG 请求"分散在四个分支（merge_siblings / collapse_into_parent / absorb_into_relative / merge_parent_bg），合并为**单一递归函数**。
- 对每个候选容器，把"容器自身 background-image（如有）+ 全部直接 image 子的 background-image"按 z 序合成单张 PNG，写回容器自身，删除子 div + 子 CSS。**容器一律保留**（不消除 DOM 层级，不破坏外层布局/虚拟 wrapper 语义）。
- 后序遍历 + 多轮扫描（最多 5 轮），实现"子图合并 → 父再吸收为背景"的链式简化。
- 装饰字段护栏（`_PARENT_BLOCKING_PROPS`）：容器有 `border-radius / overflow:hidden / box-shadow / clip-path / filter / transform` 等不可烧进 PNG 的字段时跳过。
- 几何护栏：总层数 ≥ 2、envelope 面积 ≤ canvas × 0.5、子之间 L∞ 距离 ≤ 10px 邻接图必须连通。

**Step 1.5：兄弟同质簇检测**（`sibling_group_detector.py`）
- v-row 父下非趋势子节点若满足 5 条 AND（同 `data-type` / 几何相近 / X 间距规整 / Y 共线 / N ≥ 3）→ 包成 `v-list` wrapper，便于下游 flex 化。

**Step 2：布局分析与 Flex 应用**（`layout_analyzer.py` + `flex_applier.py`）
- 检测子元素的 vertical / horizontal trend，推断 `flex-col` / `flex-row`。
- **三道安全闸门**避免误判：
  - **V8** 互相重叠装饰簇 → 拒绝 flex
  - **V9** 支配背景层（占容器 ≥ 80% + 其余子元素 ≥ 60% 落在内）→ 拒绝 flex
  - **V10** 高瘦跨行装饰 → 不参与 flex
- 趋势子元素参与 flex（写 `margin`），非趋势 / 装饰子元素保留 `position: absolute` + 原坐标。
- ⚠️ **v-stack / v-list / v-row / v-col wrapper 的 `position: relative` 必须保留**（即使父被 flex 化），否则内部 absolute 子节点会飘到外层 positioned 祖先（典型 bug：领奖.psd `wenan__93`）。

**Step 2.5：单子 wrapper 折叠**（`wrapper_collapse.py`）
- DOM 重构 + Flex 化之后，剩下一些"内部只有 1 个子节点"的虚拟 wrapper（v-row-N / v-col-N）是布局算法的副产物，纯粹冗余。
- 把 wrapper 的 margin 数值合并到子节点（margin-top/left/right/bottom 数值相加），用 `wrapper.replace_with(child)` 整体替换，删除 wrapper 的 CSS 规则。
- **故意只折叠 v-row / v-col**：不折叠 v-stack（absolute 子的 containing block 容器，折叠会让子节点跳到外层）；不折叠 v-list（同质兄弟列表，wrap 行为依赖容器存在）；不折叠根 layer-group。
- 多轮扫描（最多 5 轮）直到稳定（一次折叠可能让外层也变成单子）。

**Step 3：CSS 去冗余**（`css_dedup.py`）
- Pass 1：父容器内"已知 z 单调递增 / 单条"序列删 z-index（南瓜大作战 H5 实测精简 304 处）。
- Pass 2：等价规则按属性签名合并到 `_css_merge_groups`，配合 `dict_to_css(merge_groups=...)` 输出形如 `.a, .b, .c { ... }`。
- 数值规范化：`22.000px → 22px`，但**严格**排除 `url(...)` 内容与标识符内数字段（`bg-f07984` 不归一）。

**Step 3.5：重复元素抽取**（`repeat_class_unifier.py`）
- CssDedup 的 `_css_merge_groups` 把"属性完全相同的多个选择器"识别成等价组，但 HTML 里**仍然写着 N 个不同的 hash 类**（`.prop__68 / .prop__105 / .prop__142`），可读性差、复用代价高。
- 此 pass 把 ≥ 3 个等价 hash 类合并为单一语义 base 类（如 `prop__68 / prop__105 / prop__142` → 全部用 `.prop`），HTML 元素的 hash 类被替换为 base 类。
- 命名规则：组内成员若都形如 `.<base>__<digits>` 且 base 唯一 → 用 base；与 css_rules 重名时追加 `-grp` 后缀。
- **故意不动**：v-stack-N / v-row-N / v-col-N 等自动派生类（序号本身就是其复用维度）；layer-group / layer 角色类（layout_optimizer 契约）。
- 把已合并的组从 `_css_merge_groups` 移除，避免 CssPretty 再渲染合并块；可选写 `data-repeat-index` 给 :nth-child / JS 选择。

**Step 4：CSS 美化渲染**（`css_pretty.py`，5 Pass + 双预设）
- **预设**：`compact`（默认，紧凑接近手写 CSS）/ `expanded`（开发者友好全展开 + 坐标溯源注释）。CLI `--css-style {compact,expanded}` 切换。
- Pass 1：固定 4 段文件骨架（Reset → @media → #canvas → 图层规则）+ 段落分隔注释（compact 单行 / expanded 框框）。
- Pass 2：图层规则按 `index_optimized.html` DOM 自顶向下顺序排列；`bankuai-*` / `section-*` 边界插版块注释。
- Pass 3：属性按"定位 / 盒模型 / 排版 / 外观 / 混合 / 其他"分段（**仅 ≥ 8 个属性的块分段**；compact 默认关闭分段走紧凑）。
- Pass 4：合并组 ≥ N 成员时选择器逐行 + `/* ↳ N 个等价规则 */`（compact 阈值 4 / expanded 阈值 3）。
- Pass 5：单选择器 + 属性 ≤ N → 单行紧凑（compact ≤ 6 / expanded ≤ 2）。
- 与 `dict_to_css` 字节级 W3C 等价；CssPretty 失败自动降级到 `dict_to_css`。

**实测效果（南瓜大作战 H5 / compact 默认）**：style_optimized.css 1499 行（前 4938 行），与基线 Playwright **0 像素差异**。

> overflow / border-radius 处理已迁移到源头 `targets/html/codegen/renderers/group_renderer.py`，**不**在 LayoutOptimizer 里再加同名 pass。

### 6. 多 Target 可插拔架构
- HTML target 产出 `index.html`（原版） + `index_optimized.html`（Flex 版） + `style*.css` + `main.js` + `images/` + `metadata.json` + `layer_map.json`（剥离的 dev metadata 反查表）。
- React target：在 HTML 产物之上做 `html_to_jsx` + `css_to_module`，输出可 `npm run dev` 的 Vite 项目。
- Vue target：在 HTML 产物之上做 `html_to_template` + `css_rewrite`，输出 Vue 3 SFC + Vite 项目。
- 任何 HTML 能力升级**自动惠及**衍生 target（前 3 段 Stage 共享：Load / Parse / Codegen，后续 Optimize / Strip 走各 target 自己的写盘）。

### 7. 国际化预留
- 所有文本节点带 `data-i18n-key`，可通过 JS 动态替换实现多语言。

## 快速开始

```bash
# 默认 target = html
python3 .codebuddy/skills/psd2code/psd_to_code.py /path/to/file.psd

# 显式指定 target
python3 .codebuddy/skills/psd2code/psd_to_code.py /path/to/file.psd --target html
python3 .codebuddy/skills/psd2code/psd_to_code.py /path/to/file.psd --target react
python3 .codebuddy/skills/psd2code/psd_to_code.py /path/to/file.psd --target vue

# CSS 输出风格（仅 html target，作用于 style_optimized.css）
python3 .codebuddy/skills/psd2code/psd_to_code.py /path/to/file.psd --css-style compact   # 默认：紧凑接近手写 CSS
python3 .codebuddy/skills/psd2code/psd_to_code.py /path/to/file.psd --css-style expanded  # 全展开 + PSD 坐标溯源注释（开发者排查友好）

# CI 基线对比：禁用 CssPretty，回到 dict_to_css 字母序机械渲染
python3 .codebuddy/skills/psd2code/psd_to_code.py /path/to/file.psd --no-css-pretty

# 关闭全部智能合图（4 类一把梭：装饰组合并 / 底部背景合并 / ImageLayerFlatten / 多 url 背景合成）
# 每个 PSD 图层保留独立 DOM + CSS 规则，便于 1:1 诊断切图问题或像素回归对齐
python3 .codebuddy/skills/psd2code/psd_to_code.py /path/to/file.psd --no-smart-merge

# 运行 React 产物
cd output/<psd_stem>/react
npm install && npm run dev    # http://localhost:5173

# 运行 Vue 产物
cd output/<psd_stem>/vue
npm install && npm run dev    # http://localhost:5173
```

## 产物结构

```
output/<psd_stem>/
├── html/                       # 任何 target 都会先产出，作为中间/对照产物
│   ├── index.html              # 原始 absolute 版（与 PSD 像素级对齐，**保留** dev metadata 用于诊断）
│   ├── index_optimized.html    # Flex 优化版（**已剥离** dev metadata，开发者最终交付物）
│   ├── style.css / style_optimized.css
│   ├── main.js                 # 国际化等运行时逻辑
│   ├── metadata.json           # 图层树元数据
│   ├── layer_map.json          # 反查表：CSS 类名 / 优化版 layer-id → PSD 原图层名 / 类型
│   ├── _naming_report.md       # 语义命名报告：每个图层 → token 的来源（layer1/layer2/fallback/none）
│   ├── README.md
│   └── images/                 # 切出的图层、效果合成图、背景图
├── react/                      # 仅 --target react 产出（Vite + React 18）
│   ├── package.json / vite.config.js / index.html
│   └── src/App.jsx, App.css, main.jsx, assets/images/
└── vue/                        # 仅 --target vue 产出（Vite + Vue 3 SFC）
    ├── package.json / vite.config.js / index.html
    └── src/App.vue, main.js, assets/images/
```

## 架构（Feature Modules / 编译器式）

```
psd2code/
├── SKILL.md
├── psd_to_code.py                  # 统一 CLI 入口
└── scripts/
    ├── common/                     # 通用工具：CSS/HTML 工具、图片工具、cn_dict.json 词典
    ├── semantic/                   # 语义命名流水线：图层名 → kebab-token（多层仲裁 + report）
    ├── config/                     # 全局配置（输出路径、图片格式、开关）
    ├── core/                       # 【前端】与产物无关：PSD → IR
    │   ├── ir/                     # IR 数据类（pydantic BaseModel + 校验）
    │   │   ├── document.py         # Document：根
    │   │   ├── nodes.py            # Node：Group / Image / Text / Shape
    │   │   ├── styles.py           # Style：尺寸、定位、字体、颜色
    │   │   ├── effects.py          # EffectSpec：描边/阴影/发光/叠加
    │   │   └── assets.py           # AssetRef：导出资源引用
    │   ├── psd/                    # PSD 解析：loader / parser / classifier / text_extractor
    │   ├── render/                 # 像素渲染：layer_renderer + effects/*
    │   └── extract/                # 资源提取：exporter / group_merger / background_merger / image_dedup
    │
    ├── framework/                  # 通用流水线抽象（业务无关）
    │   ├── context.py              # PipelineContext：贯穿所有 Stage 的上下文
    │   ├── stage.py                # Stage：抽象基类
    │   └── pipeline.py             # Pipeline：注册与执行 stages
    │
    └── targets/                    # 【后端】按产物分包
        ├── base.py                 # Target 抽象基类
        ├── registry.py             # 全局 target 注册表 + @register 装饰器
        ├── html/                   # target=html
        │   ├── target.py           # HtmlTarget：注册并装配 html 流水线
        │   ├── pipeline.py         # html 专属流水线（Load/Parse/Codegen/Optimize 4 段）
        │   ├── codegen/            # IR → HTML/CSS/JS（含 css_helpers / renderers）
        │   ├── postprocess/        # layout_optimizer（7 段：DOMRestructure + ImageLayerFlatten + Sibling + Flex + WrapperCollapse + CssDedup + RepeatClassUnifier + CssPretty）+ strip_dev_metadata
        │   └── emit/               # 写入磁盘
        ├── react/                  # target=react
        │   ├── target.py           # ReactTarget
        │   ├── pipeline.py         # 复用 html 前 3 段 + 新增 2 段
        │   ├── stages.py           # HtmlToReactStage + ReactScaffoldStage
        │   └── codegen/            # html_to_jsx + css_to_module
        └── vue/                    # target=vue
            ├── target.py / pipeline.py / stages.py
            └── codegen/            # html_to_template + css_rewrite
```

### 核心抽象

1. **IR (Intermediate Representation)**：用 pydantic 定义，是 `core` 与 `targets` 之间的契约。任何 target 都从 IR 出发，不直接操作 PSD。
2. **PipelineContext**：流水线全局上下文，承载 PSD、IR、配置、产物路径、target 中间产物等。
3. **Stage**：单一职责的处理步骤，输入/输出都是 `PipelineContext`。
4. **Target**：一个产物对应一个 `Target` 子类，通过 `@register("html")` 注册到 registry。
5. **Pipeline**：由若干 Stage 组成，通过 Target 装配。

### 数据流

```
PSD 文件
  ↓ core/psd/loader + parser
PSDFile (psd-tools)
  ↓ core/psd/classifier + text_extractor + core/render/* + core/extract/*
IR Document（pydantic 校验）
  ↓ targets/<name>/codegen
中间代码（HTML / CSS）
  ↓ targets/html/postprocess/layout_optimizer
     （DOMRestructure → ImageLayerFlatten → SiblingGroupDetector → FlexApplier
      → WrapperCollapse → CssDedup → RepeatClassUnifier → CssPretty）
  ↓ targets/html/postprocess/strip_dev_metadata
     （剥离 data-name/data-type/id="layer-*" → layer_map.json）
优化后的 HTML/CSS
  ↓ targets/<name>/emit  —— 或（react/vue target）HtmlToXxxStage → XxxScaffoldStage
最终产物（output/<psd_stem>/<target>/）
```

> React 与 Vue target 都故意设计为「HTML 产物的后置加工」：前 3 段 Stage 与 HTML target
> 完全共享，确保未来 HTML 能力的每次升级都能自动惠及衍生产物。

## 依赖

- Python 3.10+
- psd-tools >= 1.14
- Pillow >= 10
- numpy
- beautifulsoup4
- pydantic >= 2.0
- pypinyin
