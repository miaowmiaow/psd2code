# 主题：布局优化器

> **本文解决什么**：讲清 LayoutOptimizer 当前 12 步流水线的整体形态、各步骤的算法核心，以及最容易踩坑的几条硬约束（v-stack wrapper position 保护、reclassify N=2 双强信号、CSS 全局 header 保留、flex-shrink:0 硬约束）。
> **不讨论什么**：`bs4` API 细节、`common/css_utils.py` 内部正则。

---

## 它做什么

从"绝对定位的图层集合"→"语义化、Flex 化、可读、可复用的 HTML/CSS"：

- **DOM 重构**：按空间包含关系 + 行/列聚类调整父子结构，输出 v-row / v-col / v-stack 三类容器。
- **图层扁平化（统一通道，2026-05-27 起默认关闭）**：把"容器自身 bg + 全部 image 子"的 PNG 合成单图写回容器，删除子 div + CSS。代替历史上四个分散的 merge 分支。⚠️ **默认关闭**（语义独立元素混在装饰里时会被误合并），通过 CLI `--enable-image-layer-flatten` 或 `FlattenConfig(enabled=True)` 显式启用。
- **同质兄弟分组**：识别"平铺的同质卡片"（设计师没用父组包起来），包成 `v-list`，让下游可写 `v-for`。
- **Flex 推断**：对剩余非 v-row/v-col 容器做 flex-col / flex-row 推断；趋势元素 → margin，非趋势元素保留 absolute；所有 flex 子元素写入 `flex-shrink: 0`。
- **单子 wrapper 折叠**：把"内部只有 1 个子节点"的虚拟 v-row/v-col wrapper 整体替换为子节点（margin 数值合并），消除布局算法副产物。
- **CSS 去冗余**：精简全局 `z-index`，合并属性等价的多个选择器为 `.a, .b, .c { ... }`。
- **位置噪声宽容合并**：同 base + 非位置签名相同且 margin 偏差 ≤ 8px 的列表项（如 `nickname-2 / nickname-3 / ...`）归一到代表样式，用 N→1 样式复用换设计稿生产噪声容忍（**链路内唯一会引入亚像素视觉差异的步骤**）。
- **重复元素抽取**：≥ 3 个等价 hash 类（`.prop__68 / .prop__105 / ...`）合并为单一语义 base 类（`.prop`），HTML 同步改写。
- **语义类去后缀**：把剩余 `.<base>__<id>` 统一改写为 `.<base>`（同名冲突用 `-2 / -3 / ...`），并把旧 → 新映射写到 `class_alias_map.json`。
- **虚拟 wrapper 命名语义化**：`.v-stack-7` → `.<语义前缀>-stack`（从子孙/祖先语义 class 挑前缀），让 wrapper 类名不再是全局编号。
- **CSS 美化**：按 DOM 顺序排序、属性按维度分段、合并组多行展开，让开发者读改 `style_optimized.css` 不再吃力；compact / expanded 双预设。

位置：`targets/html/postprocess/layout_optimizer/`

## 输入与输出

```python
optimize_layout(
    html: str,
    css_rules: dict,
    global_header: str = "",                                # extract_global_css_header 产物
    pretty_config: CssPrettyConfig = None,
    repeat_unify_config: RepeatUnifyConfig = None,
    semantic_rename_config: SemanticRenameConfig = None,    # Step 3.7
    virtual_wrapper_rename_config: VirtualWrapperRenameConfig = None,  # Step 3.8
    position_relaxer_config: PositionRelaxerConfig = None,  # Step 3.3（唯一引入视觉差异的步骤）
    images_dir: Path | None = None,                         # 物理 images/ 目录；用于落盘合成图；smart_merge=False 时由调用方传 None
    flatten_config: FlattenConfig = None,                   # ImageLayerFlatten 配置（默认 FlattenConfig.enabled=False，需显式启用）
) -> (html_out: str, css_out: dict, stats: dict)
```

> **CLI 开关与 LayoutOptimizer 参数的对应关系**
>
> | CLI 开关 | ctx key | 默认 | 作用 |
> | -------- | ------- | ---- | ---- |
> | `--no-smart-merge` | `smart_merge=False` | `True`（默认开启） | 关闭「多 url 背景内联合成」：`LayoutOptimizeStage` 把 `images_dir=None` 传给 `DOMRestructure`，并跳过 `flatten_multi_url_backgrounds` 文本兜底 |
> | `--enable-image-layer-flatten` | `image_layer_flatten_enabled=True` | `False`（**默认关闭**） | 启用 Step 1.2 `ImageLayerFlatten`：`LayoutOptimizeStage` 用 `FlattenConfig(enabled=True)` 构造 transformer。默认关闭原因见 Step 1.2 章节 |
>
> 两个开关**完全解耦**：`--no-smart-merge` 只影响多 url 背景合成（不删 DOM 子节点），`--enable-image-layer-flatten` 只控制 Step 1.2 的 ImageLayerFlatten（会删 DOM 子节点）。
>
> **解析阶段 (`LayerExporter`) 是纯解析版**——1 PSD 图层 = 1 layer_info / 1 PNG，本身就不做任何"装饰性合图"，所以两个开关都对解析阶段没有影响。

`stats` 关键字段：

```python
stats = {
    # DOM / 布局
    'backgrounds_merged': int,
    'classes_merged': int,
    'flex_applied': int,
    'positions_removed': int,
    'dom_restructured': int,
    'sibling_lists_created': int,    # v-list 数
    'sibling_items_wrapped': int,    # 被 v-list 包住的节点数
    'wrappers_collapsed': int,       # 单子 wrapper 折叠数

    # CSS
    'z_index_pruned': int,           # 删除的 z-index 字段数
    'css_rules_merged': int,         # 节省的 CSS 规则条数
    '_css_merge_groups': list[list[str]],  # 等价规则组（透传给 dict_to_css）

    # Step 3.3 PositionNoiseRelaxer（仅在启用时）
    'position_relaxed_groups': int,   # 被归一的组数
    'position_relaxed_classes': int,  # 被归一的类总数

    # Step 3.5 RepeatClassUnifier
    'classes_unified': int,          # 删除的 hash 类数
    'elements_unified': int,         # 改写的 HTML 元素数
    'repeat_groups_unified': int,    # 成功合并的组数

    # Step 3.7 SemanticClassRename
    'semantic_class_renamed': int,   # 被去后缀改写的类数
    '_class_alias_map': dict,        # 旧 __N 类名 → 新精简类名，写盘为 class_alias_map.json

    # Step 3.8 VirtualWrapperRename
    'virtual_wrapper_renamed': int,  # .v-stack-7 → .<prefix>-stack 改写数

    # 图层扁平化 / 背景合成
    'image_layer_containers_flattened': int,  # ImageLayerFlatten 处理的容器数
    'image_layer_layers_collapsed': int,      # ImageLayerFlatten 合并的图层总数
    'image_layer_bytes_saved': int,           # 节省字节数（基于原 PNG 大小）
    'bg_inline_flatten': dict,       # DOMRestructure 内联合成的 (rules_flattened, layers_collapsed, bytes_saved)

    # CssPretty
    '_pretty_css': str,              # CssPretty 渲染好的最终字符串（写盘优先用它）
}
```

## 流程

