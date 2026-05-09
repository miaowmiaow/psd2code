# 模块：`targets/html/`

> **本文解决什么**：讲清 HTML target 的三大子系统：**pipeline / codegen / postprocess**。
> **不讨论什么**：通用 Target 抽象（见 `../01-architecture/overview.md` 与 `../04-extending/add-a-target.md`）。

## 位置

```
targets/html/
├── __init__.py          # 触发 HtmlTarget 注册
├── target.py            # @register("html") class HtmlTarget
├── pipeline.py          # 4 个 Stage + build_html_pipeline()
├── codegen/
│   ├── html_generator.py   # HTMLGenerator（Composition）
│   ├── context.py          # CodegenContext
│   ├── layer_renderer.py   # LayerRenderer（分派中枢）
│   ├── html_builder.py     # 字符串拼装
│   ├── naming.py           # SimpleNamer
│   ├── escape.py           # _esc
│   ├── version.py
│   └── renderers/          # NodeRenderer 策略
│       ├── base.py            # NodeRenderer + RendererRegistry + register_renderer
│       ├── css_helpers.py     # 共享 position/size CSS 行构造
│       ├── group_renderer.py
│       ├── image_renderer.py
│       └── text_renderer.py
├── postprocess/
│   ├── strip_dev_metadata.py   # emit 阶段剥离 data-name/data-type/id="layer-*" → layer_map.json
│   ├── background_compose.py   # 多 url 背景 → 单 PNG 合成（共享 utility）
│   ├── background_flatten.py   # CSS 文本兜底入口（主路径在 dom_restructure 内联）
│   └── layout_optimizer/
│       ├── __init__.py         # 导出 optimize_layout
│       ├── optimizer.py        # LayoutOptimizer（12 段协调器）
│       ├── analyzers/
│       │   └── layout_analyzer.py   # 行/列趋势识别 + V8/V9/V10 安全闸门（只读）
│       ├── transformers/
│       │   ├── dom_restructure.py        # DOM 重构：背景剥离 + 高瘦装饰剥离 + 切行列 + 容器背景吸收 + Stack→Col 升级 + flex 早退撤销
│       │   ├── pure_image_group_flatten.py # 纯图层组扁平化（旧路径，逐步被 image_layer_flatten 接管）
│       │   ├── image_layer_flatten.py    # ★ 图层扁平化统一通道（递归 + 后序 + _PARENT_BLOCKING_PROPS 白名单）
│       │   ├── sibling_group_detector.py # 兄弟同质簇 → v-list 包装（5 条 AND 判据）
│       │   ├── flex_applier.py           # 趋势元素 → margin / 非趋势元素 → absolute；所有 flex 子元素写 flex-shrink:0；含 v-stack wrapper position 保护
│       │   ├── wrapper_collapse.py       # ★ 单子 v-row/v-col wrapper 折叠（v-stack/v-list 不动）
│       │   ├── css_dedup.py              # 默认值剔除 + background shorthand + z-index 精简 + 等价规则合并
│       │   ├── position_noise_relaxer.py # ★ 位置噪声宽容合并：margin 偏差 ≤ 8px 同 base 组 → 众数归一（⚠ 唯一引入视觉差异的步骤）
│       │   ├── repeat_class_unifier.py   # ★ ≥3 个等价 hash 类合并为单一语义类
│       │   ├── semantic_class_rename.py  # ★ 剩余 .<base>__<id> → .<base>；产出 class_alias_map.json
│       │   ├── virtual_wrapper_rename.py # ★ .v-stack-7 → .<语义前缀>-stack；子孙优先，fallback 祖先
│       │   └── css_pretty.py             # CSS 美化渲染：双预设 compact / expanded
│       └── utils/
│           └── css_parser.py             # 内部辅助
└── emit/
    └── __init__.py          # 预留
```

---

## Target 注册

`targets/html/target.py`：

```python
@register("html")
class HtmlTarget(Target):
    def build_pipeline(self, ctx):
        return build_html_pipeline(ctx)
```

