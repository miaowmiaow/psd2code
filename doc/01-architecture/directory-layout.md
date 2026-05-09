# 目录布局与关键文件

> **本文解决什么**：把"目录 / 文件 / 关键类函数"做成速查表，
> 让你在大脑中建立"需要改 X 功能 → 去 Y 文件"的直觉。
> **不讨论什么**：每个类的实现细节（见各模块文档）。

---

## 完整目录

```
psd2code/
├── SKILL.md
├── psd_to_code.py                       # CLI 入口；sys.dont_write_bytecode=True
├── doc/                                 # 本文档体系
├── output/                              # 运行产物（.gitignore 忽略）
└── scripts/
    ├── __init__.py
    │
    ├── common/
    │   ├── __init__.py
    │   ├── utils.py                     # reset_image_counter / sanitize_filename / make_image_filename
    │   ├── semantic.py                  # extract_semantic_token / is_default_ps_name (Fallback 层)
    │   ├── cn_dict.json                 # Layer 1 扩展词典（约 470 条，分 11 组）
    │   ├── css_utils.py                 # parse_css_to_dict / dict_to_css / extract_global_css_header
    │   └── image_utils.py               # BBoxUtils / ImageArrayUtils / ImageBlendUtils
    │
    ├── semantic/               # 【语义命名流水线】图层名 → kebab-token
    │   ├── __init__.py                  # 公开 NameResolver / NameCandidate
    │   ├── name_resolver.py             # 主入口：仲裁多层 candidate + cache + report
    │   ├── layer1_cleaner.py            # Layer 1：清洗 + cn_dict.json 词典
    │   └── layer2_role_inferer.py      # Layer 2：DOM 角色推断（按钮误判降级 / shape 按钮 / bg-section / text-block）
    │
    ├── config/
    │   ├── __init__.py                  # re-export __version__
    │   └── config.py                    # Config 类 + __version__
    │
    ├── framework/                       # 业务无关流水线抽象
    │   ├── __init__.py
    │   ├── context.py                   # PipelineContext（dataclass）
    │   ├── stage.py                     # Stage（ABC）
    │   ├── pipeline.py                  # Pipeline（执行器）
    │   └── hooks.py                     # PipelineHook / NullHook / LoggingHook
    │
    ├── core/                            # 【前端】PSD → IR
    │   ├── __init__.py
    │   ├── converter.py                 # 历史 PSDToHTMLConverter（回归兜底）
    │   │
    │   ├── ir/                          # pydantic IR
    │   │   ├── __init__.py              # re-export
    │   │   ├── document.py              # Document（根）
    │   │   ├── nodes.py                 # GroupNode/ImageNode/TextNode/ShapeNode
    │   │   ├── styles.py                # BBox / Color / FontStyle / Style
    │   │   ├── effects.py               # Stroke/DropShadow/OuterGlow/... Spec
    │   │   ├── assets.py                # AssetRef
    │   │   └── adapters.py              # to_legacy_layers()
    │   │
    │   ├── psd/                         # PSD 解析
    │   │   ├── __init__.py
    │   │   ├── parser.py                # parse_psd_to_ir()
    │   │   ├── classifier.py            # LayerClassifier
    │   │   └── text_extractor.py        # TextExtractor
    │   │
    │   ├── render/                      # 像素渲染
    │   │   ├── __init__.py
    │   │   ├── layer_renderer.py        # GroupRenderer（组级扩展渲染）
    │   │   └── effects/
    │   │       ├── __init__.py
    │   │       ├── effect_base.py       # 效果渲染器共用基类
    │   │       ├── effects_renderer.py  # 【Facade】render_layer_with_effects()
    │   │       ├── stroke_renderer.py   # 描边
    │   │       ├── shadow_renderer.py   # 投影 / 内阴影
    │   │       ├── glow_renderer.py     # 外发光 / 内发光
    │   │       └── overlay_renderer.py  # 颜色叠加 / 渐变叠加
    │   │
    │   └── extract/                     # 资源提取
    │       ├── __init__.py
    │       ├── layer_exporter.py        # 【核心】LayerExporter（调度 handlers + 渲染）
    │       ├── image_ops.py             # 裁剪 / 蒙版 / numpy alpha 合成
    │       └── handlers.py              # 【决策链】5 个 LayerHandler
    │
    └── targets/                         # 【后端】按产物分包
        ├── __init__.py
        ├── base.py                      # Target（ABC）
        ├── registry.py                  # _REGISTRY + @register 装饰器
        ├── html/
        │   ├── __init__.py              # 触发 HtmlTarget 注册
        │   ├── target.py                # HtmlTarget
        │   ├── pipeline.py              # 4 个 Stage + build_html_pipeline()
        │   │
        │   ├── codegen/                 # IR → HTML/CSS/JS
        │   │   ├── __init__.py
        │   │   ├── html_generator.py    # HTMLGenerator（Composition）
        │   │   ├── context.py           # CodegenContext（共享状态）
        │   │   ├── layer_renderer.py    # LayerRenderer（按 kind 分派）
        │   │   ├── html_builder.py      # HtmlBuilder（字符串拼装）
        │   │   ├── naming.py            # SimpleNamer
        │   │   ├── escape.py            # _esc()
        │   │   ├── version.py           # __version__
        │   │   └── renderers/           # 【Strategy】按节点类型分渲染
        │   │       ├── __init__.py
        │   │       ├── base.py          # NodeRenderer（ABC）
        │   │       ├── css_helpers.py   # 共享 position/size CSS 行构造
        │   │       ├── group_renderer.py
        │   │       ├── image_renderer.py
        │   │       └── text_renderer.py
        │   │
        │   ├── postprocess/
        │   │   ├── __init__.py
        │   │   ├── strip_dev_metadata.py    # emit 前剥离 data-name/data-type/id="layer-*" → layer_map.json
        │   │   ├── background_compose.py    # 多 url 背景 → 单 PNG 合成（共享 utility）
        │   │   ├── background_flatten.py    # CSS 文本兜底入口
        │   │   └── layout_optimizer/
        │   │       ├── __init__.py      # 导出 optimize_layout
        │   │       ├── optimizer.py     # LayoutOptimizer（12 段协调器）
        │   │       ├── analyzers/       # 分析阶段（只读）
        │   │       │   └── layout_analyzer.py   # 行/列趋势识别 + V8/V9/V10 安全闸门
        │   │       ├── transformers/    # 变换阶段（写入 soup / css_rules / 输出最终 CSS 字符串）
        │   │       │   ├── dom_restructure.py        # DOM 重构：背景剥离 + 高瘦装饰剥离 + 切行列 + 容器背景吸收 + Stack→Col 升级 + flex 早退撤销
        │   │       │   ├── pure_image_group_flatten.py # 纯图层组扁平化（旧路径）
        │   │       │   ├── image_layer_flatten.py    # ★ 图层扁平化统一通道（递归 + _PARENT_BLOCKING_PROPS 白名单）
        │   │       │   ├── sibling_group_detector.py # 兄弟同质簇 → v-list 包装（5 条 AND）
        │   │       │   ├── flex_applier.py            # 趋势 → margin / 非趋势 → absolute；flex-shrink:0 硬约束；v-stack/v-list wrapper position 保护
        │   │       │   ├── wrapper_collapse.py       # ★ 单子 v-row/v-col wrapper 折叠
        │   │       │   ├── css_dedup.py               # 默认值剔除 + background shorthand + z-index 精简 + 等价规则合并
        │   │       │   ├── position_noise_relaxer.py  # ★ 位置噪声宽容合并（margin 偏差 ≤ 8px → 众数归一；⚠ 唯一引入视觉差异的步骤）
        │   │       │   ├── repeat_class_unifier.py   # ★ ≥3 个等价 hash 类合并为单一语义类
        │   │       │   ├── semantic_class_rename.py  # ★ 剩余 .<base>__<id> → .<base>；产出 class_alias_map.json
        │   │       │   ├── virtual_wrapper_rename.py # ★ .v-stack-7 → .<语义前缀>-stack
        │   │       │   └── css_pretty.py              # CSS 美化渲染（双预设 compact / expanded）
        │   │       └── utils/
        │   │           └── css_parser.py        # （内部辅助）
        │   │
        │   └── emit/
        │       └── __init__.py          # 预留：写盘抽象
        │
        ├── react/
        │   ├── __init__.py              # 触发 ReactTarget 注册
        │   ├── target.py                # ReactTarget
        │   ├── pipeline.py              # 复用 html 的前 3 Stage + 新增 2 Stage
        │   ├── stages.py                # HtmlToReactStage + ReactScaffoldStage
        │   └── codegen/
        │       ├── __init__.py
        │       ├── html_to_jsx.py       # BeautifulSoup 驱动的 HTML → JSX
        │       └── css_to_module.py     # CSS url() 路径改写
        │
        └── vue/
            ├── __init__.py              # 触发 VueTarget 注册
            ├── target.py                # VueTarget
            ├── pipeline.py              # 复用 html 前 3 Stage + 新增 2 Stage
            ├── stages.py                # HtmlToVueStage + VueScaffoldStage
            └── codegen/
                ├── __init__.py
                ├── html_to_template.py  # BeautifulSoup 驱动的 HTML → <template>
                └── css_rewrite.py       # CSS url() / 选择器改写
```

