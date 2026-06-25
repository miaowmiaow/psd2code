# 模块：`core/extract/`

> **本文解决什么**：讲清 `LayerExporter` + Handler 决策链如何把 PSD 图层树
> 变成 legacy dict 树并把图片写盘。
> **不讨论什么**：像素渲染细节（在 `core/render/`）。

## 位置

```
core/extract/
├── __init__.py
├── layer_exporter.py   # ★ LayerExporter（编排 + 私有渲染方法）
├── image_ops.py        # 底层：裁剪 / 蒙版 / numpy alpha 合成
├── compose_cluster.py  # ★ PSD 原生合成簇检测（R1-R5 规则 + decide_group_merge）
└── handlers.py         # ★ Chain of Responsibility：5 个决策 Handler
```

---

## `LayerExporter`

位置：`core/extract/layer_exporter.py`

```python
class LayerExporter:
    def __init__(self, psd, output_dir):
        self.psd = psd
        self.output_dir = output_dir
        self.images_dir = output_dir / 'images'
        self.canvas_width / canvas_height
        self.exported_count = 0
        self.skipped_count = 0
        self._z_counter = 0
        self._image_hash_map: dict[md5, rel_path] = {}  # 去重
        self._dedup_count = 0

    # 公开 API
    def export_layers(self, container, parent_name='', depth=0,
                      parent_left=0, parent_top=0) -> list[dict]
    def verify_export(self) -> None   # 打印核对

    # 内部 API（被 handlers 调用）
    def _export_single_layer(layer, name, full_name, depth, ...) -> dict | None
    def _merge_group_as_single_image(group, name, full_name, ...) -> dict | None
    def _merge_cluster_layers_as_image(layers, name, full_name, ...) -> dict | None
    def _merge_group_as_image(group, ...)                  # 渲染为 PIL 后合并
    def _save_image_dedup(img, name, depth) -> str         # 返回 rel path
    # ... 其他 _helper
```

**几条关键事实：**

1. `export_layers` 会被 Handler 递归回调（组内继续调用 `exporter.export_layers(child)`）。
2. 内部维护全局 `_z_counter` 和计数器；Handler **无状态**，副作用都回到 exporter。
3. `_save_image_dedup` 用 md5 去重：同一张图在同一次运行中只写一次。
4. 不同于 p5 之前，`_export_single_layer` 已把"是否扩展渲染"判断委托给
   效果 Facade 的返回 bbox；不要再在这里"拆分渲染分支"
   （见 [`../05-conventions/known-pitfalls.md`](../05-conventions/known-pitfalls.md)）。

## Chain of Responsibility：`handlers.py`

```python
class LayerHandler(ABC):
    def can_handle(self, ctx: HandlerContext) -> bool: ...
    def handle(self, ctx: HandlerContext) -> HandlerResult: ...

@dataclass
class HandlerContext:
    exporter: LayerExporter
    item: Any                # 单图层 或 (base_layer, [clipped...]) 元组
    depth: int
    parent_name: str
    parent_left: int
    parent_top: int
    parent_clip_bbox: Optional[tuple[int,int,int,int]]
    bg_layer_ids: set[int] = field(default_factory=set)

@dataclass
class HandlerResult:
    produced: list[dict]     # 追加到 export_layers 结果
    handled: bool            # True 则终止链
```

### 默认链（顺序重要）

```python
DEFAULT_HANDLERS = [
    BackgroundSkipHandler(),   # 已合并过的背景 → 跳过
    ClippingGroupHandler(),    # 剪切蒙版组 (base_layer, [clipped...])
    InvisibleLayerHandler(),   # 隐藏 / opacity=0 → 跳过
    GroupHandler(),            # 普通组：尝试合并，否则递归
    LeafLayerHandler(),        # 叶图层（图片 / 文本 / shape）
]

def run_handlers(ctx, handlers=DEFAULT_HANDLERS) -> list[dict]:
    for h in handlers:
        if h.can_handle(ctx):
            r = h.handle(ctx)
            if r.handled:
                return r.produced
    return []
```

### 各 Handler 的职责