```
LayoutOptimizer.optimize():
  Step 1    DOMRestructure.restructure_dom()
            背景剥离 + 行/列/stack 切分 + 容器背景吸收 pass + Stack→Col 升级
            + 高瘦跨行装饰剥离 + 多层背景内联合成（透传 images_dir 时）
  Step 1.2  ImageLayerFlatten.run()
            "容器自身 bg + 全部 image 子"按 z 序合成单 PNG 写回容器，
            删除子 div + CSS（后序 + 多轮扫描 5 次，链式简化）
  Step 1.5  SiblingGroupDetector.run()
            扫描所有容器的直接子，识别同质兄弟序列 → 包成 v-list（flex-wrap）
  Step 2    FlexApplier.apply_flex_layouts()
            跳过 v-row/v-col/v-stack/v-list，对剩余容器做 flex 推断；
            所有 flex 子元素写入 flex-shrink: 0（硬约束）
  Step 2.5  WrapperCollapse.run()
            单子 v-row/v-col wrapper → 替换为子节点（margin 数值合并）
  Step 3    CssDedup.run()
            Pass 1：z-index 精简（DOM 序天然吻合 z 序的容器删掉所有 z-index）
            Pass 2：等价规则合并（属性 dict 完全相同 → 选择器分组）
  Step 3.3  PositionNoiseRelaxer.run()                ★ 唯一引入视觉差异的步骤
            同 base（剥 __N）+ 非位置签名相同的多个类，当 margin 偏差 ≤ 8px 时
            取众数 margin 归一 + 清零 z-index，让 N 个"只差亚像素位置"的选择器
            进入 CssDedup 合并组。牺牲 ≤ 8px 视觉精度换样式复用。
  Step 3.5  RepeatClassUnifier.run()
            ≥ 3 个等价 hash 类（.prop__68 / .prop__105 / ...）→ 单一 base 类（.prop）
            （已合并的组从 _css_merge_groups 移除，CssPretty 不再渲染）
  Step 3.7  SemanticClassRename.run()
            剩余 .<base>__<id> 一律改名为 .<base>（同名冲突用 -2 / -3 / ...）
            产出 _class_alias_map，调用方写盘为 class_alias_map.json
  Step 3.8  VirtualWrapperRename.run()
            .v-stack-7 / .v-row-3 → .<语义前缀>-stack / -row / -col
            前缀优先从子孙语义 class 挑，fallback 到祖先
  Step 4    CssPretty.render()
            Reset / @media / #canvas / 图层四段 + DOM 序排序 + 属性分段 + 合并组多行
            （compact / expanded 双预设；CLI --css-style 切换）
```

> **历史遗留（已移除）**
> - 早期 V3 的 `Step 1.5 _fix_overflow_after_restructure`（图片容器/圆角容器/按钮三规则补 overflow）已迁移到源头 `targets/html/codegen/renderers/group_renderer.py`：在生成 HTML 时直接判断"组的子是否溢出 bbox"，溢出则不写 `overflow:hidden`，否则默认写。布局优化器不再做 overflow 修复。
> - 历史上四个独立 merge 分支（`_try_merge_siblings` / `_try_collapse_into_parent` / `_try_absorb_into_relative_parent` / `_try_merge_parent_bg_with_single_child`）已在 2026-04-30 重构为统一的 `ImageLayerFlatten`，新增图片合并场景只需改 `_can_flatten_container` / `_PARENT_BLOCKING_PROPS` 一处。
> - 2026-04-30 曾在 `LayoutAnalyzer` 引入 V11/V12 二维网格识别（`_detect_grid_layout` + FlexApplier `_apply_grid_layout`），后因触发场景误识别问题较多已回滚；代码中仅保留 `grid-row-N` / `v-grid-row` 兼容类名，不会再主动产出新的 grid 容器。

## Step 1：DOM 重构

位置：`transformers/dom_restructure.py`

核心算法（按一次 `restructure_dom()` 执行顺序）：

### 1.1 背景剥离（`_extract_background_leaves`）

把"完全包含型 / 主轴覆盖型 / 双轴主导覆盖型"（≥ 80%×80%）的子图层作为背景层吸收进父 group 的 `background-image` 多 url 列表。

**z 序约定**：`background-image: url(a), url(b)` 第一个 url 在视觉**最上层**。candidates 先按 leaf.element 在原 DOM 父中的 sibling index 排序（=z 升序），再 reversed，保证与 PSD 视觉一致（多张近全屏背景层叠 + 颜色对调诊断必看）。

### 1.2 高瘦跨行装饰剥离（方案 A，2026-04-29）

在 `_split_by_rows` 之前剥出"高瘦 + 纵向跨过多行 + 跨过的行本身在 X 上对齐"的 leaf。否则 row envelope 会被高瘦元素拉高，所有下方贴边小元素按 ratio = 1.0 被吸到同行，形成"虚胖"行 → 错切 v-row。

**4 条 AND**（全部命中才剥离，单测在 dom_restructure 顶部 ClusterConfig docstring 列得很细）：

| # | 判据 | 阈值字段 | 默认 |
| - | ---- | -------- | ---- |
| 1 | 候选 height ≥ 其余 leaves 中位高度 × N | `tall_decor_height_ratio` | 2.0 |
| 2 | 候选 height/width ≥ N（接近正方或更瘦高，防宽横条） | `tall_decor_aspect_min` | 0.8 |
| 3 | 候选在 Y 轴"显著覆盖" ≥ N 个其他 leaves（每个的纵向重叠/自身高 ≥ 0.5） | `tall_decor_min_crossed_rows` | 2 |
| 4 | 被跨过的 leaves 之间 X 投影区间显著重叠（任意两两 X 重叠率 ≥ 1−tol × min(width)） | `tall_decor_x_align_tolerance` | 0.2 |

**典型场景**：领奖.psd 文案组 wenan__93 内 5 条说明文本 + 1 个 icon-refresh__92（73×84，跨过 2 条文本）。剥离后产物结构：`S[icon-refresh, C[文案 ×5]]`，icon 与 5 条文本各自定位正确。

**条件 4 的设计陷阱**（采坑记录）：第一版用"被跨过的 leaves 两两 left/right 都对齐"，对"左对齐 + 右端不齐"的多行文本（btn-exchange r=450 vs checkout r=343）|450-343|=107px 远超 tol，cond4 失败。改成"X 投影区间显著重叠"才正确。

临时关闭：`enable_tall_decor_extraction = False`。

### 1.3 切行/列聚类（`_cluster` + `_split_by_rows`）

- **同行判定**（V3）：与当前行 envelope 的纵向重叠率 / min(elem.h, row.h) ≥ `row_dominant_overlap_ratio`（默认 0.5），而非旧 V2 的"top < current_bottom"。
- **多行回退 stack**（`_is_fake_multirow_stack`）：切完多行后，若每行单元素 + 相邻行横向覆盖 ≥ `multi_row_stack_fallback_x_ratio`（默认 0.8），且行数 < `fake_multirow_max_rows`（默认 4），回退为 stack。**4 行以上一律视为真列表**（典型场景：领奖.psd 文案 5 条说明剥 icon 后形成 5 行 col）。
- **flex 子元素 margin 计算**（`_apply_flex_child_margins`）：当 envelope.left/top 为负（PSD 装饰元素越界）时用 `max(0, envelope.left/top)` 算 origin，与 `_apply_flex_to_existing_container` 写的 padding 严格一致，避免整组偏移。

### 1.4 容器背景吸收 pass（restructure 完成后）

`_absorb_container_backgrounds_pass()` 扫描所有真实 group + 虚拟 v-stack/v-row/v-col wrapper 的直接子 image，把"近全覆盖（≥ `container_bg_cover_ratio` 默认 0.95） + opacity≈1 + normal blend + 不溢出（≤ `container_bg_overflow_tolerance_px` 默认 2px）"的 image leaf 吸收为容器 `background-image`，覆盖 `_extract_background_leaves` 触达不到的场景。被吸收过的容器会打 `data-bg-absorbed='1'` 标记，供下一步 reclassify 识别。

### 1.5 Stack→Col 反向升级 pass（背景吸收之后）

`_reclassify_stacks_after_bg_absorption()` 重新评估那些"被吸收过背景"的 v-stack 容器。