---

## 功能 → 改什么文件？

### 加一个新产物（Vue / React）

- 新增 `scripts/targets/<name>/target.py`
- `@register("<name>")` 的子类
- 实装 `build_pipeline()` 装配至少 3 个 Stage：加载 / IR→代码 / 后处理
- 在 `psd_to_code.py` 顶部 import 触发注册
- **参考已有实现**：`targets/react/` 是"基于 HTML 产物二次加工"的模板，
  `targets/html/` 是"直接从 IR 生成"的模板。
- 详见：[`../04-extending/add-a-target.md`](../04-extending/add-a-target.md)

### 改 React 产物（JSX 样式 / 脚手架）

- JSX 转换规则：`targets/react/codegen/html_to_jsx.py`
- CSS 路径改写：`targets/react/codegen/css_to_module.py`
- Vite 模板：`targets/react/stages.py` 里的 `_MAIN_JSX` / `_PACKAGE_JSON` / `_VITE_CONFIG` 等常量

### 改 PSD 怎么解析

- `core/psd/parser.py` —— 主入口
- `core/psd/classifier.py` —— 图层类型判定
- `core/psd/text_extractor.py` —— 文本抽取

### 改图层导出 / 合并策略

- `core/extract/layer_exporter.py` —— 编排
- `core/extract/handlers.py` —— **决策链**。99% 的改动应该落在这里，加一个 Handler
- `core/extract/image_ops.py` —— 底层 numpy 操作