`targets/html/__init__.py` 会 `from .target import HtmlTarget` 触发装饰器。
入口 `psd_to_code.py` 顶部 `import targets.html` 触发整条链，完成注册。

## Pipeline

`targets/html/pipeline.py` 定义并装配 4 个 Stage：

| Stage | 职责 | 读 ctx | 写 ctx |
| ----- | ---- | ------ | ------ |
| `LoadPsdStage` | `PSDImage.open`；清理并创建输出目录 | `psd_path`, `output_dir` | `psd`, `output_dir` |
| `ParseToIrStage` | PSD → IR；**副作用：导出所有图片** | `psd`, `output_dir` | `ir`, `artifacts.layer_exporter`, `artifacts.legacy_layers` |
| `HtmlCodegenStage` | IR → HTML/CSS/JS/metadata/README | `ir`, `artifacts.layer_exporter` | `artifacts.html_generator`, `artifacts.html_path` |
| `LayoutOptimizeStage` | 后处理：DOM 重构 + 图层扁平化 + 兄弟同质簇 + Flex 推断 + Wrapper 折叠 + CSS 去冗余 + 位置噪声宽容合并 + 重复元素抽取 + 语义类去后缀 + 虚拟 wrapper 命名 + CSS 美化 + dev metadata 剥离 | `artifacts.html_path`, `artifacts.css_pretty_enabled?`, `artifacts.css_pretty_style?` | `artifacts.html_path`（覆盖为 optimized）, `artifacts.layout_stats`；副作用产物：`layer_map.json`、`class_alias_map.json` |

`LayoutOptimizeStage` 主动 try/except，失败时保留原始 `html_path`，**不** 抛出。

## Codegen

### HTMLGenerator（Composition）

位置：`targets/html/codegen/html_generator.py`

```python
class HTMLGenerator:
    def __init__(self, psd_width, psd_height, output_dir, psd_name):
        self.ctx = CodegenContext(...)
        self._layer_renderer = LayerRenderer(self.ctx)
        self._html_builder = HtmlBuilder(self.ctx)

    def generate_html(self, layers_tree) -> str: ...
    def generate_metadata(self, layers_tree, exported, skipped) -> None: ...
    def generate_readme(self, exported, skipped) -> None: ...
```

**顶层流程：**

1. `self.ctx.reset()`
2. 遍历 `layers_tree` 调 `_layer_renderer.render(layer, indent, parent, siblings)`
3. 写 `style.css`、`main.js`、`index.html`

### CodegenContext

位置：`targets/html/codegen/context.py`

```python
@dataclass
class CodegenContext:
    psd_width / psd_height / output_dir / psd_name
    namer: SimpleNamer
    css_rules: list[str]
    def reset(self): ...
```

让原本散落在 HTMLGenerator 里的状态显式集中，避免 mixin 的隐式耦合。

### LayerRenderer + NodeRenderer（Strategy + Registry）

`targets/html/codegen/renderers/base.py` 定义 `NodeRenderer` 基类和 `RendererRegistry`，
各子类通过 `@register_renderer("group")` / `"image"` / `"text"` 自注册。

`LayerRenderer.render(layer, indent, parent, siblings)` 按 `layer['type']` 查表分派。

**新增节点类型**：加一个 `NodeRenderer` 子类并用 `@register_renderer("xxx")` 注册即可。

### 辅助组件

| 组件 | 位置 | 作用 |
| ---- | ---- | ---- |
| `SimpleNamer` | `naming.py` | 给图层生成 class 名（拼音 + 唯一后缀） |
| `_esc` | `escape.py` | HTML 属性转义 |
| `HtmlBuilder` | `html_builder.py` | 拼装最终 HTML/CSS/JS 字符串 |

### 向后兼容接口

`HTMLGenerator` 保留了一批 property/delegate（如 `self.namer`, `self._css_rules`,
`self._render_layer`）让旧测试/脚本仍然可用。**新代码应直接使用 `self.ctx` 与组件**。

## Postprocess：LayoutOptimizer

