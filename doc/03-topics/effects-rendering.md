# 主题：效果渲染

> **本文解决什么**：给接手者一份"效果渲染面板"的地图：谁负责哪个效果、怎么判断
> 是否需要扩展画布、上游如何调度。
> **不讨论什么**：某个具体算法的像素级推导（代码即规格）。

---

## Facade：唯一入口

位置：`core/render/effects/effects_renderer.py`

```python
render_layer_with_effects(layer)                          # 从 layer 拉底图 + 叠效果
render_layer_with_effects_on_image(img, bbox, layer, ...) # 外部提供底图 + 叠效果
```

上游只调这两个函数，不直接触碰具体渲染器。

## 效果分派

Facade 内部按固定顺序尝试：

```
1. 拿底图：base = layer.topil() or layer.composite()
2. 依次询问各渲染器：effect_renderer.can_render(layer)?
   如果 yes → render 并叠加到画布
3. 累计最大扩展量 → 返回 expanded bbox
```

## 渲染器清单

| 文件 | 渲染器 | PSD effect name | 是否扩展画布 |
| ---- | ------ | --------------- | ------------ |
| `stroke_renderer.py` | StrokeRenderer | Stroke | position=outside 时扩展 |
| `shadow_renderer.py` | DropShadowRenderer  | DropShadow | ✓ |
| `shadow_renderer.py` | InnerShadowRenderer | InnerShadow | ✗（内部绘制） |
| `glow_renderer.py`   | OuterGlowRenderer   | OuterGlow | ✓ |
| `glow_renderer.py`   | InnerGlowRenderer   | InnerGlow | ✗ |
| `overlay_renderer.py`| ColorOverlayRenderer | ColorOverlay | ✗ |
| `overlay_renderer.py`| GradientOverlayRenderer | GradientOverlay | ✗ |

### "扩展画布"是什么意思

某些效果会在图层 bbox 之外产生像素（例如外描边向外扩 10px，或投影距离 20px）。
渲染器必须：
1. 计算自身需要的 expand 像素数。
2. Facade 汇总所有渲染器的 expand 后，扩大画布。
3. 在扩大画布上渲染，返回扩展后的 bbox。

上游（`LayerExporter`）拿到扩展 bbox 后，存在 legacy dict 的 `left/top/width/height`，
HTML 生成器就会据此定位元素。

## 效果渲染器的基类

位置：`core/render/effects/effect_base.py`

```python
class EffectRenderer(ABC):
    def can_render(self, layer) -> bool: ...
    def compute_expand(self, effect) -> int: ...
    def render(self, canvas_arr, layer, effect, offset) -> np.ndarray: ...
```

通用工具（alpha 通道抽取、距离场等）也在这里。

## 与 IR 的关系

目前 `ImageNode.effects` 字段只在 IR 层"登记"了效果列表（`EffectSpec`），
像素渲染**不是**在 target 里做的——而是在 `LayerExporter` 阶段把效果烘焙进导出的 PNG。
这与 PSD 行为一致（像素化 = 所见即所得），也保证 HTML 不需要运行时重绘效果。

未来如果要支持"矢量保留效果"（例如输出 CSS filter 模拟阴影），
可以让某个 target 读 IR 的 `EffectSpec` 并跳过 LayerExporter 的烘焙步骤。
这正是把效果抬到 IR 的原因。

## 新增一个效果

见 [`../04-extending/add-an-effect.md`](../04-extending/add-an-effect.md)。

---

## psd-tools composite 补丁

位置：`core/render/adjustments_patch.py`

psd-tools 的 `composite()` 调用路径中存在若干 bug 或缺失功能，
项目通过 monkey-patch 方式在运行时修复。补丁模块 **import 即自动注册**。

### 已修复问题

| 问题 | 症状 | 补丁函数 |
| ---- | ---- | -------- |
| Black & White 调整层不支持 | composite 忽略该调整层，不转灰度 | `apply_blackandwhite` |
| `draw_stroke_effect` 对全填满形状产出错误 mask | 形状层的描边颜色覆盖整个填充颜色（如淡黄变全绿） | `_patched_draw_stroke_effect` |

### 描边效果 bug 详解

当形状层的 vector mask 完全填满（全 1）时：
1. `scharr` 边缘检测返回全 0
2. `divide(0, 0)` → NaN → 被替换为 1.0
3. 描边 mask 变成 100% 覆盖 → 描边颜色替代填充颜色

修复：检测到 edges 全 0 时直接返回空 mask，跳过归一化。

详见 [`../05-conventions/known-pitfalls.md`](../05-conventions/known-pitfalls.md) #26。

---

## 形状层底图策略（`_render_shape_base_from_fill`）

位置：`core/render/effects/effects_renderer.py`

### 问题

shape 图层的 `topil()` 返回 None 时，需要决定如何获取基础图：
1. 自渲染：用 SoCo 纯色 + origination 几何（Rectangle / RoundedRectangle / Ellipse）合成
2. composite()：让 psd-tools 从存储的 Bézier 路径合成

### 决策规则

| 条件 | 采用策略 | 原因 |
| ---- | -------- | ---- |
| 有启用的 Stroke 效果（lfx2 FrFX） | 自渲染 | composite 有描边覆盖填充色 bug（见上节） |
| 无 Stroke 效果 | `layer.composite()` | vector path 比 origination 参数更准确 |

### 为什么不能无条件自渲染

PSD 图层中存在两套几何描述：
- **Origination（Live Shape 参数）**：`VECTOR_ORIGINATION_DATA` 的 `keyOriginRRectRadii` 等，是 PS UI 上编辑时的参数
- **Vector path（Bézier knots）**：实际存储的矢量路径，是 PS 渲染时使用的源数据

两者可能不一致——用户拖拽控制点修改过路径后，origination 参数可能**未同步更新**。
`composite()` 使用 vector path，因此在无描边 bug 时总是更准确。

典型案例：领奖.psd "圆角矩形 1"（38×38px）
- Origination radii: TL=10, TR=19, BL=19, BR=19
- 实际 vector path: 6 knots 近似形状
- 旧代码取平均半径 17 → 在 38px 尺寸上接近圆形（错误）
- composite() 正确渲染为圆角矩形（正确）

### 自渲染的四角独立半径支持

当确实需要自渲染时（有 stroke effect），圆角矩形支持四角不同半径：

1. 读取 `keyOriginRRectRadii` 中 topLeft / topRight / bottomLeft / bottomRight
2. 按 CSS border-radius 规范缩放：`if (tl+tr) > w or (bl+br) > w or (tl+bl) > h or (tr+br) > h` → 按最大超额比例等比缩小
3. `_draw_rounded_rect_variable()`：polygon + 16 段/角弧线近似

详见 [`../05-conventions/known-pitfalls.md`](../05-conventions/known-pitfalls.md) #27。