### 改像素渲染 / 新增效果

- `core/render/effects/effects_renderer.py` —— Facade 入口
- `core/render/effects/<xxx>_renderer.py` —— 具体效果
- `core/render/layer_renderer.py` —— 组级扩展渲染

### 改 HTML 输出

- `targets/html/codegen/html_generator.py` —— 编排
- `targets/html/codegen/renderers/` —— 按节点类型分渲染
- `targets/html/codegen/html_builder.py` —— 最终字符串拼装

### 改布局优化

- `targets/html/postprocess/layout_optimizer/optimizer.py` —— 12 段协调器（DOMRestructure → ImageLayerFlatten → SiblingGroupDetector → FlexApplier → WrapperCollapse → CssDedup → PositionNoiseRelaxer → RepeatClassUnifier → SemanticClassRename → VirtualWrapperRename → CssPretty）
- `.../transformers/dom_restructure.py` —— DOM 重构：背景剥离、高瘦装饰剥离、行/列切分、容器背景吸收 pass、Stack→Col 反向升级、`_can_flex_applier_handle` 早退撤销、`_is_stack_group` 零面积过滤
- `.../transformers/image_layer_flatten.py` —— ★ 图层扁平化统一通道（递归 + 后序遍历 + `_PARENT_BLOCKING_PROPS` 白名单）
- `.../transformers/sibling_group_detector.py` —— 兄弟同质簇 → v-list 包装（5 条 AND 判据）
- `.../transformers/flex_applier.py` —— flex 推断 + 趋势/非趋势元素分流 + v-stack/v-list wrapper position 保护 + flex-shrink:0 硬约束
- `.../transformers/wrapper_collapse.py` —— ★ 单子 v-row / v-col wrapper 折叠（v-stack / v-list / 根 layer-group 不动）
- `.../transformers/css_dedup.py` —— Pass 0a 默认值剔除 + Pass 0b background shorthand + Pass 1 z-index 精简 + Pass 2 等价规则合并
- `.../transformers/position_noise_relaxer.py` —— ★ 位置噪声宽容合并（margin 偏差 ≤ 8px 同 base 组 → 众数归一；⚠ 唯一引入视觉差异的步骤）
- `.../transformers/repeat_class_unifier.py` —— ★ ≥3 个等价 hash 类合并为单一语义类（自动派生类 `v-stack-N / v-row-N / v-col-N` 不参与）
- `.../transformers/semantic_class_rename.py` —— ★ 剩余 `.<base>__<id>` 去后缀为 `.<base>`（冲突用 `-2/-3`）；产出 `class_alias_map.json`
- `.../transformers/virtual_wrapper_rename.py` —— ★ `.v-stack-7` / `.v-row-3` / `.v-col-12` → `.<语义前缀>-stack / -row / -col`（子孙优先，fallback 祖先）
- `.../transformers/css_pretty.py` —— CSS 文本美化（5 Pass + 双预设 compact / expanded，CLI `--css-style`）
- `.../analyzers/layout_analyzer.py` —— 行/列趋势识别 + V8/V9/V10 安全闸门（只读；历史 V11/V12 Grid 识别已回滚）
- `.../utils/css_parser.py` —— 内部辅助；CSS 文本/字典互转走 `common/css_utils.py`
- ⚠️ overflow / border-radius 已迁移到源头 `targets/html/codegen/renderers/group_renderer.py`，**不要** 在 LayoutOptimizer 里再加同名 pass