位置：`targets/html/postprocess/layout_optimizer/`

```python
def optimize_layout(
    html, css_rules,
    *, global_header="", pretty_config=None,
    repeat_unify_config=None,
    semantic_rename_config=None,          # Step 3.7
    virtual_wrapper_rename_config=None,   # Step 3.8
    position_relaxer_config=None,         # Step 3.3（唯一引入视觉差异的步骤）
    images_dir=None, flatten_config=None,
) -> tuple[html, css_rules, stats]:
    ...
```

协调器 `LayoutOptimizer` 串 12 段写入步骤（早期设计中的 wrapper_creator 等已合并到 `dom_restructure` 内，无独立文件）：

### Analyzers（只读）

| 文件 | 职责 |
| ---- | ---- |
| `layout_analyzer.py` | 行/列趋势识别；输出 children_info（含 `class` / `classes` / 几何 / `data_type` / `is_trend` 等字段，供下游 flex_applier 使用）；V8（堆叠装饰组）/ V9（支配背景层）/ V10（装饰剥离）安全闸门；历史上的 V11/V12 Grid 识别已回滚，仅保留 `grid-row-N` / `v-grid-row` 兼容类名 |

### Transformers（写入 soup / css_rules）

| 文件 | 职责 |
| ---- | ---- |
| `dom_restructure.py`         | DOM 重构：背景剥离（3 条规则）→ 高瘦跨行装饰剥离（4 条 AND）→ 按行/列重叠率切簇 → 容器背景吸收 pass → Stack→Col 反向升级（N=2 双强信号）→ `_can_flex_applier_handle` 早退撤销；`_is_stack_group` 过滤零面积 bbox |
| `image_layer_flatten.py` ★   | **图层扁平化统一通道**：递归后序遍历每个容器，把"全 image 子簇"合成单 PNG；`_PARENT_BLOCKING_PROPS` 白名单防止把 border-radius / box-shadow / clip-path / filter / transform / mask 错误地烧进 PNG |
| `sibling_group_detector.py`  | 兄弟同质簇检测：同 v-row 父下非趋势子节点若满足"同 data-type / 几何相近 / X 间距规整 / Y 共线 / N≥3"则包成 v-list wrapper |
| `flex_applier.py`            | 趋势元素 → flex 流（margin 间距）；非趋势元素保留原 `top/left` 走 absolute；**所有 flex 子元素写入 `flex-shrink: 0`（硬约束）**；v-stack/v-list/v-row/v-col wrapper 即使在父容器 flex 化时也保持 `position:relative`（保留 containing block）|
| `wrapper_collapse.py` ★      | 单子 v-row / v-col wrapper 折叠（margin 数值合并到子节点）；**v-stack / v-list / 根 layer-group 不动**；多轮扫描最多 5 轮直到稳定 |
| `css_dedup.py`               | Pass 0a 删 CSS 默认值（`opacity:1` / `mix-blend-mode:normal`）；Pass 0b 合 background shorthand；Pass 1 z-index 精简；Pass 2 等价规则合并到 `_css_merge_groups` |
| `position_noise_relaxer.py` ★| **位置噪声宽容合并**（唯一引入视觉差异的 transformer）：同 base + 非位置签名相同 + margin 偏差 ≤ 8px 的组 → 取众数 margin + z-index 清零；用 N→1 样式复用换亚像素精度容忍 |
| `repeat_class_unifier.py` ★  | 把 ≥3 个等价 hash 类（`.prop__68 / .prop__105 / .prop__142 / ...`）合并为单一语义类（`.prop`）；自动派生类 `v-stack-N / v-row-N / v-col-N` 故意不参与 |
| `semantic_class_rename.py` ★ | 把剩余 `.<base>__<id>` 改名为 `.<base>`（冲突用 `-2 / -3 / ...`）；旁路产出 `stats['_class_alias_map']`，写盘为 `class_alias_map.json` |
| `virtual_wrapper_rename.py` ★| `.v-stack-7` / `.v-row-3` / `.v-col-12` → `.<语义前缀>-stack / -row / -col`；前缀优先子孙语义 class，fallback 祖先 |
| `css_pretty.py`              | 5 Pass 美化文本（不改 css_rules 字典）；双预设 `compact`（默认）/ `expanded` |

