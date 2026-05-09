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
