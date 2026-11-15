# 模块：`core/render/`

> **本文解决什么**：讲清像素渲染侧的两个子系统——**效果渲染** 与 **组扩展渲染**。
> **不讨论什么**：图层何时选择哪条渲染路径（见 `core-extract.md` 与 `../03-topics/group-rendering.md`）。

## 位置

```
core/render/
├── __init__.py
├── adjustments_patch.py    # psd-tools 补丁（调整层 / 描边绘制修复）
└── effects/
    ├── __init__.py
    ├── effects_renderer.py  # ★ Facade: render_layer_with_effects()
    ├── stroke_renderer.py   # StrokeRenderer
    ├── shadow_renderer.py   # DropShadowRenderer + InnerShadowRenderer
    ├── glow_renderer.py     # OuterGlowRenderer + InnerGlowRenderer
    └── overlay_renderer.py  # ColorOverlayRenderer + GradientOverlayRenderer
```

---

## Facade：`render_layer_with_effects`

位置：`core/render/effects/effects_renderer.py`

```python
def render_layer_with_effects(layer) -> tuple[Image, bbox] | None:
    """
    渲染图层并叠加所有启用的效果。
    - 如果有需要扩展画布的效果（外描边/外发光/投影），
      自动扩展并返回扩展后的 bbox。
    - 单图层级入口：只拿本层的 topil() / composite()，不合成其他图层。
    """

def render_layer_with_effects_on_image(img, bbox, layer, ...) -> tuple[Image, bbox]:
    """
    组合成场景：外部已经准备好一张合成图（如多图层 numpy 合成的结果），
    只对它叠加效果。用于剪切蒙版组等场景。
    """
```

上游代码（`LayerExporter`）**只**使用这两个入口。
新增效果时应让它们自动被 Facade 调起，而不是在上游逐个判断。

### 形状层底图获取策略

`render_layer_with_effects` 内部对 shape 图层的底图获取逻辑：

```
topil() 不为 None → 直接使用
    ↓ 为 None
shape + 有 Stroke effect → _render_shape_base_from_fill()（自渲染）
shape + 无 Stroke effect → composite()（vector path 更准确）
非 shape → composite()
```

`_render_shape_base_from_fill()` 用 SoCo 纯色 + origination 几何合成基础图，
绕开 composite 的描边覆盖 bug。仅在有 Stroke 效果时启用——无描边时 composite()
的 vector path 渲染比 origination 参数更准确。

辅助函数 `_draw_rounded_rect_variable()` 支持四角不同半径的圆角矩形，
实现 CSS border-radius 规范的缩放规则（相邻角半径之和超过边长时等比缩小）。

详见 [`../05-conventions/known-pitfalls.md`](../05-conventions/known-pitfalls.md) #27。

## 各效果渲染器

| 文件 | 渲染器 | 是否需要扩展画布 |
| ---- | ------ | ---------------- |
| `stroke_renderer.py`  | StrokeRenderer（position=outside 才扩展） | 条件性 |
| `shadow_renderer.py`  | DropShadowRenderer / InnerShadowRenderer | DropShadow ✓；InnerShadow ✗ |
| `glow_renderer.py`    | OuterGlowRenderer / InnerGlowRenderer | Outer ✓；Inner ✗ |
| `overlay_renderer.py` | ColorOverlayRenderer / GradientOverlayRenderer | ✗ |

各渲染器都是**独立类 + 静态方法**（`can_render(effect)` / `render(...)`），
由 Facade 用 if/elif 链按 PS 绘制顺序分派，没有共同基类，也没有注册表。
（历史上的 `effect_base.py` 基类从未被采用，已于 2026-11 删除。）

> 详细渲染算法见 [`../03-topics/effects-rendering.md`](../03-topics/effects-rendering.md)

## 组扩展渲染（已迁到 `core/extract/layer_exporter.py`）

组的"效果溢出"不是一个独立的渲染器类，而是 `LayerExporter` 内部的一组方法：