### 流程

```
LayoutOptimizer.optimize():
  step 1    DOMRestructure.restructure_dom()
            ├─ 背景剥离 → bg-section 写入父 background-image
            ├─ 高瘦跨行装饰剥离 → 单独成 v-stack
            ├─ 切行/列簇 → v-row / v-col / v-stack
            ├─ 容器背景吸收 pass（再次扫描，剥同尺寸大背景为父 background）
            ├─ Stack→Col 反向升级（N=2 双强信号）
            └─ 早退撤销：_can_flex_applier_handle 探测到 flex → 跳过叠图组早退
  step 1.2  ImageLayerFlatten.run()
            └─ 递归后序遍历，把"容器 bg + 全 image 子簇"合成单 PNG；可上提/吸收父
  step 1.5  SiblingGroupDetector.detect_and_wrap()
            └─ 5 条 AND：同 type / 同高度 ±tol / 等间距 / Y 共线 / N≥3 → 包 v-list
  step 2    FlexApplier.apply_flex_layouts()
            └─ LayoutAnalyzer 判趋势 → _apply_vertical / _apply_horizontal
               / _handle_non_flex_container；v-stack 例外保护 position:relative；
               所有 flex 子元素写 flex-shrink:0
  step 2.5  WrapperCollapse.run()
            └─ 单子 v-row / v-col wrapper 折叠（margin 合并），多轮扫描
  step 3    CssDedup.run() → 写 stats['_css_merge_groups']
  step 3.3  PositionNoiseRelaxer.run()                  ⚠ 引入 ≤ 8px 视觉差异
            └─ 同 base + 非位置签名相同 + margin 偏差 ≤ 8px → 众数归一
  step 3.5  RepeatClassUnifier.run()
            └─ ≥ 3 个等价 hash 类合并为单一语义类
  step 3.7  SemanticClassRename.run()
            └─ 剩余 .<base>__<id> → .<base>（冲突 -2/-3）；产出 _class_alias_map
  step 3.8  VirtualWrapperRename.run()
            └─ .v-stack-7 → .<语义前缀>-stack
  step 4    CssPretty.render() → 写 stats['_pretty_css']
  return (str(soup), css_rules, stats)
```

> 任一步抛异常都会被 `optimize()` 自身 try/except 吞掉并打印 traceback，**不**影响
> 整条 Pipeline；外层 `LayoutOptimizeStage` 还有第二层兜底。
>
> 算法细节、闸门判据、4 条 AND 等详见
> [`../03-topics/layout-optimizer.md`](../03-topics/layout-optimizer.md)。

### stats 字段

`optimize_layout` 返回的 `stats: dict` 现含：

