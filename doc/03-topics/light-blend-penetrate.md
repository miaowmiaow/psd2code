# 光效层穿透渲染（Light Blend Penetrate）

## 问题描述

在 PASS_THROUGH 模式的组中，存在使用"光照类" blend mode 的光效层（如 `COLOR_DODGE`、`LINEAR_DODGE`、`SCREEN`、`LIGHTEN`、`LIGHTER_COLOR`）。这些模式下，黑色像素是恒等色（与底色混合后等价于透明）。但 `topil()` 导出时黑色会成为实际的黑色像素块。

当光效层在 PASS_THROUGH 组内需要穿透到外组下层进行混合时，单独导出该光效层会得到错误的视觉结果。需要识别这种情况，在被覆盖的外层图层导出时，附带对应的光效层一起合成渲染。

---

## 总体流程（4 个阶段）

```
Phase 1: 遍历 PSD → 识别光效层
Phase 2: 组内向下查找 → 判定是否需要穿透
Phase 3: 穿透到外组 → 匹配外层下方图层
Phase 4: 导出时 → 叠加光效层渲染
```

---

## Phase 1: 识别光效层

### 时机

在 `export_layers()` 递归处理之前，先做一遍预扫描（pre-scan）。

### 光照类 blend mode 集合

```python
#: "光照类" blend mode 集合——这些模式下黑色像素是恒等色（与底色混合后
#: 相当于透明/不贡献），topil() 导出时黑色会"暴露"成假的黑色区块。
#: 只有命中此集合 + 图层原始像素含显著黑色区域，才值得走穿透渲染。
_LIGHT_BLEND_MODES: frozenset[str] = frozenset({
    'COLOR_DODGE',
    'LINEAR_DODGE',   # PS add → CSS screen（黑色=恒等）
    'SCREEN',         # 黑色=恒等
    'LIGHTEN',        # 黑色=恒等（取 max）
    'LIGHTER_COLOR',  # 黑色=恒等（取亮色）
})
```

### 逻辑

```
遍历 PSD 所有图层（递归）:
    if 图层所在的直接父组是 PASS_THROUGH 模式:
        if 图层 blend_mode ∈ _LIGHT_BLEND_MODES:
            if 图层为可见（visible=True, opacity > 0）:
                → 标记为"候选光效层"
```

### 数据结构

```python
@dataclass
class LightEffectLayerInfo:
    layer: Any                    # 光效图层对象
    bbox: tuple[int,int,int,int]  # 光效层 bbox (left, top, right, bottom)
    parent_pt_group: Any          # 所在的 PASS_THROUGH 父组
    needs_penetrate: bool = False # Phase 2 判定结果
```

---

## Phase 2: 组内向下查找 → 判定是否需穿透

### 目的

判断光效层在其 PASS_THROUGH 父组内，其有效作用区域是否被下方图层**充分覆盖**。若覆盖不足，则需要穿透到更外层取底色。

### 前置：有效作用区域识别

光效层的**原始 bbox** 不一定等于其实际发光区域。以下机制会限制实际作用范围：

| 限制机制 | 效果 | 获取方式 |
|---------|------|---------|
| Layer Mask | 实际区域 = layer_bbox ∩ mask_bbox | `layer.mask.bbox` |
| Clipping Layer | 实际区域受 base layer 不透明区域限制 | 向下找第一个非 clip 层的 bbox |
| Vector Mask | 矢量路径裁剪 | `layer.vector_mask.bbox` |

计算公式：

```
effective_bbox = layer_bbox
                 ∩ mask_bbox          (如果有 layer mask)
                 ∩ vector_mask_bbox   (如果有 vector mask)
```

> **注意**：PASS_THROUGH 组不裁剪子层（穿透特性），所以不需要与父组 bbox 取交集。

### 快速路径

- **Clipping layer → 直接判定不穿透**：clipping layer 的可见性完全由其 base layer（下方第一个非 clip 层）的 alpha 决定，base layer 天然就是它的"底色"，不存在无底色的情况。

### 逻辑

