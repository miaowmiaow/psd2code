# 模块：`core/render/`

> **本文解决什么**：讲清像素渲染侧的两个子系统——**效果渲染** 与 **组扩展渲染**。
> **不讨论什么**：图层何时选择哪条渲染路径（见 `core-extract.md` 与 `../03-topics/group-rendering.md`）。

## 位置

```
core/render/
├── __init__.py
├── layer_renderer.py     # GroupRenderer（组级扩展渲染）
└── effects/
    ├── __init__.py
    ├── effect_base.py
    ├── effects_renderer.py  # ★ Facade: render_layer_with_effects()
    ├── stroke_renderer.py
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

## 各效果渲染器

| 文件 | 渲染器 | 是否需要扩展画布 |
| ---- | ------ | ---------------- |
| `stroke_renderer.py`  | StrokeRenderer（position=outside 才扩展） | 条件性 |
| `shadow_renderer.py`  | DropShadowRenderer / InnerShadowRenderer | DropShadow ✓；InnerShadow ✗ |
| `glow_renderer.py`    | OuterGlowRenderer / InnerGlowRenderer | Outer ✓；Inner ✗ |
| `overlay_renderer.py` | ColorOverlayRenderer / GradientOverlayRenderer | ✗ |

`effect_base.py` 提供通用工具（如 alpha 通道处理、距离场）。

> 详细渲染算法见 [`../03-topics/effects-rendering.md`](../03-topics/effects-rendering.md)

## `GroupRenderer`（组扩展渲染）

位置：`core/render/layer_renderer.py`

```python
class GroupRenderer:
    def __init__(self, canvas_width, canvas_height): ...

    def render_group_expanded(
        self,
        group_layer,
        grp_bbox,                    # 组原始 bbox
        expand: int,                 # 向外扩展像素
        depth: int = 0,
    ) -> Image.Image:
        """
        在扩展画布上手动渲染组的所有子图层。
        用于处理 **效果溢出** 场景（子图层的外描边/投影超出组 bbox）。
        """
```

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