| 键 | 含义 |
| --- | --- |
| `dom_restructured`                | step 1 重构的 group 数 |
| `image_layer_containers_flattened`| step 1.2 合并的"全 image 子簇"容器数 |
| `image_layer_layers_collapsed`    | step 1.2 折叠的 image 子图层数 |
| `image_layer_bytes_saved`         | step 1.2 节省的字节数 |
| `bg_inline_flatten`               | step 1 / 1.2 内联背景合成统计 `{rules_flattened, layers_collapsed, bytes_saved}` |
| `sibling_lists_created`           | step 1.5 包出的 v-list wrapper 数 |
| `sibling_items_wrapped`           | step 1.5 被包进 v-list 的节点数 |
| `flex_applied`                    | step 2 改写为 flex 的容器数 |
| `positions_removed`               | step 2 删除的 `position: absolute` 数 |
| `wrappers_collapsed`              | step 2.5 折叠的单子 v-row / v-col 数 |
| `css_defaults_stripped`           | step 3 Pass 0a 删的默认值字段数 |
| `background_shorthand_merged`     | step 3 Pass 0b 合并为 shorthand 的规则数 |
| `z_index_pruned`                  | step 3 Pass 1 删除的 z-index 字段数 |
| `css_rules_merged`                | step 3 Pass 2 合并节省的 CSS 块数 |
| `position_relaxed_groups`         | step 3.3 归一的组数（启用 PositionNoiseRelaxer 时）|
| `position_relaxed_classes`        | step 3.3 归一覆盖的类总数 |
| `classes_unified`                 | step 3.5 删除的 hash 类数 |
| `elements_unified`                | step 3.5 被改写 class 的 HTML 元素总数 |
| `repeat_groups_unified`           | step 3.5 处理的合并组数 |
| `semantic_class_renamed`          | step 3.7 去后缀改写的类数 |
| `virtual_wrapper_renamed`         | step 3.8 重命名的虚拟 wrapper 数 |
| `_class_alias_map`（私有）         | step 3.7 / 3.8 旧 → 新类名映射，写盘为 `class_alias_map.json` |
| `_css_merge_groups`（私有）        | step 3 → 3.3 / 3.5 / 4 / 写盘的合并组列表（list[list[str]]）|
| `_pretty_css`（私有）              | step 4 渲染好的最终 CSS 字符串；为空表示降级到 `dict_to_css` |

> **overflow / border-radius 处理已迁移到源头** `targets/html/codegen/renderers/group_renderer.py`
> （emit 阶段直接补 `overflow:hidden` 与 `border-radius`），`dom_restructure.py` 已删除
> 历史上的 `_fix_overflow_after_restructure`。**不要** 在 LayoutOptimizer 里再加同名 pass。

## Postprocess：CssPretty（CSS 美化渲染）

位置：`targets/html/postprocess/layout_optimizer/transformers/css_pretty.py`

```python
class CssPretty:
    def __init__(self, soup, css_rules, merge_groups=None, global_header="", config=None): ...
    def render(self) -> str: ...

@dataclass
class CssPrettyConfig:
    style: str = "compact"                      # ★ "compact"（默认） / "expanded"

    enabled: bool = True
    file_skeleton: bool = True                  # Pass 1：Reset / @media / #canvas / 图层 四段
    dom_order: bool = True                      # Pass 2：图层规则按 DOM 顺序
    section_comments: bool = True               # Pass 2 子开关：版块注释
    section_comment_style: Optional[str] = None # Pass 2 子开关：framed / single（由 style 决定）
    property_grouping: Optional[bool] = None    # Pass 3：定位 / 盒模型 / 排版 / 外观 / 混合（由 style 决定）
    property_grouping_min_props: int = 8        # 块内属性 < 8 时走紧凑（不分段）
    merge_group_multiline: bool = True          # Pass 4：合并组多行
    multiline_threshold: Optional[int] = None   # 由 style 决定（compact=4 / expanded=3）
    short_rule_inline: bool = True              # Pass 5：1 选择器 + 属性 ≤ N → 单行
    short_rule_max_props: Optional[int] = None  # 由 style 决定（compact=6 / expanded=2）
    coord_provenance: Optional[bool] = None     # 坐标溯源注释（由 style 决定）
    coord_provenance_only_first: bool = True    # 同类被多元素复用时只输出第 1 个的注释
```

**双预设的核心区别**：

| 维度 | compact（默认）| expanded |
| ---- | ----------- | -------- |
| 段标题样式 | 单行 `/* ---- xxx ---- */` | 4 行框框注释 |
| 属性分段 | 关闭（紧凑顺序输出） | 启用（段间空行）|
| 短规则单行阈值 | ≤ 6 字段 | ≤ 2 字段 |
| 合并组多行阈值 | ≥ 4 成员 | ≥ 3 成员 |
| 坐标溯源注释 | 关闭 | 启用 |
| 行数（南瓜大作战 H5）| ~1499 行 | ~5000 行 |

`__post_init__` 仅对 `None` 字段填充预设值，可"既要紧凑又要某项展开"：