**升级条件（全部满足才升级为 v-col）**：
1. 容器是 v-stack 且打了 `data-bg-absorbed`
2. 剩余直接子 ≥ `reclassify_min_rows`（默认 2）
3. `_split_by_rows` 切出的行数 == 子元素总数（每行单元素，纯单列）
4. **横向覆盖率**：N=2 时 ≥ `reclassify_n2_min_x_overlap`（默认 0.95，更严）；N≥3 时 ≥ `reclassify_x_overlap_ratio`（默认 0.8）
5. **gap 校验**：N=2 时单 gap ≤ `reclassify_n2_max_gap_px`（默认 50px）；N≥3 时相邻行 gap 的变异系数 ≤ `reclassify_gap_cv_max`（默认 0.4）

**关键**：**不**调用 `_is_fake_multirow_stack`——它会把"完美单列多行列表"100% 误拦。N=2 场景靠"横向覆盖 0.95 + gap ≤ 50px"双强信号防止真叠图对（如 badge + 底图）被误升级。

**触发原因**：dom_restructure 早期 `_cluster_row` 遇到"大底框 + N 个并列卡"这种"前景与底框 100% 纵向重叠 + X 同列"的组合时会 fallback 判 stack；等容器背景吸收 pass 把底框吸走后，本质上剩下的是真列布局，应该升级。

## Step 1.2：图层扁平化（ImageLayerFlatten，2026-04-30 重构；2026-05-27 起默认关闭）

> **⚠️ 2026-05-27 起 `FlattenConfig.enabled` 默认值改为 `False`**。本步骤会把容器内 N 个 image 子合成为单张 PNG 并删除全部子 DOM，对"含语义元素混在装饰中"的组误伤严重——典型反例：抽奖活动「游泳圈」组里"游泳圈底图(pixel) + 数字框矩形(shape) + 礼盒文字(被栅格化的 TypeLayer)"3 个语义独立元素全部满足触发条件（都是 `data-type=image` + 单 PNG + 邻接图连通 + opacity=1 + 正常 blend），被合并成单张 `flat-*.png` 后丧失独立改色 / 换文案 / 绑事件能力。
>
> 判定逻辑虽然检查了 `data-type=image / 单 PNG / 邻接连通` 等几何条件，**但无法识别"栅格化产物背后的语义角色"**。在加入"按子 PSD kind 混合检测 / 语义类名族系 / 子数量上限"等更严格护栏之前，默认关闭比较安全。
>
> 启用方法：CLI `--enable-image-layer-flatten` / 显式 `FlattenConfig(enabled=True)`。下面的章节描述的是**启用后**的行为。

位置：`transformers/image_layer_flatten.py`

### 历史背景

"合并图片以减少 DOM/CSS/PNG 请求"在历史上分散在 4 个独立函数里：

| 旧函数 | 场景 |
| ------ | ---- |
| `_try_merge_siblings` | 多个 image 兄弟合并为一张 |
| `_try_collapse_into_parent` | 子图合并后上提到 absolute 父，删父 div |
| `_try_absorb_into_relative_parent` | 子图吸收为 relative 父的 background，保留父 div |
| `_try_merge_parent_bg_with_single_child` | 父 bg + 单子叠加合并 |

每个分支各自有触发条件、白名单、护栏，新增一种场景就要改多处，维护噩梦。

### 新设计：单一递归函数

对每个候选容器，把"容器自身的 background-image（如有）+ 全部直接 image 子的 background-image"视为一个图层栈，**按 z 序合成单张 PNG，写回容器自己的 background-image，删除所有子 div + 子 CSS 规则**。

**关键差异：容器一律保留**，不消除 DOM 层级。从而：

- 不破坏外层布局（容器在父 flex/grid 中的 width/height/margin 不变）
- 不破坏虚拟 wrapper 的语义（v-stack/v-row/v-col 仍然作为 flex 子项）
- 任意"父背景 + 单子叠加"场景天然支持
- 任意"无背景父 + N 子叠加"场景天然支持
- **新增规则只需改一处**：`_can_flatten_container` / `_PARENT_BLOCKING_PROPS`

### 触发条件（全部 AND）

容器侧：

1. 容器是 `layer-group` 或 `data-virtual ∈ {stack, row, col, grid-row}`
2. 容器没有"无法烧进 PNG 的装饰字段"（`_PARENT_BLOCKING_PROPS`）
3. 容器自身 background-image：缺失 OR 单一本地 PNG（参与合成）

子侧（每个直接子 div 都要满足）：

- `data-type == "image"` 且无内部 div（叶子）
- `position:absolute` + 完整 `left/top/width/height` (px)
- `background-image` 是单一本地 PNG
- `opacity ≈ 1.0`、`mix-blend-mode` 缺省/normal

层数 / 几何护栏：

- 总层数（容器自身 bg + 子层数）≥ `min_total_layers`（默认 2）
- envelope 面积 ≤ canvas × `max_area_ratio`（默认 0.5）
- 子之间 L∞ 距离 ≤ `max_neighbor_gap_px`（默认 10px）邻接图必须连通

### `_PARENT_BLOCKING_PROPS`

```python
_PARENT_BLOCKING_PROPS = {
    'border-radius',
    'border', 'border-top', 'border-bottom', 'border-left', 'border-right',
    'box-shadow',
    'clip-path',
    'filter', 'backdrop-filter',
    'transform',
    'mask', 'mask-image',
}
# overflow 仅在值为 hidden / clip / scroll / auto 时阻断
```

理由：这些字段必须保留在最终 div 上才能正确渲染；如果合并子 PNG 后再叠加：

- `border-radius / overflow:hidden / clip-path`：会裁掉子的 PNG 范围
- `box-shadow`：阴影位置依赖容器 bbox
- `filter / transform`：会改变最终视觉，烧进 PNG 后再叠 filter 等于双重作用

后续要放宽某条规则，只需把它从这里移除（并在 `_replace_container_with_merged` 中把字段保留下来）。

### 后序遍历 + 多轮扫描

`run()` 用**后序遍历**先尝试扁平化最深的容器，再向上尝试。这样深层合并产物（变成单 div + 容器自身有 bg）会被外层再次发现，实现"子图合并 → 父再吸收为背景"的链式简化。

多轮扫描（最多 5 轮）直到稳定（一次内层合并可能让外层从"含子组"变成"叶子容器"）。

### 接入位置

`LayoutOptimizer.optimize()` 步骤 1.2，在 `dom_restructure.restructure_dom()` 之后、`sibling_group_detector.run()` 之前。这样 DOM 重构产物已稳定，sibling_group_detector / flex_applier 都看到合并后的单 div。

### 配置

```python
@dataclass
class FlattenConfig:
    enabled: bool = False         # ⚠️ 2026-05-27 起默认关闭，需显式启用
    min_total_layers: int = 2
    max_area_ratio: float = 0.5
    max_neighbor_gap_px: int = 10
    max_canvas_px: int = 8192
```

### 排查提示

- ImageLayerFlatten 整体没生效（产物 `index_optimized.html` 找不到 `flat-*.png` 引用 + 子 div 仍独立）→ 2026-05-27 起默认关闭，需要时显式 `--enable-image-layer-flatten` 或 `FlattenConfig(enabled=True)`
- 启用后期望某容器被扁平化但未触发 → 看 `_can_flatten_container` 哪条返回 False：常见 `mix-blend-mode != normal`、`opacity < 1`、子 div 内部还有子节点
- 期望容器被合并但有 `border-radius` → 它在 `_PARENT_BLOCKING_PROPS` 里，按设计跳过；要支持需把字段留到 PNG 之上的 div
- 临时关闭整个 transformer：`FlattenConfig(enabled=False)`（也是当前默认）

## Step 1.5：同质兄弟分组（SiblingGroupDetector，V11）

位置：`transformers/sibling_group_detector.py`

