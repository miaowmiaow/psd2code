# 数据流：从 PSD 到前端代码

> **本文解决什么**：用一条时间线讲清 `.psd` 如何一步步变成 `index_optimized.html`
> （或 React 项目）。
> **不讨论什么**：每个 Stage 的内部算法（见各模块文档）。

---

## 全景图

```
┌─────────────┐
│  .psd file  │
└──────┬──────┘
       │
       ▼ psd_to_code.py（CLI 入口）
┌─────────────────────────────────────────────┐
│  registry.get("html") → HtmlTarget()        │
│  target.run(ctx)  ──►  Pipeline.run(ctx)    │
└──────┬──────────────────────────────────────┘
       │
       ▼ Stage 1: LoadPsdStage
┌─────────────────────────────────────────────┐
│  PSDImage.open(path)                        │
│  清空并创建  output/<psd_stem>/             │
│  结果：ctx.psd 就绪，ctx.output_dir 就绪    │
└──────┬──────────────────────────────────────┘
       │
       ▼ Stage 2: ParseToIrStage
┌─────────────────────────────────────────────┐
│  core/psd/parser.parse_psd_to_ir()          │
│  内部步骤：                                   │
│   a. LayerExporter 遍历图层树                │
│      ├─ handlers.py 决策链分派每个图层/组    │
│      ├─ core/render/* 渲染效果、合并组        │
│      ├─ core/extract/image_ops 裁剪/合成      │
│      └─ 把每张图片写入 output/<stem>/images/ │
│   b. 把 legacy 字典包装成 IR Document        │
│   结果：ctx.ir 就绪；图片已全部落盘；         │
│        ctx.artifacts['layer_exporter'] 可取  │
└──────┬──────────────────────────────────────┘
       │
       ▼ Stage 3: HtmlCodegenStage
┌─────────────────────────────────────────────┐
│  to_legacy_layers(ctx.ir)  → list[dict]     │
│  HTMLGenerator(...).generate_html/metadata   │
│   ├─ CodegenContext      共享状态容器        │
│   ├─ LayerRenderer       树递归，按节点类型   │
│   │    └─ renderers/     Strategy 分派        │
│   │         ├─ GroupRenderer                  │
│   │         ├─ ImageRenderer                  │
│   │         └─ TextRenderer                   │
│   └─ HtmlBuilder         拼装 HTML/CSS/JS    │
│  结果：index.html / style.css / main.js /    │
│        metadata.json / README.md 已写入       │
└──────┬──────────────────────────────────────┘
       │
       ▼ Stage 4: LayoutOptimizeStage
┌─────────────────────────────────────────────┐
│  optimize_layout(html, css_rules,            │
│                  global_header, pretty_config,│
│                  repeat_unify_config,         │
│                  semantic_rename_config,      │
│                  virtual_wrapper_rename_config,│
│                  position_relaxer_config, ...)│
│  （12 段协调器，按顺序执行）                  │
│   Step 1    DOMRestructure         背景剥离 / 高瘦装饰剥离 / 切行列 │
│                                    / 容器背景吸收 / Stack→Col 升级 │
│                                    + _can_flex_applier_handle 早退撤销 │
│   Step 1.2  PureImageGroupFlatten  多 PNG 子层合成单背景图（含 父吸收/上提） │
│   Step 1.5  OverflowFixer          图片容器/圆角容器/按钮 overflow 修复 │
│   Step 2    SiblingGroupDetector   兄弟同质簇 → v-list（5 条 AND）    │
│   Step 2.5  FlexApplier            识别水平/垂直 flex 模式            │
│                                    含 v-stack/v-list position 保护  │
│                                    + flex-shrink:0 硬约束            │
│   Step 3    WrapperCollapse        单子 row/col wrapper 折叠          │
│   Step 3.3  PositionNoiseRelaxer   位置噪声宽容合并（唯一视觉差异步） │
│   Step 3.5  CssDedup               默认值剥离 + background shorthand  │
│                                    + z-index 精简 + 等价规则合并      │
│   Step 3.7  SemanticClassRename    .<base>__<id> → .<base>            │
│                                    产出 _class_alias_map              │
│   Step 3.8  VirtualWrapperRename   .v-stack-7 → .<语义前缀>-stack     │
│   Step 4    CssPretty              DOM 序排序 + 属性分段 + 合并组多行 │
│  + strip_dev_metadata 剥离 data-name/data-type/id="layer-*"           │
│  结果：index_optimized.html / style_optimized.css                     │
│        class_alias_map.json / layer_map.json                          │
└─────────────────────────────────────────────┘
       │
       │    ↙ 仅 --target react 继续
       ▼ Stage 5: HtmlToReactStage（仅 react target）
┌─────────────────────────────────────────────┐
│  html_to_jsx(index_optimized.html)          │
│   ├─ BeautifulSoup 解析 + 递归重写           │
│   ├─ class→className、<img>自闭合、{转义     │
│   └─ images/x.png → ./assets/images/x.png   │
│  css_to_module(style_optimized.css)         │
│   └─ url("images/...") → url("./assets/...")│
│  复制 output/<stem>/html/images/ → react/src/assets/images/ │
│  结果：react/src/App.jsx / App.css 写入      │
└──────┬──────────────────────────────────────┘
       │
       ▼ Stage 6: ReactScaffoldStage（仅 react target）
┌─────────────────────────────────────────────┐
│  写入 Vite 脚手架：                           │
│   ├─ react/package.json                      │
│   ├─ react/vite.config.js                    │
│   ├─ react/index.html                        │
│   ├─ react/src/main.jsx                      │
│   ├─ react/.gitignore                        │
│   └─ react/README.md                         │
│  结果：react/ 是独立可运行的 Vite 项目       │
└─────────────────────────────────────────────┘
       │
       │    ↙ 或 --target vue 走 Stage 5'/6'
       ▼ Stage 5'/6': HtmlToVueStage + VueScaffoldStage（仅 vue target）
┌─────────────────────────────────────────────┐
│  html_to_template / css_rewrite              │
│   → vue/src/App.vue + assets/style.css       │
│  + 复制 images/ → vue/src/assets/images/     │
│  + 写 Vite + Vue 脚手架（package.json / main.js / ...） │
└─────────────────────────────────────────────┘
```