```
对每个候选光效层 L（位于 PT 组 G 中）:

    # 快速路径: clipping layer 不需要穿透
    if L.clipping == True:
        L.needs_penetrate = False
        continue

    # 计算有效作用区域
    effective_bbox = L.bbox
    if L.mask 存在:
        effective_bbox = effective_bbox ∩ L.mask.bbox
    if L.vector_mask 存在:
        effective_bbox = effective_bbox ∩ L.vector_mask.bbox

    if effective_bbox 为空:
        L.needs_penetrate = False  # 无可见区域
        continue

    effective_area = effective_bbox 的面积

    # 计算组内覆盖率
    siblings = list(G)  # PSD 自底向上顺序
    L_idx = siblings.index(L)

    total_covered_area = 0
    for i in range(L_idx - 1, -1, -1):
        sib = siblings[i]
        if not sib.visible or sib.opacity == 0:
            continue
        if sib.clipping == True:
            continue  # 跳过 clipping layer（它不作为独立底色）

        inter = intersect_bbox(effective_bbox, sib.bbox)
        if inter 为空:
            continue
        total_covered_area += inter 的面积

    coverage = total_covered_area / effective_area

    # 覆盖率判定（阈值 90%）
    if coverage >= 0.90:
        L.needs_penetrate = False  # 充分覆盖 → 不需穿透
    else:
        L.needs_penetrate = True   # 覆盖不足 → 需要穿透
```

### 关键细节

- **覆盖率阈值 90%**：允许小面积边缘未覆盖（抗锯齿、1px 偏移等），覆盖 ≥ 90% 视为充分
- **有效区域而非原始 bbox**：layer mask 可能将实际发光区域缩小 60%~90%（实测案例），用原始 bbox 会导致覆盖率被严重低估
- **跳过 clipping sibling**：组内的 clipping layer 依附于其 base layer，不能作为独立底色参与覆盖计算
- 如果组内下方图层本身也是 PASS_THROUGH 子组，需要递归检查其 composite 结果

---

## Phase 3: 穿透到外组 → 匹配外层下方图层

### 目的

找到光效层穿透出去后，实际会与哪些外层图层混合。

### 逻辑

```
对每个 needs_penetrate = True 的光效层 L（位于 PT 组 G 中）:
    # 向上追溯到 G 在其父级中的位置
    outer_parent = G 的父级
    G_idx = outer_parent 子列表中 G 的位置

    # 在 G 之下（idx 更小）的 outer_parent 子图层中查找
    matched_targets: list[Any] = []

    for i in range(G_idx - 1, -1, -1):
        sib = outer_parent[i]  # G 的同级（外层图层/组）
        if not sib.visible or sib.opacity == 0:
            continue

        # 计算 sib 与 L 的 bbox 交集
        inter = intersect_bbox(L.bbox, sib.bbox)
        if inter 为空:
            continue

        # 过滤：排除不相关图层
        if sib.is_group():
            # 组的情况：检查 composite 后交集区域是否有不透明内容
            sib_img = sib.composite()
            if 交集区域全透明:
                continue  # 跳过空组
        else:
            # 叶图层：检查 topil() 后交集区域是否有不透明内容
            sib_img = sib.topil()
            if sib_img is None:
                continue
            if 交集区域全透明:
                continue  # 跳过透明图层

        # 通过过滤 → 记录为目标图层
        matched_targets.append(sib)

    # 如果 outer_parent 本身也是 PASS_THROUGH 组，继续向上穿透
    # （递归，直到遇到 NORMAL 组或根节点）

    # 记录映射关系
    for target in matched_targets:
        penetrate_map[id(target)].append(L)
```

### 过滤条件汇总（排除不相关图层）

1. 不可见 (`visible=False`) 或 `opacity==0` → 跳过
2. bbox 与光效层无交集 → 跳过
3. 交集区域内全透明 → 跳过（空图层/透明区域）
4. 调整层（adjustment）→ 跳过（无像素内容）

### 数据结构

