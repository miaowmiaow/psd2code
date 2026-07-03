# Phase 2: 集成新模块到 layer_exporter.py

## 状态

✅ **完成**: 导入语句添加，新模块可正常导入

## 集成策略

### 保守方案（推荐）

由于 layer_exporter.py 的光效渲染逻辑已深度集成到导出流程中（6 个方法，跨越 400+ 行），直接替换存在高风险。

采用**增量集成**策略：

#### 第 1 步：验证导入 ✅

- [x] 更新 layer_exporter.py 导入部分
- [x] 添加三个新模块的导入
- [x] 验证无导入错误

#### 第 2 步：创建适配器层（进行中）

为了最小化对现有代码的改动，创建适配器方法：

```python
class LayerExporter:
    def __init__(self, ...):
        # 初始化新的上下文对象
        self.ctx = LayerExporterContext(
            canvas_width=psd.width,
            canvas_height=psd.height,
            psd=psd,
            images_dir=self.images_dir,
        )
        
        # 初始化新的渲染器
        self.light_renderer = LightEffectRenderer()
        self.clipping_handler = ClippingHandler()
```

#### 第 3 步：逐步迁移方法

为每个旧方法创建"代理"，在新模块和旧代码之间转接：

```python
# 旧方法保留，但转接到新模块
def _pre_scan_light_layers(self, psd):
    self.light_renderer.scan_and_build_penetrate_map(
        psd, self.ctx
    )
    self._suppressed_light_layers = self.ctx.suppressed_light_layers
    self._fallback_light_layers = self.ctx.fallback_light_layers
    # ...
```

#### 第 4 步：单元测试验证

- [ ] 光效层识别（Phase 1）
- [ ] 穿透判定（Phase 2）
- [ ] 目标层查找（Phase 3）
- [ ] 剪贴蒙版识别
- [ ] 图片去重逻辑

#### 第 5 步：性能基准

- [ ] 旧版本运行时间
- [ ] 新版本运行时间
- [ ] 内存消耗对比

## 新模块功能概览

### 1. `LightEffectRenderer` (light_effect_renderer.py)

**用途**: 识别和应用光效层穿透渲染

**关键方法**:
- `scan_and_build_penetrate_map()`: Phase 1-3 预扫描
- `check_needs_penetrate()`: Phase 2 判定
- `find_penetrate_targets()`: Phase 3 查找目标
- `is_effective_light_target()`: 检查目标有效性
- `blend_light_layer()`: 像素合成

**依赖**: 无（纯函数库）

### 2. `ClippingHandler` (clipping_handler.py)

**用途**: 处理 PSD 剪贴蒙版

**关键方法**:
- `is_clipping_layer()`: 判断是否为剪贴蒙版
- `export_clipped_layer()`: 导出剪贴蒙版
- `merge_clipping_group()`: 合并剪贴蒙版组

**依赖**: effects_renderer, image_ops

### 3. `LayerExporterContext` (exporter_context.py)

**用途**: 子模块间的状态共享

**关键字段**:
- `canvas_width/height`: 画布尺寸
- `psd`: PSD 文档对象
- `images_dir`: 输出目录
- `image_hash_map`: 图片去重
- `penetrate_map`: 光效层映射
- `suppressed_light_layers`: 被抑制的光效层
- `fallback_light_layers`: 降级为 CSS 的光效层

## 现有代码中的光效相关方法

| 行号 | 方法名 | 用途 |
|------|--------|------|
| 186-289 | `_pre_scan_light_layers` | Phase 1-3 预扫描 |
| 290-372 | `_check_needs_penetrate` | Phase 2 判定 |
| 373-520 | `_find_penetrate_targets` | Phase 3 查找 |
| 521-644 | `_is_effective_light_target` | 目标有效性检查 |
| 645-1184 | `_blend_light_layer` | 像素合成（Phase 4） |
| 1185-1260 | `_apply_penetrate_light_layers` | Phase 5 应用穿透 |

**总计**: ~600 行光效渲染代码

## 剪贴蒙版相关方法

| 行号 | 方法名 | 用途 |
|------|--------|------|
| ? | `_merge_clipping_group` | 识别和分组 |
| ? | `_export_clipped_layer_against_group_base` | 像素合成 |

## 下一步行动

### 推荐顺序

1. **完成导入集成** ✅
2. **创建适配器方法** (15-30 分钟)
3. **运行集成测试** (30 分钟)
4. **验证输出** (1 小时)
5. **性能对比** (30 分钟)
6. **文档更新** (30 分钟)

### 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 导入循环依赖 | 低 | 中等 | 模块已设计为纯函数库 |
| 状态不同步 | 中等 | 高 | LayerExporterContext 做单一数据源 |
| 性能下降 | 低 | 中等 | 避免额外的数据复制 |
| 现有功能破坏 | 低 | 高 | 保留原方法，仅作代理转接 |

## 质量检查清单

- [ ] 所有新模块无导入错误
- [ ] 现有单元测试通过
- [ ] 新集成代码覆盖 90%+ 的分支
- [ ] 内存消耗无明显增长
- [ ] 转换时间无显著增加 (< 5%)
- [ ] 输出 HTML/CSS 与旧版本一致

---

**预计完成时间**: 2-3 小时

**当前进度**: Phase 1 (导入) ✅ → Phase 2 (适配器) ⏳