### 改 IR 契约

- `core/ir/*.py` —— pydantic 数据类
- 记得同步更新 `adapters.py` 和 `core/psd/parser.py` 的映射
- 需回归所有 target 的 baseline diff

### 改图层语义命名（class 名 / 图片文件名）

- `common/cn_dict.json` —— **加新词只改这里**（无需改 Python）
- `semantic/layer1_cleaner.py` —— 清洗规则（emoji / 拷贝 / 数字编号）
- `semantic/layer2_role_inferer.py` —— DOM 角色规则与阈值
- `semantic/name_resolver.py` —— 多层仲裁 + 缓存 + report
- 调试：`<output>/_naming_report.md` 看每个图层走的是哪层、给出什么 token
- 详见：[`../02-modules/semantic.md`](../02-modules/semantic.md)

### 加全局配置

- `config/config.py` —— `Config` 类

## 入口流程全调用

```
psd_to_code.main()
  ├─ parse_args()
  ├─ registry.get("<target>") → HtmlTarget | ReactTarget | VueTarget
  ├─ Target().run(ctx)
  │    └─ build_<target>_pipeline(ctx).run(ctx)
  │         ├─ LoadPsdStage.run(ctx)
  │         ├─ ParseToIrStage.run(ctx)
  │         │    └─ parse_psd_to_ir(psd_path, out, psd)
  │         │         └─ LayerExporter.export_layers(psd)
  │         │              └─ run_handlers([...], ctx)
  │         ├─ HtmlCodegenStage.run(ctx)
  │         │    └─ HTMLGenerator.generate_html(legacy_layers)
  │         │         └─ LayerRenderer → renderers/*
  │         ├─ LayoutOptimizeStage.run(ctx)
  │         │    └─ optimize_layout(html, css, global_header, pretty_config, ...)
  │         │         ├─ DOMRestructure
  │         │         ├─ ImageLayerFlatten
  │         │         ├─ SiblingGroupDetector
  │         │         ├─ FlexApplier
  │         │         ├─ WrapperCollapse
  │         │         ├─ CssDedup
  │         │         ├─ PositionNoiseRelaxer   (⚠ 唯一引入 ≤ 8px 视觉差异的步骤)
  │         │         ├─ RepeatClassUnifier
  │         │         ├─ SemanticClassRename     → _class_alias_map
  │         │         ├─ VirtualWrapperRename    → _class_alias_map
  │         │         └─ CssPretty
  │         │    ├─ strip_and_collect → layer_map.json
  │         │    └─ write class_alias_map.json（若 _class_alias_map 非空）
  │         │
  │         ├─ (react only) HtmlToReactStage.run(ctx)
  │         │    ├─ html_to_jsx(index_optimized.html) → App.jsx
  │         │    ├─ css_to_module(style_optimized.css) → App.css
  │         │    └─ copy images/ → react/src/assets/images/
  │         │
  │         ├─ (react only) ReactScaffoldStage.run(ctx)
  │         │    └─ 写 package.json / vite.config.js / index.html / main.jsx / README.md
  │         │
  │         ├─ (vue only) HtmlToVueStage.run(ctx)
  │         │    ├─ html_to_template(index_optimized.html) → App.vue
  │         │    ├─ css_rewrite(style_optimized.css) → assets/style.css
  │         │    └─ copy images/ → vue/src/assets/images/
  │         │
  │         └─ (vue only) VueScaffoldStage.run(ctx)
  │              └─ 写 package.json / vite.config.js / index.html / main.js / README.md
  └─ 打印 Done
```