```python
# 穿透映射：target 图层 id → 需要叠加的光效层列表
penetrate_map: dict[int, list[LightEffectLayerInfo]] = {}
```

---

## Phase 4: 导出时叠加光效层

### 时机

Phase 4 有**两条路径**，确保无论目标层是叶图层还是组，都能正确叠加光效：

1. **叶图层路径**：在 `_export_layer_image()` 中，步骤 2.5（mask 后）与步骤 3（裁剪）之间
2. **组目标路径**：在 `_merge_group_as_single_image()` 中，`composite()` 完成后、保存 PNG 之前

> **设计动机**：Phase 3 映射到的 target 可能是叶图层（直接 `topil()`），也可能是
> 组（走 `_merge_group_as_single_image` → `group.composite()`）。如果只在
> `_export_layer_image` 中检查 `penetrate_map`，组目标永远匹配不到，导致光效层
> 无法合成到 `头部`、`BG` 等组合成的 PNG 上。

### 逻辑

```python
# ── 路径 1: 叶图层（_export_layer_image 中）──
def _export_layer_image(self, layer, ...):
    # 正常渲染 layer 得到 base_img, base_bbox
    ...
    # Phase 4: 检查 penetrate_map
    light_layers = self._penetrate_map.get(id(layer), [])
    if light_layers:
        base_img, base_bbox = self._apply_penetrate_light_layers(
            base_img, base_bbox, light_layers, ...)
    # 继续正常的保存流程
    ...

# ── 路径 2: 组目标（_merge_group_as_single_image 中）──
def _merge_group_as_single_image(self, group_layer, ...):
    composite_img = group_layer.composite(viewport=grp_bbox)
    ...
    # Phase 4 补充: 组目标也需叠加光效层
    group_light_layers = self._penetrate_map.get(id(group_layer), [])
    if group_light_layers:
        composite_img, actual_bbox = self._apply_penetrate_light_layers(
            composite_img, actual_bbox, group_light_layers, ...)
    # 继续保存
    ...
```

### 光效混合算法（`_blend_light_layer`）

| Blend Mode | 公式 |
|---|---|
| SCREEN | `1 - (1-bg) * (1-fg)` |
| COLOR_DODGE | `bg / (1 - fg)` (clamp) |
| LINEAR_DODGE | `bg + fg` (clamp) |
| LIGHTEN | `max(bg, fg)` |
| LIGHTER_COLOR | 取整体亮度更高的颜色 |

#### 核心语义：光效"附着于底层"

PSD 中光效 blend mode 的正确语义是**附着于底层内容**：
- 光效层只改变底层**已有像素**的颜色/亮度
- 底层透明的区域，光效层**不产生新像素**
- **输出 alpha = 底层 alpha**（光效层不增加覆盖面积）

```python
# 错误做法（标准 Porter-Duff OVER）——会产生黑底：
out_a = fg_a + bg_a * (1 - fg_a)  # ← 光效层 alpha 扩展到底层透明区域
# 当 bg_a=0, fg_a>0, fg_rgb=0 时 → 输出黑色不透明像素！

# 正确做法——光效层只在有底的区域起作用：
has_bg = (bg_a > threshold)
mix_factor = fg_a * has_bg  # 底层透明时 mix_factor=0
out_rgb = bg_rgb * (1 - mix_factor) + blended * mix_factor
out_a = bg_a  # 不增加覆盖面积
```

#### 有效作用域一致性

Phase 2 在判断"是否需要穿透"时检查了**有效覆盖率**（只看有底色的区域），
Phase 4 在实际合成时也必须遵守相同语义——只在底层有内容的区域起作用。
否则光效层的黑色恒等色区域（对 COLOR_DODGE，black = identity）在底层透明处
会"填充"出黑色不透明像素，产生黑底。

#### bbox 不扩展

`_apply_penetrate_light_layers` 只在 base bbox 与光效层 bbox 的**交集**区域
内做 blend，不扩展 base 的 bbox。因为光效层不增加覆盖面积，超出 base 范围的
光效层区域（底层透明）不会产生任何可见像素。

---