```python
CssPrettyConfig(style="compact", coord_provenance=True)   # 紧凑 + 保留溯源
CssPrettyConfig(style="expanded", short_rule_max_props=4) # 全展开但短规则更宽松
```

**目的**：`dict_to_css` 是字母序机械渲染（diff 稳定但**对人不友好**）：281 个块按字母序，
找"页面顶部头像"要在文件中段查；同块内 `position` 被字母序拆到 `background-*` 之前；
合并组 289 字节单行选择器 grep / 折叠都吃力。CssPretty 在保持 W3C 等价的前提下重排：

| Pass | 改善 | 例子 |
| ---- | ---- | ---- |
| 1 文件骨架 | 固定 4 段 + 醒目分隔注释 | `Reset → @media → #canvas → 图层规则` |
| 2 DOM 序 | 按 `index_optimized.html` 自顶向下出现顺序排列；`bankuai-*` / `section-*` 边界插版块注释 | 找版块 1 的 button 直接翻到该段 |
| 3 属性分段 | 按 `定位 / 盒模型 / 排版 / 外观 / 混合 / 其他` 分段，段内空一行（仅 expanded 启用） | `position; left; top; z-index` 集中在块顶 |
| 4 合并组多行 | 成员 ≥ `multiline_threshold` 时选择器逐行 + `/* ↳ N 个等价规则 */` | grep / git diff / 折叠都友好 |
| 5 短规则单行 | 1 选择器 + 属性 ≤ `short_rule_max_props` → `.foo { color: red; }` | 占位 / 工具类不撑文件 |

**接入流程**（LayoutOptimizer step 4）：
```
DOMRestructure → ImageLayerFlatten → SiblingGroupDetector → FlexApplier → WrapperCollapse
    → CssDedup → PositionNoiseRelaxer → RepeatClassUnifier → SemanticClassRename → VirtualWrapperRename → CssPretty
```

`LayoutOptimizer.optimize()` 把渲染好的字符串放在 `stats['_pretty_css']`；
`LayoutOptimizeStage` / `core/converter.py` 写盘前**优先取该字符串**，为空才降级到
`dict_to_css(...)`。所以 CssPretty 抛任何异常都不会阻断生成。

**与下游的关系**：CssPretty 只改"文本排版"，**不改** `css_rules` 字典本身。
react / vue target 仍然按 selector 查样式，零影响。

**回归保障**：
- **CSS 语义等价**：用 cssutils 解析 CssPretty 与 dict_to_css 输出，462/462 selector
  完全一致、0 属性差异（南瓜大作战 H5 实测）。
- **像素级一致**：Playwright 截图比对 compact / expanded / dict_to_css 三种产物，
  南瓜大作战 H5 都是 **0 像素差异**（5083500 像素全等）。

**配置开关**：
- CLI：
  - `--css-style compact|expanded`（默认 compact）切换预设；
  - `--no-css-pretty` 一键关闭，回到 `dict_to_css` 输出（CI 基线对比专用）。
- 通过 `ctx.set("css_pretty_enabled", False)` / `ctx.set("css_pretty_style", "expanded")` 编程控制。
- 单 Pass 关闭：构造 `CssPrettyConfig(property_grouping=False, ...)` 传给
  `optimize_layout(pretty_config=...)`。

**⚠️ 历史踩坑**：CssDedup Pass 0a 删除"等于 CSS 默认值"的字段时，**`background-repeat: no-repeat` 不能删！** 因为 `background-repeat` 的 CSS 默认值是 `repeat`（不是 `no-repeat`），删除会让单张大背景图被浏览器平铺。详见 [`../03-topics/layout-optimizer.md`](../03-topics/layout-optimizer.md) "Step 4：CSS 美化（CssPretty）" 章节。

**排查提示**：
- 优化版样式没生效 → 看 `index_optimized.html` 的 `<link>` 是否指向 `style_optimized.css`
  且 CSS 头部有 `/* 1. Reset & 全局 */` 标志。