### 问题背景

PSD 设计稿里"商品卡 / 道具卡 / 礼包卡"经常被设计师摆成 N 个同名同结构图层，**没有用一个父组包起来**。LayoutOptimizer 之前的 DOM 重构只在已有 group 内部聚类，这种"平铺在 #canvas 直接子"的列表会全部走 absolute 路径输出，开发拿到的 HTML/CSS 完全看不出"它是一个数据列表"，没法直接写 `v-for`。

### 判定规则（5 条 AND）

1. 至少 `min_count` 个连续兄弟（默认 3）
2. **class 词根**相同：去掉 `__\d+` 后缀和 `-\d+` 序号后比较
   - `prop__30` / `prop-2__38` / `prop-10__101` 词根都是 `prop`
   - 这是**最强的设计师意图信号**（设计师把同类卡命名规范化）
3. **bbox 尺寸近似**：width / height 误差 ≤ `size_tolerance`（默认 5%）
4. **网格规则**：能排成 `M 列 × K 行`（含单行/单列），同列 left 一致且同行 top 一致（误差 ≤ 2px），且 cols × rows == n（满格）
5. **父非 flex 容器**：若父 class 含 `v-row` / `v-col`，跳过——父容器已经在 flex 化它们了，再 wrap 反而多此一举

**不做子结构同构判定的原因**：实际 PSD 中同类卡内部结构几乎总是有差异（首张卡设计完后复制改文案/图片，结构变化包括少了一行文字、按钮换成图片、装饰图层数量不同等）。如果强求子结构完全一致，会**绝大多数现实场景识别失败**。class 词根 + bbox 尺寸两条已足够强。

### 输出

被识别的 N 个兄弟被包成一个虚拟 div：

```html
<div class="prop-list v-list" data-virtual="list" style="...">
  <div class="prop__30 layer-group" ...>...</div>
  <div class="prop-2__38 layer-group" ...>...</div>
  ...
</div>
```

CSS：`display: flex; flex-wrap: wrap; column-gap: ...; row-gap: ...`。被包的子节点改成 `position: static`，去掉 left/top；其它属性原样保留。下游开发可直接：

```vue
<div class="prop-list" v-for="item in items">...</div>
```

## Step 2：Flex 推断（FlexApplier）

位置：`transformers/flex_applier.py`

核心逻辑：
1. **跳过已被处理的容器**：class 含 `v-row` / `v-col` / `v-stack` / `v-list` 任一即跳过（DOM 重构 / SiblingGroupDetector 已经处理过）。
2. 对剩余容器调 `LayoutAnalyzer.analyze_children_layout`：
   - V10 装饰剥离：先把子节点分为 bg / decor / content 三类，**只在 content 子集上**做趋势检测和 V8/V9 闸门
   - bg / decor 子节点的 `is_trend` 永远 False，输出到 `decor_classes` 字段
3. 若 `layout_type='vertical'/'horizontal'` → `_apply_vertical_layout` / `_apply_horizontal_layout`
4. 若 `layout_type='none'` 但有 absolute 子元素 → `_handle_non_flex_container` 给父容器补 `position:relative`

### Step 2.0：V8 / V9 / V10 安全闸门（layout_analyzer）

| 闸门 | 触发条件 | 适用场景 | 阈值 |
| ---- | -------- | -------- | ---- |
| **V8** `_is_stacked_cluster` | trend_ratio < 0.6 且"显著重叠对数 ≥ 子元素数 n" | 互相重叠的装饰图层组 | 重叠率 > 30% 算"显著重叠对" |
| **V9** `_has_dominant_background_overlay` | 存在子元素 X 满足 `X.area / envelope.area ≥ 0.8` 且 ≥ 60% 其他子元素显著落在 X 内（无 trend_ratio 门槛） | 1 个大背景层 + 多个小元素的"卡片"（V8 漏拦的场景） | `bg_area_ratio=0.8`, `overlap_ratio=0.6`, `min_other_ratio=0.6`；candidate **必须是 image**（V10 修正） |
| **V10 装饰剥离** | 子节点分 bg / decor / content；trend / V8 / V9 都只看 content 子集 | 内容容器自带大背景图层 + 角落装饰，被误判为堆叠卡片 | bg：image + opacity ≥ 0.95 + 任一覆盖判据；decor：image + opacity < 0.95 + area ≤ 30% + 不压在内容上（与非 image 子重叠 < 30%） |

V10 还放开"content 子集只有 2 个 → 至少 1 次变化即可 flex"（V7 老规则要求 ≥ 2 次变化）。

**排查提示**：Flex 优化后某容器子元素飘到顶部/底部 → 看该容器是否：
1. 有大底框图层（≥ 80% 容器面积）= V9 场景，应保持 absolute（V9 拦下）
2. 子元素 bbox 大量互相重叠（无明显主背景）= V8 场景
3. 内容 + 装饰混排 = V10 场景，装饰应剥到 `decor_classes`

### Step 2.1：趋势元素 → margin（flex 原生布局）

趋势元素全部转成 **flex 流布局**，删除 `position/top/left`，用 margin 表达间距：

**垂直（`_apply_vertical_layout`）：**

| 位置 | 策略 |
| ---- | ---- |
| 第一个趋势元素 | `margin-top = 原 top`（保留首元素相对容器顶部的初始偏移）|
| 后续趋势元素 | `margin-top = 本元素 top − 前一个元素 bottom`（间距）|
| 全部趋势元素 | `margin-left = 原 left`（若 > 0，处理列内水平缩进）|

**横向（`_apply_horizontal_layout`）：**

| 位置 | 策略 |
| ---- | ---- |
| 第一个趋势元素 | `margin-left = 原 left` |
| 后续趋势元素 | `margin-left = 本元素 left − 前一个元素 right`（间距）|
| 全部趋势元素 | `margin-top = 原 top`（> 0 时，作为垂直对齐偏移）|

**幂等保护**：若 `margin-top` 已存在（来自 DOM 重构），**保留不覆盖**，避免二次计算。

**硬约束 `flex-shrink: 0`**（2026-05-01，两处）：`_apply_vertical_layout` 和 `_apply_horizontal_layout` 对每个 trend 子元素在写 margin 之后强制写入 `flex-shrink: 0`。PSD 所有子图层都是 absolute、尺寸独立不互相影响；转 flex 后若子总高/宽超过父 bbox（PSD group bbox 本来就不保证装得下所有子），浏览器默认 `flex-shrink: 1` 会按比例压缩子元素，导致视觉走样（如南瓜图 115px 被压成 111.59px）。`flex-shrink: 0` 让子元素保持 CSS 声明尺寸，不足时自然溢出，与 PSD absolute 语义对齐。**新增任何"产出 flex 子项"的 transformer 都必须同步写入 flex-shrink:0**（`dom_restructure._apply_flex_child_margins` 也实装一份）。

### Step 2.2：v-stack wrapper 的 position 必须保留 ★

**关键陷阱**：当 v-stack 容器被父 group 的 flex 化"吸"成趋势子元素后，`del child_css['position']` 会把 stack 容器升级时打的 `position:relative` 删掉，导致内部 absolute 子节点跳到外层 #canvas 定位（典型现象：文本飘到屏幕左边缘）。

**修复**（2026-04-29）：删除 position 之前判断 `is_stack_wrapper = 'v-stack' in child_info['classes']`，是的话改为 `child_css['position'] = 'relative'` 而不是删除。同样保护适用于 `_apply_horizontal_layout`。

依赖：`analyzers/layout_analyzer.py::analyze_children_layout` 在 `children_info` 字典里写出 `classes: list(child.get('class', []))` 字段，让 flex_applier 能读到 wrapper 标记。

### Step 2.3：非趋势元素 → 保留 `top/left` 的 absolute