## 集成点（在现有架构中的切入位置）

| 切入位置 | 文件 | 说明 |
|---|---|---|
| 预扫描入口 | `layer_exporter.py` → `_pre_scan_light_layers()` | 在 `export_layers()` 前执行 Phase 1-3，并构建 Phase 5 抑制集合 |
| 穿透映射存储 | `LayerExporter` 实例属性 | `self._penetrate_map: dict[int, list[...]]` |
| 抑制集合存储 | `LayerExporter` 实例属性 | `self._suppressed_light_layers: set[int]` |
| 叶图层叠加 | `layer_exporter.py` → `_export_layer_image()` | Phase 4 路径 1：步骤 2.5（mask 后）与步骤 3（裁剪）之间叠加光效层 |
| 组目标叠加 | `layer_exporter.py` → `_merge_group_as_single_image()` | Phase 4 路径 2：`composite()` 完成后叠加光效层（目标是组时） |
| 光效层抑制（叶） | `layer_exporter.py` → `_export_single_layer()` | Phase 5：跳过独立导出 |
| 光效层抑制（簇） | `layer_exporter.py` → `_merge_cluster_layers_as_image()` | Phase 5 补充：从 cluster 合成中排除光效层（隐藏后不参与 `composite()`） |

---

## Phase 5: 光效层自身导出抑制

### 问题

Phase 4 已在目标层 PNG 上做了正确的像素级合成。但光效层自身有两条路径会导致黑底残留：

1. **独立叶导出路径**（原 Phase 5 已修复）：光效层被 `LeafLayerHandler` 正常导出为独立 PNG + CSS `mix-blend-mode`。CSS blend 无法穿透 DOM 容器边界（stacking context），黑色恒等色像素暴露为真实黑底。

2. **组内 cluster 合并路径**（补充修复）：光效层因 compose cluster 的 R2 规则（非 NORMAL blend → 与下方同簇）被粘连到 glued cluster 中，通过 `_merge_cluster_layers_as_image` → `group.composite()` 合成。PSD 的 `composite()` 会忠实渲染 COLOR_DODGE/SCREEN 等 blend，但合成结果仍然是"光效层 + 下方 sibling"的视觉——**光效层的黑色恒等色像素会参与合成**，烧入最终 PNG。

### 解决方案

**Phase 5a（叶导出抑制）**：

在 `_export_single_layer()` 开头，检查 `id(layer) in _suppressed_light_layers`，命中则跳过。

**Phase 5b（cluster 排除）**：

在 `_merge_cluster_layers_as_image()` 中，将 `_suppressed_light_layers` 中的光效层**临时隐藏**（`visible = False`），使其不参与 `group.composite()` 合成。

```
# Phase 5b: cluster 合成时排除被抑制的光效层
for m in cluster_members:
    if id(m) in suppressed_light_layers:
        m.visible = False  # 临时隐藏
        # group.composite() 将不包含该光效层
# 合成完成后恢复 visible
```

### 逻辑

```
# Phase 5: 构建抑制集合（_pre_scan_light_layers 末尾）
for target_lights in penetrate_map.values():
    for li in target_lights:
        suppressed_light_layers.add(id(li.layer))

# Phase 5a: 叶图层导出时检查（_export_single_layer 开头）
if id(layer) in suppressed_light_layers:
    skip  # 光效已通过 Phase 4 合成到目标层，不独立导出

# Phase 5b: cluster 合成时排除（_merge_cluster_layers_as_image）
suppressed_in_cluster = [m for m in cluster_members if id(m) in suppressed_light_layers]
# 临时隐藏 → composite() 不包含 → 恢复
```

---

## 调用时序

