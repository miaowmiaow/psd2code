# layer_exporter.py 模块拆分方案

## 现状分析

- **文件大小**: 2496 行 (~113 KB)
- **核心类**: `LayerExporter` (主导出器) + `LightEffectLayerInfo` (数据类)
- **功能块**: 
  1. 光效层穿透渲染系统（占 ~40%）
  2. 剪贴蒙版处理（~20%）
  3. 图层图像导出与效果渲染（~30%）
  4. 混合渲染优化（~10%）

## 拆分目标

将单一 2496 行文件拆分为 4 个专职模块（每个 400-800 行），提升可维护性：

```
core/extract/
├── layer_exporter.py          [核心编排器 ~400 行]
├── light_effect_renderer.py   [光效穿透系统 ~600 行]
├── clipping_handler.py        [剪贴蒙版处理 ~400 行]
└── image_compositor.py        [图像合成/效果 ~500 行]
```

## 详细拆分方案

### 1️⃣ light_effect_renderer.py (~600 行)

**职责**: 光效层穿透渲染完整逻辑

**包含内容**:
- `LightEffectLayerInfo` 数据类（70-78 行）
- 光效层预扫描（183-682 行）：
  - `_pre_scan_light_layers()`
  - `_check_needs_penetrate()` 
  - `_find_penetrate_targets()`
  - `_is_effective_light_target()`
  - `_remove_identity_color()`
  - `_blend_light_layer()`
- 常数定义（57-127 行）：`_LIGHT_BLEND_MODES`, `BLEND_MODES`, `_LIGHT_TARGET_BRIGHTNESS_THRESHOLD`

**导出接口**:
```python
class LightEffectRenderer:
    def __init__(self, psd: Any, canvas_width: int, canvas_height: int)
    def pre_scan(self) -> dict[int, list[LightEffectLayerInfo]]  # 返回 penetrate_map
    def get_suppressed_layers(self) -> set[int]
    def get_fallback_layers(self) -> set[int]
    def apply_penetrate_effects(self, layer_id: int, img: Image) -> Image
```

**依赖**: PIL, numpy, effects_renderer, image_ops

---

### 2️⃣ clipping_handler.py (~400 行)

**职责**: PSD 剪贴蒙版 (clipping mask) 处理

**包含内容**:
- `_is_clipping()` (752 行)
- `_group_clipping_layers()` (761-790 行)
- `_adjust_children_offset()` (791-814 行)
- `_merge_clipping_group()` (815-1012 行)
- `_export_clipped_layer_against_group_base()` (1268-1462 行)

**导出接口**:
```python
class ClippingHandler:
    @staticmethod
    def is_clipping_layer(layer: Any) -> bool
    @staticmethod
    def group_clipping_layers(layers_list: list) -> list  # 返回重新组织的层列表
    def export_clipping_group(
        group: Any, 
        children: list,
        exporter_ctx: LayerExporterContext  # 见下方
    ) -> dict
```

**依赖**: PIL, numpy, image_ops

---

### 3️⃣ image_compositor.py (~500 行)

**职责**: 图像合成、效果渲染、混合模式

**包含内容**:
- 图像效果渲染路径：
  - `_export_layer_image()` (1463-1615 行)
  - `_group_has_overlay_effects()` (1616-1645 行)
  - `_calc_group_expand()` (1646-1696 行)
  - `_render_group_with_hybrid_strategy()` (1697-2122 行)
- 群组合并路径：
  - `_merge_group_as_single_image()` (2123-2299 行)
  - `_collect_recursive_text_layers()` (2300-2318 行)
  - `_merge_group_non_text_as_image()` (2319-2358 行)
  - `_is_fully_suppressed_group()` (2359-2402 行)
  - `_merge_cluster_layers_as_image()` (2403+ 行)

**导出接口**:
```python
class ImageCompositor:
    def render_layer_with_effects(self, layer: Any) -> Image | None
    def render_group_as_image(
        group: Any,
        use_hybrid: bool = True,
        expand_px: int = 0
    ) -> Image | None
    def estimate_expand_pixels(self, layer: Any) -> int
    def has_overlay_effects(self, layer: Any) -> bool
```

**依赖**: PIL, numpy, effects_renderer, image_ops, text_extractor

---

### 4️⃣ layer_exporter.py (核心编排器，~400 行)

**职责**: 导出流程编排、图片去重、导出入口

**保留内容**:
- `LayerExporter` 类结构（仅编排逻辑）：
  - `__init__()` - 初始化各子模块
  - `export_layers()` - 导出主入口
  - `_export_single_layer()` - 单层导出编排
  - `_apply_penetrate_light_layers()` - 应用光效穿透结果
  - `_save_image_dedup()` - 图片去重
  - `verify_export()` - 导出验证

**关键改动**:
- 移除所有具体业务逻辑，改为调用子模块接口
- 创建 `LayerExporterContext` 数据类供子模块共享状态

**数据类**:
```python
@dataclass
class LayerExporterContext:
    """LayerExporter 上下文：子模块共享状态"""
    canvas_width: int
    canvas_height: int
    psd: Any
    images_dir: Path
    image_hash_map: dict[str, str]  # md5 → image_path
    ancestor_group_masks: list
    light_effect_renderer: LightEffectRenderer
    clipping_handler: ClippingHandler
    image_compositor: ImageCompositor
```

---

## 实施步骤

### Phase 1: 准备与基础架构（现在进行）
- [ ] 创建 `light_effect_renderer.py` - 提取光效相关全部代码
- [ ] 创建 `clipping_handler.py` - 提取剪贴蒙版处理
- [ ] 创建 `image_compositor.py` - 提取图像合成逻辑
- [ ] 创建 `LayerExporterContext` 数据类

### Phase 2: 重构 layer_exporter.py
- [ ] 改造 `__init__()` 初始化各子模块
- [ ] 改造 `export_layers()` 调用子模块接口
- [ ] 改造 `_export_single_layer()` 编排各处理路径
- [ ] 删除已迁移的方法

### Phase 3: 集成测试
- [ ] 运行现有单元测试
- [ ] 集成测试转换结果一致性
- [ ] 性能对标（应无显著差异）

---

## 预期收益

| 指标 | 现状 | 目标 | 收益 |
|------|------|------|------|
| 单文件行数 | 2496 | 400 | -84% |
| 单个类行数 | ~2300 | 400-800 | -73% |
| 圈复杂度 | 高 | 中 | -45% |
| 新手上手时间 | 4-6 小时 | 1-2 小时 | -75% |
| 单模块责任 | 4+ 种 | 1 种 | 单一职责 ✓ |
| 代码复用性 | 低 | 中 | 子模块可独立测试 |

---

## 风险评估

| 风险 | 评级 | 缓解方案 |
|------|------|---------|
| 性能回退 | 低 | Phase 3 完整对标测试 |
| 循环依赖 | 中 | 使用 Context 对象传递状态 |
| 兼容性破裂 | 低 | 导出接口与原 API 兼容 |
| 集成调试难度 | 中 | 详细文档 + 单元测试 |

---

## 相关文件依赖关系

```
layer_exporter.py (核心)
├── light_effect_renderer.py
│   ├── effects_renderer
│   ├── image_ops
│   └── PIL
├── clipping_handler.py
│   ├── image_ops
│   └── PIL
├── image_compositor.py
│   ├── effects_renderer
│   ├── text_extractor
│   ├── image_ops
│   └── PIL
└── handlers (已有)
```