**问题**：直接把所有子元素都塞进 flex 流会让"溢出装饰层 / 角标 / 叠图"错位。
**解决**：对非趋势元素**保留其原有 `top/left`**，只补齐 `position: absolute` 与父容器的 `position: relative`。

> ⚠ 反模式：`new_top = original_top − container_top`（减去父容器自身坐标）。
> **不要**这样做。`original_top` 是 extract 阶段算好的**相对父容器坐标**，二次相减会让所有非趋势元素整体偏负一个 `container_top`，表现为整个区域大面积留白 + 子元素堆到顶部之外。

### Step 2.4：非 flex 容器的兜底（`_handle_non_flex_container`）

即使 `layout_type='none'`，只要容器里存在 absolute 子元素，就给父容器追加 `position: relative`，保证它们有正确的定位上下文。**不做任何坐标换算**。

## Step 2.5：单子 wrapper 折叠（WrapperCollapse）

位置：`transformers/wrapper_collapse.py`

### 背景

DOM 重构 + Flex 推断的副产物：当一个 v-row / v-col 内部经过 `pure_image_group_flatten` 或 `image_layer_flatten` 合成后只剩 1 个子节点，wrapper 本身已经退化为"只起占位作用的多余 div"，应当折叠掉。

```
<!-- 折叠前 -->
<div class="v-col-12 v-col">              <!-- 单子 wrapper，无意义 -->
  <div class="prop-merged" style="..."></div>
</div>

<!-- 折叠后 -->
<div class="prop-merged" style="..."></div>
```

### 折叠规则

| 项 | 策略 |
| -- | ---- |
| 触发条件 | wrapper 是 `data-virtual ∈ {row, col}` 且只含 1 个直接子 div |
| margin 合并 | wrapper 与子节点的 `margin-top/right/bottom/left` 数值相加，写到子节点 |
| z-index | 子节点已有则保留，否则继承 wrapper 的 z-index |
| DOM 操作 | `wrapper.replace_with(child)`，删除 wrapper 的 `css_rules` 条目 |
| 多轮扫描 | 最多 5 轮直到稳定（一次折叠后可能暴露新的"单子 wrapper"链） |

### 故意不折叠的类型 ★

| 类型 | 原因 |
| ---- | ---- |
| `v-stack` | 是 absolute 子元素的 **containing block**，折叠后子元素会跳到外层，导致定位错乱（known-pitfall #4 的反向场景） |
| `v-list` | SiblingGroupDetector 标记的"同质重复列表"，是数据驱动渲染的语义锚点（v-for），不能消除 |
| 根 `layer-group` | 顶层版块容器，是 LayoutOptimizer 契约的一部分（known-pitfall #7） |

代码层面通过 `COLLAPSIBLE_VIRTUAL_KINDS = {'row', 'col'}` 与 `SKIP_MARKER_CLASSES = {'v-stack', 'v-list'}` 双白/黑名单实现。

### 排查提示

- 优化后某个 v-stack 内的 absolute 子元素飘到屏幕角落 → 检查 `SKIP_MARKER_CLASSES` 是否被改动，v-stack 是否被误折叠
- wrapper 折叠后子元素位置整体偏移 → 检查 margin 合并逻辑，wrapper 与子的 margin 单位是否都是 px
- 期望折叠但产物里依然有 v-row / v-col 单子 wrapper → 该 wrapper 可能含 padding / background 等"非中性属性"，按设计不允许折叠（防止视觉副作用）

## Step 3：CSS 去冗余（CssDedup）

位置：`transformers/css_dedup.py`

### Pass 1 — z-index 精简（`_prune_z_index`）

按 BeautifulSoup 遍历每个父容器，收集子元素的 (selector, z) 序列，把"已知 z (z!=None)"的子序列单独拎出来：

| 序列形态 | 动作 |
| -------- | ---- |
| 长度 0 | 跳过 |
| 长度 1（独 z + 全 None 兄弟）| 删该 z-index |
| 长度 ≥ 2 严格递增 | 全删 |
| 长度 ≥ 2 出现倒挂 | 全部保留兜底 |

逻辑：`position:absolute` 元素的视觉叠序只在"父容器内出现 bbox 重叠的兄弟"时才依赖 z-index；绝大多数父容器下子元素遵循"DOM 源代码顺序 = z 序升序"（这是 LayerRenderer / HTMLGenerator 的天然产出顺序），浏览器默认行为已能正确实现叠序，z-index 完全是噪声。

### Pass 2 — 等价规则合并（`_merge_equivalent_rules`）

扫描 `css_rules`：把"属性 dict 完全相等"的多个选择器登记到同一个签名组。`common/css_utils.dict_to_css(rules, merge_groups=...)` 输出时，同组选择器写成 `.a, .b, .c { ... }` 单条规则。CSS 选择器分组在 W3C 标准里完全等价于多条独立规则，不会引入任何视觉差异。

注意：仅对 `parse_css_to_dict` 已识别的"单 .class / #id"规则去重；全局 header（`* { ... }`、`body { ... }`、`@media`）由 `extract_global_css_header` 原样保留，不参与合并。

### 数值规范化（`common/css_utils._normalize_css_value`）

把 CSS 值里数字字面量做精度归一：`22.000px → 22px`、`opacity: 1.0 → 1`、`1.500em → 1.5em`。

**关键 bug 修复（2026-04-29）**：旧 `_NUMBER_RE = r'-?\d+\.\d+|-?\d+'` 会误吃标识符里的数字段，把 `url("images/bg-f07984.png")` 改成 `url("images/bg-f7984.png")`（前导 0 被当数字归一），同样把 `images/btn-0b0682.png` 改成 `images/btn0b682.png`（`-0` 被当 "-0" 字面量吃掉）。

**修复方案**：
- 新增 `_URL_RE`，先把 `url(...)` 用占位符抠出来再做数字替换，最后还原
- 新增带边界的 `_NUMBER_RE`：前置 lookbehind 排除 `[A-Za-z0-9_\-.]`，后置 lookahead 排除 `[A-Za-z0-9_]`，单位组覆盖标准 CSS 单位列表（px/em/rem/%/vh/vw/vmin/vmax/deg/rad/turn/grad/s/ms/ch/ex/pt/pc/cm/mm/in/fr）
- `targets/html/codegen/renderers/text_renderer.py` 在源头修一份：`_text_style_css` 写 `font-size` / `line-height` 时调用本地 `_fmt_num()`（`style.css` 走 ctx.css_rules 字符串拼接路径，不经过 `dict_to_css`）

**南瓜大作战实测效果**：
- z-index 精简：304 处
- CSS 等价规则合并：节省 209 条
- style_optimized.css 行数 ~3450（前 5400）
- CSS 块数：457 → ~270
- 残余 z-index 字段数：~432 → 97

## Step 3.3：位置噪声宽容合并（PositionNoiseRelaxer）★ 唯一引入视觉差异的步骤

位置：`transformers/position_noise_relaxer.py`

### 背景

设计师在 PSD 里手摆 N 个同类列表项（昵称条 / 积分块 / 卡片）时，几乎不可能做到像素级对齐，相邻项 `top/margin-top` 经常有 1~5px 的亚像素抖动。这些抖动让 `.nickname__2 / .nickname__5 / .nickname__8 / ...` 的 CSS 属性 dict **不完全相等**（仅 `margin-top` 差 3px），绕过了 CssDedup 的严格等价判断，各自独占一条规则 + 一个 class，后续 RepeatClassUnifier 也无法把它们识别成同一类。

PositionNoiseRelaxer 用**样式复用换亚像素精度容忍**：同 base（剥 `__N`）+ 非位置签名完全相同 + margin 偏差 ≤ 8px 的选择器组，把所有成员的 margin 统一为**众数**，同时把 z-index 清零。这样它们进入下游 CssDedup 的等价规则合并组，最终在 HTML 里只留一个 unified class。