```
LayerExporter.__init__(psd, output_dir)
    │
    ▼
_pre_scan_light_layers(psd)
    ├── Phase 1: 遍历识别候选光效层
    ├── Phase 2: 组内检查 → 标记 needs_penetrate
    ├── Phase 3: 向外匹配 → 构建 penetrate_map（目标可能是叶图层或组）
    └── Phase 5: 构建 suppressed_light_layers 集合
    │
    ▼
export_layers(psd, ...)
    ├── _group_clipping_layers()
    ├── run_handlers()
    │     ├── ClippingGroupHandler
    │     ├── InvisibleLayerHandler
    │     ├── GroupHandler → decide_group_merge()
    │     │     ├── merge_full / merge_with_text_kept
    │     │     │     └── _merge_group_as_single_image()
    │     │     │           └── ★ Phase 4 路径2: 检查 penetrate_map → 组目标叠加光效
    │     │     └── merge_partial
    │     │           └── _merge_cluster_layers_as_image()
    │     │                 ├── ★ Phase 5b: 排除 suppressed 光效层（临时隐藏）
    │     │                 └── _merge_group_as_single_image()
    │     │                       └── ★ Phase 4 路径2: 组目标叠加光效
    │     └── LeafLayerHandler
    │           └── _export_single_layer()
    │                 ├── ★ Phase 5a: 检查抑制集合 → 跳过穿透光效层
    │                 └── _export_layer_image()
    │                       └── ★ Phase 4 路径1: 检查 penetrate_map → 叶目标叠加光效
    └── return result
```

---

## 边界情况与注意事项

1. **多层穿透**：如果 PT 组嵌套在另一个 PT 组中，需要递归向上查找目标图层（Phase 3 递归）
2. **光效层被抑制导出**：`needs_penetrate=True` 的光效层不再独立导出 PNG，其视觉效果完全由 Phase 4 在目标层上的像素合成提供。这避免了 CSS `mix-blend-mode` 无法穿透 DOM stacking context 的根本限制
3. **性能考虑**：Phase 2 的 alpha 检测可以只采样交集区域的子集（如 stride=4），避免对大图全量扫描
4. **组被 merge_full 的情况**：如果目标图层所在的组被 `decide_group_merge()` 判定为 `merge_full`，则不需要单独处理（整组 composite 时 psd-tools 已正确处理穿透混合）
5. **黑色区域占比检查**：Phase 1 可增加"黑色区域占比"校验（如黑色像素 > 30%），避免误标记非光效图层
6. **`id()` 生命周期**：`penetrate_map` 和 `_suppressed_light_layers` 使用 `id(layer)` 作 key，与 `partial_merged_ids` 一致，仅在本次运行中有效
7. **未穿透的光效层不受影响**：`needs_penetrate=False`（组内已充分覆盖）的光效层不在抑制集合中，仍正常导出 + CSS `mix-blend-mode`（因为组内有底色，CSS blend 能正确工作）
8. **目标层可能是组**：Phase 3 匹配的 target 可能是叶图层也可能是组。Phase 4 在两处检查 `penetrate_map`：`_export_layer_image`（叶图层路径）和 `_merge_group_as_single_image`（组路径），确保所有情况都能正确叠加
9. **光效层被 compose cluster R2 粘连**：光效层因非 NORMAL blend（如 COLOR_DODGE）会被 R2 规则粘到 glued cluster 中。Phase 5b 在 `_merge_cluster_layers_as_image` 中将其临时隐藏，使 `composite()` 不包含光效层的黑色恒等色像素。典型案例：`吧台` 组的 `레이어 202`（COLOR_DODGE）被粘到 5 层 cluster，不排除则 `batai-*.png` 含黑底
10. **光效合成语义：附着于底层（不扩展覆盖范围）**：Phase 2 在判断穿透时有"有效作用域"和"有效覆盖率"的概念，Phase 4 实际合成时也必须遵守相同语义。`_blend_light_layer` 中输出 alpha = 底层 alpha，不使用标准 Porter-Duff OVER（否则光效层的黑色恒等色区域会在底层透明处填充出黑色不透明像素）。`_apply_penetrate_light_layers` 只在 base 与光效层的 bbox 交集内做 blend，不扩展 base bbox。典型案例：`头部` 组只有 750x473 的内容，光效层 `레이어 202` bbox 为 1045x678——如果扩展则导出 1045x678 的大图含大量黑底