| 方法 | 位置 | 职责 |
| ---- | ---- | ---- |
| `_group_has_overlay_effects` | `layer_exporter.py:1109` | 判断组是否带叠加类效果 |
| `_calc_group_expand` | `layer_exporter.py:1139` | 计算需要的向外扩展像素 |
| `_render_group_with_hybrid_strategy` | `layer_exporter.py:1190` | 扩展画布手动渲染 + `composite()` 覆盖的混合策略 |
| `_merge_group_as_single_image` | `layer_exporter.py:1616` | 无溢出效果的组：直接整体合成 |

> 历史上的 `core/render/layer_renderer.py`（`GroupRenderer.render_group_expanded`）
> 已于 2026-11 删除：它是 P2 迁移期的中间产物，主流程从未调用。当前唯一实现
> 就是上表这套方法，**不要**再按旧文档去找 `GroupRenderer`。

**使用前提：** 一般不会直接调用它，而是由
`core/extract/layer_exporter.py` 的 `_merge_group_as_image` 在检测到效果
溢出时使用"手动扩展 + composite 覆盖"的混合策略。

### ⚠️ 硬约束（来自历史经验）

1. **子组必须用 `sub_grp.composite(viewport=grp_bbox)` 渲染**，
   不能递归调用手动渲染+裁切。
2. **扩展画布外部保留手动渲染结果，内部区域用 `group_layer.composite(viewport=grp_bbox)` 的 PS 原生结果覆盖**。
3. **不要** 把混合策略重构成"只走一边"。

详见 [`../03-topics/group-rendering.md`](../03-topics/group-rendering.md) 和
[`../05-conventions/known-pitfalls.md`](../05-conventions/known-pitfalls.md)。

## `adjustments_patch.py`（psd-tools 补丁）

位置：`core/render/adjustments_patch.py`

```python
# 模块级自动注册：import 即生效
import scripts.core.render.adjustments_patch
```

此模块对 psd-tools 的 composite 管线进行 monkey-patch，修复上游已知 bug
或补充缺失功能。**import 即自动注册**，无需显式调用。

### 补丁清单

| 补丁 | 修复对象 | 说明 |
| ---- | -------- | ---- |
| `apply_blackandwhite` | `ADJUSTMENT_FUNC['blackandwhite']` | psd-tools 原生不支持 Black & White 调整层，此补丁实现完整的色相加权灰度转换算法（含 tint 着色） |
| `_patched_draw_stroke_effect` | `draw_stroke_effect` | 修复全填满形状（shape 全 1）时 scharr 边缘检测→NaN→mask 100% 覆盖的 bug，详见 [known-pitfalls #26](../05-conventions/known-pitfalls.md#26) |

### 注入机制

- **ADJUSTMENT_FUNC** 是简单字典注入：`ADJUSTMENT_FUNC['blackandwhite'] = apply_blackandwhite`
- **draw_stroke_effect** 需要修改 `Compositor._apply_stroke_effect.__globals__`
  字典（因为函数引用在导入时已绑定到 `__globals__`，单纯修改模块属性不生效）

### 扩展指南

新增补丁时：
1. 在此文件中实现修复函数
2. 在 `register_*` 函数中注册
3. 确保模块级调用（文件底部的 `register_*()` 调用）
4. 在 `known-pitfalls.md` 记录根因和修复方案
5. 在 `tests/test_adjustments_patch.py` 添加对应测试

---

## 工具依赖

- `common.image_utils.ImageArrayUtils`：PIL ↔ numpy float array 的统一入口
  - `pil_to_float_array(img)` —— RGBA 图
  - `pil_l_to_float_array(img)` —— L 灰度图
  - `float_array_to_pil(arr)` —— float → uint8 RGBA PIL
  - `float_to_uint8_rgba(arr)` —— float → uint8 numpy
- `common.image_utils.ImageBlendUtils`：alpha blend 等
- `common.image_utils.BBoxUtils`：bbox 常用操作

> 禁止在 `core/render/` 下再手写 `np.array(img, dtype=np.float32) / 255.0`，
> 统一走 `ImageArrayUtils`。