### 触发条件（全部 AND）

1. 组大小 ≥ `min_group_size`（默认 3）
2. 所有成员选择器形如 `.<base>__<digits>`（剥 `__N` 后 base 相同）
3. 组内所有成员的**非位置 CSS 属性**（去掉 `top/left/margin-*/z-index`）完全相等
4. 组内成员 margin 各方向最大值 − 最小值 ≤ `max_margin_delta_px`（默认 8px）

### 归一动作

| 步骤 | 操作 |
| ---- | ---- |
| margin | 每个方向（top/right/bottom/left）取众数，平票时取最小值 |
| z-index | 统一清零或移除（避免 CssDedup 因 z-index 差异拒绝合并）|
| stats | `position_relaxed_groups +=1`、`position_relaxed_classes += N` |

归一后 N 个成员的 CSS dict 完全相等，CssDedup Pass 2 把它们合并成一个签名组，RepeatClassUnifier 再把它们改写为单一 `.<base>` 类。

### ⚠️ 唯一会引入视觉差异的步骤

这是整个 LayoutOptimizer 链路里**唯一**不保持像素级一致的 transformer：最坏情况下某个成员的 margin 会偏移 ≤ `max_margin_delta_px` 像素。其他所有步骤（DOMRestructure / ImageLayerFlatten / FlexApplier / CssDedup / CssPretty 等）都保证像素级等价。

**需 100% 像素一致时**：`PositionRelaxerConfig(enabled=False)`。

### 排查提示

- 某组期望归一但 `_css_merge_groups` 里仍然分散 → 检查 margin 最大差是否超过 `max_margin_delta_px`；或非位置属性里有某字段不相等（典型：`opacity` 0.99 vs 1）
- 归一后视觉对齐偏移明显（> 5px 可见）→ 调小 `max_margin_delta_px`，或单独给该场景关闭（目前只能全局开关）
- 想看被归一的组：`stats['position_relaxed_groups']`、`stats['position_relaxed_classes']`；CLI 打印 `- 位置噪声归一: N 组 (覆盖 M 个类)`

## Step 3.5：重复元素抽取（RepeatClassUnifier）

位置：`transformers/repeat_class_unifier.py`

### 背景

CssDedup 已经把"属性完全相同的多个选择器"识别成 `_css_merge_groups`，CssPretty 也会渲染成 `.a, .b, .c { ... }` 合并块；**但 HTML 中依然写了 N 个不同的 hash 类**（`.prop__68 / .prop__105 / .prop__142 / ...`），带来：

1. **可读性差**：工程师看 HTML 时无法立刻意识到"这是同一类卡片，重复 5 次"，只能比对 hash 后缀
2. **复用代价高**：想给"所有 prop 卡片"加交互/动画/状态修饰符（`active` / `done`），要同步改 N 个 class，或退化到 `[class^="prop__"]` 属性选择器

figma-to-frontend 的产物里这种 5×4 网格只有一个 `.sec-grid-item` 类被复用 20 次，可读性、可维护性、可状态扩展都远胜 hash 类方案。

### 触发条件（同时满足）

1. **来源**：从 `stats['_css_merge_groups']`（CssDedup 产出）取候选；
2. **组大小** ≥ `min_unify_count`（默认 3）——避免对偶发 2 个相似类强行抽象；
3. **命名形态**：所有成员选择器形如 `.<base>__<digits>`（SimpleNamer 产出，含 sibling_index + id 后缀），用 `_NAMED_RE = ^\.([A-Za-z][A-Za-z0-9-]*?)__(\d+)$` 匹配；
4. **公共 base 段非空且唯一**：组内所有成员剥掉 `__\d+` 后前缀相同（如都是 `prop` 或都是 `btn-receive`）。

### 改写动作

| 步骤 | 操作 |
| ---- | ---- |
| HTML | 每个成员 class（`prop__68`）从元素 class 列表中移除，替换为统一 unified class（`prop`）|
| css_rules | 删除原 N 个选择器条目，新增单一 `.<unified>` 条目，属性 dict 取首个成员（CssDedup 已保证组内属性逐字相等）|
| `_css_merge_groups` | 把这一组从合并组里移除（已被合并，无需 CssPretty 再渲染合并块）|
| stats | 累加 `classes_unified` / `elements_unified` / `repeat_groups_unified` 计数 |
| 可选注解 | `annotate_index=True` 时给元素加 `data-repeat-index="N"`（位序，1 起），方便后续 `:nth-child` / JS data-* 选择 |

### 命名规则（unified class）

- 取组内成员"剥掉 `__\d+` 后的前缀"作为 base；
- base 唯一 → 直接用 base；
- base 不唯一（极少见，组成员混用 `rounded` / `rounded-2`）→ 跳过，不合并；
- 命名空间冲突（base 已存在为 `css_rules` 中的某个具体选择器）→ 追加 `-grp` 后缀（`prop` → `prop-grp`）。

### 故意不合并的类型 ★

| 类型 | 原因 |
| ---- | ---- |
| `v-stack-N` / `v-row-N` / `v-col-N` 等自动派生类 | 由 `_DERIVED_RE = ^\.(?:v-stack\|v-row\|v-col)-\d+$` 拦截。**序号本身就是它们的"复用维度"**——dom_restructure / flex_applier 按 `v-stack-7` 选位置，替换为单一类反而破坏定位假设 |
| `layer-group` / `layer` 角色类 | layout_optimizer 契约的一部分（known-pitfalls #7），保留 |
| HTML id（`id="layer-N"`） | 由 `strip_dev_metadata` 处理，本 transformer 不碰 |

### 排查提示

- 期望某组被抽取但产物里仍有多个 hash 类 → 检查 `min_unify_count`（默认 3）是否满足；检查所有成员是否都符合 `_NAMED_RE`（自动派生类不会被合并是 by design）
- 抽取后某些元素丢失视觉差异 → CssDedup 是否真的把它们放在同一组（属性必须逐字相等，包括 z-index）
- 抽取后状态修饰符冲突 → unified class 与某个具体选择器同名时应自动加 `-grp` 后缀，检查 `_resolve_unified_class` 的命名空间冲突分支

## Step 3.7：语义类去后缀（SemanticClassRename）

位置：`transformers/semantic_class_rename.py`

### 背景

`SimpleNamer` 给每个图层产出形如 `.nickname__37` / `.btn-receive__128` 的类，`__N` 是 sibling_index + id 后缀，**防止命名冲突**用，不具备业务含义。RepeatClassUnifier 只抽取"≥ 3 个完全等价"的 hash 类；剩下那些**独一份**或"只 2 个"的 hash 类还会带着 `__N` 后缀存活。开发者读 HTML / CSS 时看到 `.nickname__37` / `.nickname__142` 仍会困惑"这两个有什么区别"。

SemanticClassRename 收尾：把剩余 `.<base>__<id>` 一律改写为 `.<base>`，同名冲突（base 已被 RepeatClassUnifier 用掉 或 多个不等价类撞 base）自动追加 `-2 / -3 / ...`。

### 触发条件 & 改写

| 项 | 说明 |
| -- | ---- |
| 来源 | HTML 里所有 class 匹配 `^<base>__<digits>$` 的元素 + css_rules 里对应选择器 |
| 排除 | 自动派生类 `v-stack-N / v-row-N / v-col-N`（由 Step 3.8 处理）、`layer-group` / `layer` 角色类 |
| 冲突解决 | 首次遇到 base → 用 base；再遇到 → `base-2`、`base-3`、... |
| HTML | 每个元素的 class 列表中的 `__N` 类替换为新名 |
| css_rules | 选择器 key 从 `.<base>__<N>` 改为 `.<base>`；`_css_merge_groups` 里的残留选择器同步更新 |
| stats | `semantic_class_renamed` 计数；`_class_alias_map` 字典旧→新（供外部写 `class_alias_map.json`）|

