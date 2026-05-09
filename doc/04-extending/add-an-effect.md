# 扩展：新增一种效果渲染器

> 例：支持 `Satin`（绸缎）效果。

## 套路

1. 在 `core/ir/effects.py` 加 `SatinSpec`（pydantic 类）。
2. 在 `core/render/effects/` 新增 `satin_renderer.py`，继承 `EffectRenderer`。
3. 在 Facade `effects_renderer.py` 里把它串进去。
4. 如果效果会扩展画布：让它的 `compute_expand()` 返回非零，并确保 Facade 汇总。

## 1. IR 定义

`core/ir/effects.py`：

```python
class SatinSpec(_EffectBase):
    kind: Literal["satin"] = "satin"
    color: Color
    angle_deg: float = 0
    distance_px: float = 0
    size_px: float = 0


EffectSpec = Union[
    StrokeSpec, DropShadowSpec, InnerShadowSpec,
    OuterGlowSpec, InnerGlowSpec,
    ColorOverlaySpec, GradientOverlaySpec,
    SatinSpec,     # ←★ 新增
]
```

`core/ir/__init__.py` 的 re-export 列表也要加。

## 2. 渲染器

`core/render/effects/satin_renderer.py`：

```python
from PIL import Image
import numpy as np

from common.image_utils import ImageArrayUtils
from .effect_base import EffectRenderer


class SatinRenderer(EffectRenderer):
    EFFECT_NAME = "Satin"

    def can_render(self, effect) -> bool:
        return getattr(effect, "__class__", type(None)).__name__ == "Satin"

    def compute_expand(self, effect) -> int:
        # Satin 是内部效果，不扩展画布
        return 0

    def render(self, canvas_arr, layer, effect, offset) -> np.ndarray:
        # TODO: 实际像素算法
        # 1. 拿 alpha = canvas_arr[..., 3]
        # 2. 按 angle_deg / distance_px / size_px 生成干涉图案
        # 3. 用 effect.color 着色后 alpha blend 回 canvas_arr
        return canvas_arr
```

## 3. Facade 串入

`core/render/effects/effects_renderer.py` 顶部：

```python
from .satin_renderer import SatinRenderer
```

在 Facade 内部创建渲染器的地方（搜索 `ColorOverlayRenderer()` 附近）加上
`SatinRenderer()`，并按 PS 的绘制顺序决定位置。

参考顺序（自底向上）：
```
DropShadow → OuterGlow → [原图层] → InnerShadow → InnerGlow →
ColorOverlay → GradientOverlay → Satin → Stroke
```

## 4. 扩展画布逻辑

如果你的效果会扩展（不是我们这个 Satin 的情况）：

1. `compute_expand()` 返回正整数。
2. Facade 已有统一代码汇总 `max(expand_i)` 并扩画布、返回扩展 bbox，
   **不需要** 你手写扩画布代码。

## 5. IR 填充

`core/psd/parser.py`（或更细的 effect 抽取器）把 PSD 的 effect 映射成
`SatinSpec`，加到 `node.effects`。

## 6. 回归验证

- 先跑 baseline diff 确保"没有 Satin 的 PSD"输出完全不变。
- 用一个真的带 Satin 的 PSD 看 `output/<stem>/images/` 里的像素是否与 PS 导出一致。
  允许少量容差，但不应有"明显缺失的效果层"。

## Checklist

- [ ] `SatinSpec` pydantic 类 + re-export
- [ ] `satin_renderer.py` 继承 `EffectRenderer`，实现 can_render / compute_expand / render
- [ ] Facade 串入且顺序正确
- [ ] `core/psd/parser.py` 能把 PSD 的 Satin 塞进 IR
- [ ] 不扩展画布：`compute_expand() == 0`；扩展：返回合理值
- [ ] 用 `ImageArrayUtils` 做 PIL↔numpy 转换，不手写 `np.array(img,...)/255`
- [ ] baseline diff 通过（无 Satin 的 PSD 零差异）
- [ ] `03-topics/effects-rendering.md` 的渲染器清单补一行