- CssPretty 失败时控制台会打印 `⚠️  CSS 美化失败（降级到 dict_to_css）`，文件仍正常产出。
- 想看每条规则属于哪个 PSD 图层 → 用 `--css-style expanded` 启用坐标溯源注释。
- 如果某条规则在 DOM 序里"消失"，先看它是否在 `CssDedup` 的合并组中（在它的"组首"位置出现）；
  若不在合并组也不在 DOM → 进入文件末尾 `/* ---- 版块: 工具类 / 合并组（跨版块复用） ---- */` 段。

## Postprocess：class_alias_map.json 落盘

`LayoutOptimizeStage` / `core/converter.py::_apply_layout_optimization` 在写盘 `index_optimized.html` 之前，读 `stats['_class_alias_map']`（由 `SemanticClassRename` / `VirtualWrapperRename` 填充），把**旧 → 新** 类名映射写到 `class_alias_map.json`：

```json
{
  "nickname__37": "nickname",
  "nickname__142": "nickname-2",
  "btn-receive__128": "btn-receive",
  "v-stack-7": "nickname-stack",
  "v-col-12": "prop-col"
}
```

用途：
- 外部工具（React/Vue 迁移、JS 查询选择器、埋点上报）反查优化版类对应的原始图层 id
- AI 排查"旧类名为什么不见了"时的反向索引锚点

接入点：
- `targets/html/pipeline.py::LayoutOptimizeStage.run()` —— 主路径
- `core/converter.py::_apply_layout_optimization()` —— 兼容路径

写盘时机：在 `strip_and_collect` 写 `layer_map.json` 的同一段逻辑里；如果 `_class_alias_map` 为空字典则不写。

## Postprocess：strip_dev_metadata（dev 元数据剥离）

位置：`targets/html/postprocess/strip_dev_metadata.py`

```python
def strip_and_collect(html: str) -> tuple[str, dict]: ...
def write_layer_map(layer_map: dict, path: Path) -> None: ...
```

**目的**：`index_optimized.html` 是开发者最终交付物，但生成阶段为了 LayoutOptimizer
内部判断（`dom_restructure` / `flex_applier` 等读 `data-name` / `data-type`）必须保留
`data-name="形状 17"` / `data-type="image"` / `id="layer-5"` 三类属性，对开发者只是噪音
（中文 PSD 原名、内部全局自增 layer_id、CSS 已能区分 type）。

**策略**：在 emit 阶段（LayoutOptimizer 跑完之后、写盘之前）一次性剥离这三类属性，
同时把映射关系落到旁路文件 `layer_map.json`，给 AI 排查时反查 PSD 原名 / layer_id。

| 范围 | 处理 |
| ---- | ---- |
| `index.html`（未优化版） | **不剥**，完整保留所有 metadata，给 AI 做"优化版与原版视觉不一致"诊断锚点 |
| `index_optimized.html`   | 剥 `data-name` / `data-type` / `id="layer-N"`（仅 `layer-N` 格式，自定义 id 如 `id="group-18"` 保留） |
| `layer_map.json`         | 同时存 `by_class`（首类名 → meta）和 `by_layer_id`（layer-N → meta），双向反查 |

**接入点**：
- `targets/html/pipeline.py::LayoutOptimizeStage.run()` —— 主路径
- `core/converter.py::_apply_layout_optimization()` —— 兼容路径

两处都在 `html_opt.replace('href="style.css"', ...)` 之后、`html_opt_path.write_text` 之前
调用 `strip_and_collect` + `write_layer_map`，确保产物三件套（HTML / CSS / JSON）原子产出。

**反查**：grep 优化版 HTML 拿到类名（如 `shape-2__5`）→ 查 `layer_map.json["by_class"]["shape-2__5"]`
得 `{layer_id: "layer-5", name: "形状 17", type: "image"}`，再回 `index.html` 用 `layer-5`
定位完整原始上下文。

## Emit

`targets/html/emit/__init__.py` 目前是占位，将来会抽象"写盘"逻辑。
目前 HTMLGenerator 与 LayoutOptimizeStage 直接写盘，**不要** 从 emit 外部重复写。