### 产出：class_alias_map.json

`LayoutOptimizeStage` 在写盘时读 `stats['_class_alias_map']`，把旧 hash 类 → 新精简类的映射写到 `class_alias_map.json`，供外部工具（React/Vue 迁移、JS 查询选择器、埋点打点）反查原图层 id。

### 排查提示

- 期望某类被去后缀但产物里仍是 `__N` → 检查是否命中排除规则（派生类 / 角色类）；或该类在 `_css_merge_groups` 中，被 RepeatClassUnifier 处理成了别的 unified 名
- 新名冲突（两个本该不同的类都想叫 `.nickname`）→ 按设计走 `-2 / -3` 后缀，查 `stats['_class_alias_map']` 里有无 `-2`、`-3` 后缀的新名
- 旧 → 新映射没写出 class_alias_map.json → 检查 LayoutOptimizeStage 有没有读 `stats['_class_alias_map']` 并落盘

## Step 3.8：虚拟 wrapper 命名语义化（VirtualWrapperRename）

位置：`transformers/virtual_wrapper_rename.py`

### 背景

DOM 重构和 SiblingGroupDetector 会产出大量 `.v-stack-7 / .v-row-3 / .v-col-12 / .prop-list / ...` wrapper 类，`-N` 是全局流水号，不带语义。开发者看产物很难一眼判断"这个 `.v-col-12` 在 DOM 里服务哪个业务模块"。

VirtualWrapperRename 把这些类重命名为 `.<语义前缀>-stack / -row / -col`：语义前缀从 wrapper 的**子孙**语义 class 优先挑选（典型：一个 `v-col-12` 里包着 `.nickname`、`.score`、`.btn-receive`，选最频繁或最具代表性的 `nickname` 作前缀 → `.nickname-col`）；子孙没有合适语义 class 时 fallback 到**祖先**。

### 触发 & 改写

| 项 | 说明 |
| -- | ---- |
| 来源 | 所有 class 匹配 `^(v-stack\|v-row\|v-col)-\d+$` 的元素 |
| 前缀挑选 | 优先子孙，fallback 祖先；同 base `__N` 去后缀对比 |
| 冲突解决 | 多个 wrapper 落到同一前缀 → 用 `-stack / -row / -col-2 / -3` 后缀区分 |
| HTML | class 列表里的 `v-stack-7` 替换为新名 |
| css_rules | 选择器 key 从 `.v-stack-7` 改为 `.<prefix>-stack` |
| stats | `virtual_wrapper_renamed` 计数；`_class_alias_map` 同步更新 |

### 故意不改名的类型

- `v-list`：SiblingGroupDetector 的语义锚点（v-for），不参与改名
- 根层 wrapper（没有语义子孙、也没有语义祖先）：保持 `v-stack-N` 原名

### 排查提示

- 某 wrapper 未改名 → 看子孙/祖先是否都没有合适的语义 class（如全是 hash 类、或全是 layer-group）
- 改名后出现 CSS 选择器覆盖冲突 → `_class_alias_map` 里应该有 `-2 / -3` 后缀的新名；否则检查是否跳过了 Step 3.7（它应保证 `.<base>__N` 已变 `.<base>`）
- 接下游 React/Vue target → `_class_alias_map` 同步更新，JS 里用旧名查询的地方需要用新名

## Step 4：CSS 美化（CssPretty）

位置：`transformers/css_pretty.py`

### 目的

`dict_to_css` 是字母序机械渲染（diff 友好但人不友好）。CssPretty 在保持 W3C 等价前提下重排 `style_optimized.css`，让开发者的"找/改"路径与 PSD 视觉/DOM 顺序对齐。

### 双预设：compact（默认）vs expanded

通过 `CssPrettyConfig.style` 字段切换两种预设；CLI 通过 `--css-style {compact,expanded}` 控制（默认 compact）。

| 维度 | compact（默认）| expanded |
| ---- | ----------- | -------- |
| 目标 | 接近手写 CSS 的紧凑度，与 figma-to-frontend 同量级 | 开发者全展开调试模式，可读性最高 |
| 段标题 `section_comment_style` | `single`（单行 `/* ---- xxx ---- */`）| `framed`（4 行框框）|
| 属性分段 `property_grouping` | False（按内置顺序紧凑输出）| True（定位/盒模型/排版/外观/混合，段间空行）|
| 短规则单行 `short_rule_max_props` | 6 | 2 |
| 合并组多行阈值 `multiline_threshold` | 4 | 3 |
| 坐标溯源注释 `coord_provenance` | False | True（每条规则上方加 PSD 图层 id / 名 / 类型）|
| 行数（南瓜大作战 H5 实测）| ~1499 | ~5000 |

`__post_init__` 仅对 `None` 字段填充预设值——可以"既要紧凑又要某项展开"：

```python
CssPrettyConfig(style="compact", coord_provenance=True)  # 紧凑 + 保留溯源
CssPrettyConfig(style="expanded", short_rule_max_props=4)  # 全展开但短规则更宽松
```

### 5 个 Pass（`CssPrettyConfig` 单独开关）

| Pass | 改善 | 例子 |
| ---- | ---- | ---- |
| 1 文件骨架 | 固定 4 段 + 醒目分隔注释 | `Reset → @media → #canvas → 图层规则` |
| 2 DOM 序 | 按 `index_optimized.html` 自顶向下出现顺序排列；`bankuai-*` / `section-*` 边界插版块注释 | 找版块 1 的 button 直接翻到该段 |
| 3 属性分段 | 按 `定位 / 盒模型 / 排版 / 外观 / 混合 / 其他` 分段，段间空一行；**只有 ≥ 8 个属性的块才分段**（`property_grouping_min_props=8`），仅 expanded 启用 | `position; left; top; z-index` 集中在块顶 |
| 4 合并组多行 | 成员 ≥ `multiline_threshold` 时选择器逐行 + `/* ↳ N 个等价规则合并 */` | grep / git diff / 折叠都友好 |
| 5 短规则单行 | 单选择器 + 属性 ≤ `short_rule_max_props` → `.foo { color: red; }` | 占位 / 工具类不撑文件 |

### 接入点

- `LayoutOptimizer.optimize()` step 4 在 CssDedup 之后调用 `CssPretty.render()`，结果放 `stats['_pretty_css']`
- `targets/html/pipeline.py::LayoutOptimizeStage.run()` 写盘前**优先取该字符串**，为空才降级到 `dict_to_css(...)`
- LayoutOptimizer 构造增加 `global_header` 和 `pretty_config` 参数
- CLI：
  - `--css-style compact|expanded`（默认 compact）
  - `--no-css-pretty`（彻底关闭，降级到 dict_to_css）

### 关键 bug 修复

`common/css_utils._iter_top_level_blocks` 按 `{` 切分时把"块前注释"粘到 selector 头部（如 `"/* 图层样式 */\n.bg__1"`），导致 `parse_css_to_dict` / `extract_global_css_header` 把第一条 `.class` 规则误判为非 class 块、整段塞进全局头。新增内部辅助 `_strip_leading_comments(selector)` 在判定前剥掉前置 `/* ... */` 注释。

### 回归保障

- **CSS 语义等价**：用 cssutils 解析 CssPretty 与 dict_to_css 输出，462/462 selector 完全一致、0 属性差异（南瓜大作战 H5 实测）
- **像素级一致**：Playwright 截图比对 compact / expanded / dict_to_css 三种产物，南瓜大作战 H5 都是 **0 像素差异**（5083500 像素全等）

### ⚠️ 历史踩坑：CSS 默认值不能随便删

