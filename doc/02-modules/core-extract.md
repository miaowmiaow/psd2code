# 模块：`core/extract/`

> **本文解决什么**：讲清 `LayerExporter` + Handler 决策链如何把 PSD 图层树
> 变成 legacy dict 树并把图片写盘。
> **不讨论什么**：像素渲染细节（在 `core/render/`）。

## 位置

```
core/extract/
├── __init__.py
├── layer_exporter.py  # ★ LayerExporter（编排 + 私有渲染方法）
├── image_ops.py        # 底层：裁剪 / 蒙版 / numpy alpha 合成
└── handlers.py         # ★ Chain of Responsibility：5 个决策 Handler
```

---

## `LayerExporter`

位置：`core/extract/layer_exporter.py`

```python
class LayerExporter:
    def __init__(self, psd, output_dir, smart_merge: bool = True):
        self.psd = psd
        self.output_dir = output_dir
        self.images_dir = output_dir / 'images'
        self.canvas_width / canvas_height
        self.exported_count = 0
        self.skipped_count = 0
        self._z_counter = 0
        self._image_hash_map: dict[md5, rel_path] = {}  # 去重
        self._dedup_count = 0
        self.smart_merge = smart_merge                   # 图层级合图总开关（见下文）

    # 公开 API
    def export_layers(self, container, parent_name='', depth=0,
                      parent_left=0, parent_top=0) -> list[dict]
    def verify_export(self) -> None   # 打印核对

    # 内部 API（被 handlers 调用）
    def _export_single_layer(layer, name, full_name, depth, ...) -> dict | None
    def _merge_group_as_single_image(group, name, full_name, ...) -> dict | None
    def _merge_group_as_image(group, ...)                  # 渲染为 PIL 后合并
    def _can_merge_group(group) -> bool
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
| `GroupHandler` | `layer.is_group()` | 先试 `_merge_group_as_single_image`；合并失败则递归 `export_layers(child)` |
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

## 背景合并链路

当画布底部有若干"覆盖整个画布且 normal 混合"的图层，`LayerExporter`
会自动把它们合并为一张背景图（`_detect_background_layers` +
`_merge_background_layers`），然后把这些原图层 id 加入 `bg_layer_ids`，
让 `BackgroundSkipHandler` 跳过后续重复导出。

## 智能合图总开关：`smart_merge`

`LayerExporter.__init__(psd, output_dir, smart_merge: bool = True)` 暴露一个
**图层级合图总开关**，等价于 CLI `--no-smart-merge` 的 PSD 端切面。`smart_merge=False` 时：

- `_can_merge_group(group)` 直接返回 `False`（装饰组不再合成单张 PNG）
- `_can_merge_group_non_text(group)` 直接返回 `False`（"非文本→背景图 + 文本独立"策略禁用）
- `_detect_background_layers()` 直接返回 `[]`（画布底部连续背景合并禁用）

三条闸门都在方法最前面，**不影响其它逻辑路径**，切换开关不需要修改任何其它代码。

**不在此开关范围**（由 LayoutOptimizer 层单独控制，见
[`../03-topics/layout-optimizer.md`](../03-topics/layout-optimizer.md) 的
`images_dir` / `flatten_config` 小节）：

- `ImageLayerFlatten`（Step 1.2）"容器 bg + image 子"的合并
- `DOMRestructure` 的多 url 背景内联合成
- `background_flatten.flatten_multi_url_backgrounds` 文本兜底

CLI 入口 `psd_to_code.py --no-smart-merge` 会把 `smart_merge=False` 写入
`PipelineContext`，由 `ParseToIrStage` 透传到 `parse_psd_to_ir(..., smart_merge=False)`
再传给 `LayerExporter`；`LayoutOptimizeStage` 同时读取该值，相应地传
`images_dir=None` 和 `FlattenConfig(enabled=False)`、并跳过 `flatten_multi_url_backgrounds`，
确保"一键关全部 4 类合图"的语义一致。

旧入口 `PSDToHTMLConverter(psd_path, smart_merge=False)` 行为等价。

## 与 IR 的连接

- `LayerExporter` 产出 **legacy dict 树**。
- `core/psd/parser.py::parse_psd_to_ir` 将其包装进 `Document.root.meta['legacy_roots']`。
- 之后由 `core/ir/adapters.to_legacy_layers` 原样取出给 codegen。