| Handler | 判定 | 动作 |
| ------- | ---- | ---- |
| `BackgroundSkipHandler` | item 已登记在 `bg_layer_ids` | 静默跳过（不 produce） |
| `ClippingGroupHandler`  | `isinstance(item, tuple)` | 合并 base+clipped；无法合并则递归 |
| `InvisibleLayerHandler` | `not visible or opacity==0` | 计入 skipped |
| `GroupHandler` | `layer.is_group()` | 调 `compose_cluster.decide_group_merge()` 取 4 个 action 分支处理（详见 [§ `decide_group_merge` 4 个 action](#decide_group_merge-4-个-action)） |
| `LeafLayerHandler` | 叶图层 | 文本→TextNode；旋转文本→Image；其他→Image |

### 扩展：新增一条决策

见 [`../04-extending/add-a-layer-handler.md`](../04-extending/add-a-layer-handler.md)

## `image_ops.py`

底层纯函数，无状态：

```python
_constrain_bbox_to_canvas(bbox, canvas_w, canvas_h) -> bbox
_apply_layer_mask(img, layer) -> Image
_alpha_composite_numpy(base_arr, overlay_arr, ...) -> arr
```

复用场景：组合成、剪切蒙版合成等。

## `compose_cluster.py`：PSD 原生合成簇决策

> **核心定位**：替代历史上的"三道闸门"（`_is_button_group / _can_merge_group / _can_merge_group_non_text`）—— 不再用启发式（按钮关键词、子节点数量、文本数量等）猜测"该不该合图"，而是基于 PSD 自身**硬性合成语义**给出决策。

### R1–R5 规则

`detect_compose_clusters(group_layer)` 把组的直接子序列（按 PSD 自底向上）切成若干 `ComposeCluster`，每个 cluster 表示"必须一起 composite 才不偏离 PSD 视觉"的最小单位。划簇规则：

| 规则 | 触发条件 | 含义 |
| ---- | -------- | ---- |
| **R1** 剪贴蒙版 | `clipping == 1` | 剪贴层只能在 base 的 alpha/bbox 范围内显示 → 与下方最近的 non-clipping base 同簇 |
| **R2** 非 NORMAL 混合 | blend ∉ {NORMAL, DISSOLVE, PASS_THROUGH} | OVERLAY / MULTIPLY / SCREEN / LINEAR_DODGE 等通过公式修改下层像素 → 必须与下方一起合成。**非 clipping 的 R2 还会"回吸"此前所有 cluster**（其合成对象是 PT 组下方全部已合像素） |
| **R3** PASS_THROUGH 子组 + 上下文依赖 | PT 组内含调整层 / 非 NORMAL blend / 跨组剪贴 | PT 组不形成独立合成层，内部依赖会穿透组边界 → 与上下邻居同簇。判定细化为 C1–C5 五类触发条件，见 `_group_contains_context_dependent` |
| **R4** 调整层 | adjustment kind | 曲线/色阶/曝光等修改下方所有像素 → 与下方一起合成（仅当下方 cluster 已被 R1/R2/R3 锁定时才合并；否则单独成簇） |
| **R5** 浏览器不可还原（推论） | ≥1 个 cluster 含 ≥2 元素 | 浏览器 alpha 堆叠只能还原 NORMAL → 凡是被 R1-R4 粘连成 ≥2 元素的 cluster 都必须合成单张 PNG |

### `decide_group_merge(group_layer)` 4 个 action

`GroupHandler.handle` 直接消费这 4 个 action：

| action | 触发 | LayerExporter 调用 |
| ------ | ---- | ------------------ |
| `merge_full` | 单 cluster 全非文本；或单 cluster + 文本 但触发"clipping over text base" 升级 | `_merge_group_as_single_image(layer)` |
| `merge_with_text_kept` | 单 cluster + ≥1 文本 + ≥1 非文本视觉成员 | `_merge_group_as_single_image(layer)`（非文本合成为背景）+ 文本独立 export |
| `merge_partial` | ≥2 cluster 且存在 ≥2 元素 glued cluster | **每个 glued cluster** 调一次 `_merge_cluster_layers_as_image(members)`；singleton independent cluster 保留独立递归。⚠️ 多个 glued cluster 之间是 NORMAL 堆叠，**绝不可合到同一张图** |
| `no_merge` | 唯一 cluster 全文本；唯一单元素 cluster；全单元素 cluster；空组 | 完全递归 `export_layers(child)` |

`merge_partial` 路径的内部不变式（关键，详见 `handlers.py` 注释）：

- cluster bg 与 indep sibling 必须**按 PSD sibling 顺序交错** export（不能集中前置），避免下方 indep sibling 的 z 反盖到 cluster bg 之上
- cluster bg z-index 必须严格小于"位于 anchor_high 位置及之后的 indep sibling 的 z"，且严格大于 anchor_low 之前的 indep sibling 的 z

## 解析阶段：纯解析、不做装饰性合图

`LayerExporter` 是**纯解析版**：1 PSD 图层 = 1 `layer_info`（叶图层）或 1 `group_info`（组），解析阶段**不**做任何启发式装饰合图。

历史上的旧三道闸门 `_can_merge_group / _can_merge_group_non_text / _is_button_group`（按钮关键词命中 / 子节点数量上限 / 全非文本判定）以及 `_merge_background_layers / _detect_background_layers` 等"画布底部连续背景合并"逻辑已**全部移除**：

- 启发式合图（多 PNG 折叠 / 装饰组拼图） → 下沉到 LayoutOptimizer（详见 [§ 智能合图开关](#智能合图开关cli--api)）
- 真正不可还原的合图（PSD 原生剪贴蒙版 / 非 NORMAL 混合 / 调整层 / PT 组上下文依赖） → 上移到 `compose_cluster.py` 用硬性规则 R1-R5 判定

唯一仍在解析阶段就触发的合图：`compose_cluster` R1-R5 给出的 `merge_full / merge_with_text_kept / merge_partial`，由 `_merge_group_as_single_image` 或 `_merge_cluster_layers_as_image` 落盘为 PNG。这些都对应**浏览器无法用 alpha 堆叠还原的 PSD 视觉**，与"按钮看着像图所以合一张"等启发式有本质区别。

## 智能合图开关（CLI / API）

合图（多 PNG → 1 PNG、多 div 折叠为 1 div）的优化全部由 LayoutOptimizer 接管，对应 2 个 CLI 开关、2 个 ctx key：

| CLI 开关 | ctx key | 默认 | 影响范围（仅 LayoutOptimizer 链路） |
| -------- | ------- | ---- | ----------------------------------- |
| `--no-smart-merge` | `smart_merge=False` | `True`（默认开） | 关闭「多 url 背景内联合成」：`DOMRestructure._try_inline_compose_backgrounds`（容器内多张装饰背景合成为单图写到自身 `background-image`，**不**删除 DOM 子节点）+ `background_flatten.flatten_multi_url_backgrounds` 文本兜底 |
| `--enable-image-layer-flatten` | `image_layer_flatten_enabled=True` | `False`（**默认关**） | 启用 Step 1.2 `ImageLayerFlatten`：把容器内 N 个 image 子合成单张 PNG 并**删除所有子 DOM**。默认关闭原因：会把语义独立的栅格化元素和真正装饰像素一起合并，可维护性损失不可接受（详见 [`../03-topics/layout-optimizer.md`](../03-topics/layout-optimizer.md) Step 1.2） |

**两个开关完全解耦**——`--no-smart-merge` 只影响多 url 背景合成（不删 DOM），`--enable-image-layer-flatten` 只控制 Step 1.2（会删 DOM）。

CLI 入口 `psd_to_code.py` 把对应值写入 `PipelineContext`：

- `args.no_smart_merge` → `ctx.set("smart_merge", False)`
- `args.enable_image_layer_flatten` → `ctx.set("image_layer_flatten_enabled", True)`

`LayoutOptimizeStage` 读取两个 ctx key，相应地构造 `images_dir`（`None` 时禁用主路径多 url 合成）和 `FlattenConfig(enabled=...)`，并在 `smart_merge=False` 时跳过 `flatten_multi_url_backgrounds` 兜底。

两个参数都不会传给 `LayerExporter`——解析阶段始终是纯解析。

## 与 IR 的连接

- `LayerExporter` 产出 **legacy dict 树**。
- `core/psd/parser.py::parse_psd_to_ir` 将其包装进 `Document.root.meta['legacy_roots']`。
- 之后由 `core/ir/adapters.to_legacy_layers` 原样取出给 codegen。

## 性能优化

本模块包含第1-2周的性能优化实现：

| 优化项 | 位置 | 效果 |
|-------|------|------|
| **光效渲染缓存** | `_unified_light_cache` | 消除重复 composite (-20-30%) |
| **效果渲染缓存** | `_render_layer_with_effects_cached()` | 避免多路径重复 (-10-15%) |
| **属性预计算** | `_precompute_layer_properties()` | 加速属性访问 (-5-8%) |
| **图片并行化** | `ThreadPoolExecutor` 异步 IO | 非阻塞编码+写入 (-5-10%) |
| **图片去重** | `_save_image_dedup()` + MD5 | 复用重复图片 |

详见 [Performance-Optimization.md](./Performance-Optimization.md)。