CssDedup Pass 0a 会删除"等于 CSS 默认值"的字段（如 `opacity: 1` / `mix-blend-mode: normal`）。**`background-repeat: no-repeat` 不能删！** 因为 `background-repeat` 的 CSS 默认值是 `repeat`（不是 `no-repeat`），删除会让单张大背景图被浏览器平铺。曾把它误加进 `_BACKGROUND_NOISE_VALUES` 导致 Playwright 测出 76072/5083500 像素差异。类似要小心的"反直觉默认值"还有 `background-attachment`（默认 scroll）、`background-clip`（默认 border-box）、`background-origin`（默认 padding-box）。

### 排查提示

- 优化版样式没生效 → 看 `index_optimized.html` 的 `<link>` 是否指向 `style_optimized.css` 且 CSS 头部有 `/* 1. Reset & 全局 */` 标志
- CssPretty 失败时控制台打印 `⚠️ CSS 美化失败（降级到 dict_to_css）`，文件仍正常产出
- 某条规则在 DOM 序里"消失" → 它在 CssDedup 合并组中（在组首位置一次性输出），或被塞到末尾 `/* ---- 版块: 工具类 / 合并组（跨版块复用） ---- */` 段
- 想看每条规则属于哪个 PSD 图层 → 用 `--css-style expanded` 启用坐标溯源注释
- 优化版背景图反复平铺 → 检查 `_BACKGROUND_NOISE_VALUES` 是否又被加进 `("background-repeat", "no-repeat")`
- react/vue target 不读 CssPretty 输出（它们走自己的 css_rules dict 渲染路径），所以 CssPretty 不影响下游

## Analyzer（只读）

只有一个文件：`analyzers/layout_analyzer.py::LayoutAnalyzer`，承担：
- V10 装饰剥离（`_classify_children`：bg / decor / content 三分类）
- 行/列趋势识别（`analyze_children_layout` 主体，只在 content 子集上做）
- V8 堆叠装饰组安全闸门（`_is_stacked_cluster`）
- V9 支配背景层闸门（`_has_dominant_background_overlay`，candidate 必须是 image）
- 给 children_info 输出 `classes` 字段（让 flex_applier 识别 v-stack wrapper）

LayoutAnalyzer 不修改 soup / css_rules，可以安全复用。

> **历史遗留（V11/V12 Grid 识别已回滚）**：2026-04-30 曾引入 `_detect_grid_layout` + FlexApplier `_apply_grid_layout`（把容器识别为 `row-of-rows` 嵌套 flex），因触发场景误识别较多已回滚；代码中仅保留 `grid-row-N` / `v-grid-row` 类名兼容字段，不再主动产出新的 grid 容器。

## DOM 重构早退撤销（`_can_flex_applier_handle`）

`dom_restructure._restructure_group` 在老路径下遇到"直接子大量互相重叠"的容器会判为叠图组，直接打印 `⊙ 识别为叠图组` 早退，不让 FlexApplier 接手。但有些"大背景 + 规整列表"被剥完背景后本质是 flex 布局，早退会让这些容器永远走不到 FlexApplier。

修复：早退前调用 `_can_flex_applier_handle()` —— 临时构造一个 LayoutAnalyzer 探测子容器布局，如果识别为 `vertical` / `horizontal`，跳过早退，不动 DOM，让下游 FlexApplier 接手。日志从 `⊙ 识别为叠图组` 改为 `⏭ 叠图组判定撤销，转交 FlexApplier`。

## 零面积 bbox 过滤（`_is_stack_group`，2026-05-07）

PSD 里常见"空 group 占位"（所有子图层隐藏 / mask 完全透明）导出为 `width: 0px; height: 0px` 的 div，这种 bbox 会稀释 `_is_stack_group` 的重叠率统计（零面积 bbox 的 `overlap_ratio` 永远为 0，拖累 `stack_pairs / total_pairs` 跌破 0.5 门槛），让真正的叠图组被错判。

修复：`_is_stack_group` 入口先 `effective = [b for b in bboxes if b.area > 0]` 过滤零面积 bbox，再做两两重叠对比。其他路径（`_extract_background_leaves` / `_cluster`）天然不受影响（它们要么看单个 bbox 面积、要么看重叠关系时零面积天然被 0 过滤）。

## 失败处理

`LayoutOptimizeStage` 会把 `optimize_layout` 的异常吞掉并打印 warn，保留原始 `index.html / style.css` 不崩溃。这是**故意**设计的：优化失败不应阻断转换，用户至少有可用的原始版。

`optimize()` 内部每个 Step 也都套了 try/except，单步失败只跳过该步，后续步骤照常跑（CssPretty 失败时自动降级到 `dict_to_css`）。

## CSS 全局 header 保留（parse→dict→rebuild 的坑）

`LayoutOptimizeStage` 的 CSS 往返路径：

```
style.css (text)
  ├─ parse_css_to_dict         → {'.cls': {...}, '#canvas': {...}}
  │       ↓ optimize_layout 就地修改 dict
  └─ extract_global_css_header → 保留的前缀（* / body / @media）
                                     ↘
                                CssPretty.render(global_header=...) 
                                  或 dict_to_css(css_rules, header=...)
                                     ↓
                                style_optimized.css
```

**为什么要单独抽 header？** `parse_css_to_dict` 只提取顶层 `.xxx` / `#xxx` 规则，以下 3 类会被丢失：
- `* { margin: 0; padding: 0; box-sizing: border-box; }` — 通配符
- `body { width: 100vw; ... }` — 元素选择器
- `@media screen and (max-width: 750px) { #canvas { transform: scale(...) } }` — at-rule 嵌套。**尤其注意**：旧版正则 `([.#][\w-]+)\s*\{([^}]+)\}` 会把 `@media` 内部的 `#canvas` 误当作顶层规则，导致**外层 `#canvas` 的完整样式被内层 `transform` 单条覆盖** —— `#canvas` 失去 `position / width / height / margin / background / overflow`，整个 canvas 塌成 0 高，页面全错位。

**解决（`common/css_utils.py`）：**
- `_iter_top_level_blocks()`：按 `{}` 配对枚举**真正的顶层块**，并剥掉块前注释，不再用贪婪正则
- `parse_css_to_dict()`：只接收形如 `.xxx` / `#xxx` 的**单一类/ID 选择器**；其余（`*` / `body` / `@media ... {...}`）全部跳过
- `extract_global_css_header()`：把上面这些被跳过的块连同块间注释原样返回，作为 header 注入到优化产物开头
- `dict_to_css(rules, header='...', merge_groups=...)`：header 按原样写在最前面，后面跟 `/* ========== 图层样式 ========== */` + 字典规则

**触发场景**：任何 `LayoutOptimize` 后"整页空白 / 元素挤到顶部 / canvas 塌高"等问题，优先检查 `style_optimized.css` 头部是否保留了 `* / body / #canvas / @media` 块。

## 扩展方向

- 新增一种 DOM 重构 pass：在 `dom_restructure.py` 末尾加一个 pass（如 `_some_new_pass()`），在 `restructure_dom()` 末尾按需调用，加 ClusterConfig 开关字段
- 新增一种 flex 判定：在 `FlexApplier` 里加分支；或把 analyzer 结果加字段
- 新增一种同质兄弟模式（如"垂直堆叠 N 个 banner"）：在 `SiblingGroupDetector` 里放宽网格规则
- 新增一种 CSS 优化（如属性折叠 `margin-top + margin-left → margin: T R B L`）：在 `CssDedup` 里加 Pass 3
- 新增一种 class 改名策略：在 `SemanticClassRename` / `VirtualWrapperRename` 里加 base 命名规则
- 新增 Grid 布局：新增一个 Transformer，在 `optimizer.py::optimize()` 里按需 invoke（V11/V12 原地实现已回滚，新实现建议作为独立 transformer 加在 `FlexApplier` 之后）