---

## 关键事实

### 1. 图片在 Stage 2 就写盘了

`ParseToIrStage` 通过 `LayerExporter` 完成两件事：
- 产生 legacy layer 字典树（包 IR 用）
- **同步把所有图片写入 `output/<stem>/images/`**（副作用！）

因此 Stage 3/4 只处理 HTML/CSS 文本，不再碰图片。

### 2. IR 与 legacy 双轨并行（过渡态）

- `ctx.ir` 是真正的契约对象（pydantic Document）。
- 每个 IR 节点的 `meta['legacy']` 里 **完整保留** 原 legacy 字典。
- codegen 通过 `to_legacy_layers(ctx.ir)` 拿回 legacy 列表，再喂给 HTMLGenerator。
- 未来（P5+）会把更多字段从 `meta['legacy']` 提升到 IR 的一等字段，
  直到可以移除 `legacy` 逃生舱。

> 这是"保证字节级回归零差异"的工程选择，不是"糟糕设计"。
> 请在改动时保持该不变量。

### 3. LayoutOptimizeStage 是字符串后处理

它直接读 `index.html` 文本 + `style.css`，用 BeautifulSoup 解析改写，
**不回看 IR**。这意味着：

- 想让优化器感知某个语义，必须先从 codegen 把它写进 HTML（class / data-* 属性）。
- 目前通过 `data-type="image" | "text" | "group"` 等属性传递类型提示。

### 4. 流水线 Hook（Observer）

`PipelineContext.hook` 默认是 `NullHook`（no-op）。如需打印每个 Stage 的耗时，
把 `ctx.hook = LoggingHook(verbose=True)` 即可，无须改 Pipeline 源码。

典型用途：
- 本地开发：`LoggingHook` 看每个 Stage 耗时
- CI：自定义 Hook 把耗时上报到监控系统

## 产物目录

运行结束后，产物按 `target` 落到 `output/<psd_stem>/<target>/` 子目录。
以 `--target html` 为例：

```
output/<psd_stem>/
└── html/
    ├── index.html              # Stage 3 产物（原始）
    ├── style.css               # 同上
    ├── main.js                 # 同上（如有交互）
    ├── metadata.json           # 图层统计 / 画布信息
    ├── README.md               # 使用说明
    ├── _naming_report.md       # 语义命名层级报告（开启 enable_report 时）
    ├── index_optimized.html    # Stage 4 产物（默认"成品"，已剥离 dev metadata）
    ├── style_optimized.css     # 同上（CssPretty 渲染输出）
    ├── layer_map.json          # Stage 4 副产物：剥离的 data-name/data-type/layer-id 反查映射
    └── images/
        ├── *.png               # Stage 2 导出的每张图
        └── ...
```

若使用 `--target react`，目录变成：

```
output/<psd_stem>/
└── react/
    ├── (上面 html target 的全部产物作为参照基底)
    ├── index.html              # Vite 模板
    ├── package.json            # react + vite
    ├── vite.config.js
    ├── .gitignore
    ├── README.md
    └── src/
        ├── main.jsx            # 入口
        ├── App.jsx             # 由 index_optimized.html 转出
        ├── App.css             # 由 style_optimized.css 改写
        └── assets/images/      # 由同级 images/ 复制
```

`--target vue` 同理产出 `output/<psd_stem>/vue/`，结构镜像 react，差异是 `App.vue` + `main.js`。

> 用户最终看到的"成品"是 `_optimized` 版本（html target）或 `<target>/src/` 目录（react/vue target）。
> 原始 `index.html` 始终保留用于调试对比；`layer_map.json` 给 AI 排查时反查 PSD 原名 / layer_id。

## 错误处理

- **Stage 内部抛异常** → `Pipeline` 捕获 → 调用 `hook.on_error` → 继续 raise。
- **LayoutOptimizeStage 主动 try/except** → 失败时保留原始版本不崩溃。
  这是**故意**的：优化失败不应导致整次转换失败（用户至少有可用的原始版）。

## 参见

- 各 Stage 具体实现：[`../02-modules/framework.md`](../02-modules/framework.md)
  和 [`../02-modules/targets-html.md`](../02-modules/targets-html.md)
- IR 契约细节：[`../03-topics/ir-contract.md`](../03-topics/ir-contract.md)
- 布局优化器：[`../03-topics/layout-optimizer.md`](../03-topics/layout-optimizer.md)
