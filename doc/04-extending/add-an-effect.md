# 扩展：新增一种效果渲染器

> 例：支持 `Satin`（绸缎）效果。

## 套路

1. 在 `core/ir/effects.py` 加 `SatinSpec`（pydantic 类）。
2. 在 `core/render/effects/` 新增 `satin_renderer.py`（独立类 + 静态方法 `can_render` / `render`）。
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

> 注意：各 renderer **没有共同基类**（历史 `effect_base.EffectRenderer` 从未被采用，
> 已于 2026-11 删除）。约定是「独立类 + 两个静态方法」，由 Facade 的 if/elif 链分派。

```python
import numpy as np


class SatinRenderer:
    """绸缎（Satin）效果渲染器。"""

    @staticmethod
    def can_render(effect) -> bool:
        return str(effect) == 'Satin'

    @staticmethod
    def render(effect, layer, alpha: np.ndarray, h: int, w: int) -> np.ndarray | None:
        """在 (h, w, 4) 画布上叠加效果，返回新的数组（None 表示不处理）。

        约定：
        - 输入 alpha 是基础图层 alpha（H, W, 1），画布已被 Facade 扩到 h×w；
        - Satin 属于内部效果，不扩展画布，因此无需 compute_expand。
        """
        # 1. 拿 alpha = alpha[..., 3]
        # 2. 按 angle_deg / distance_px / size_px 生成干涉图案
        # 3. 用 effect.color 着色后 alpha blend 回画布
        return None
```

> **画布扩展**由 Facade 统一决定（`effects_renderer.py` 里的 `_calc_expand` 逻辑），
> 新增 renderer 只在需要外扩时通知 Facade，不要自己扩画布。

## 3. Facade 串入

`core/render/effects/effects_renderer.py` 顶部：

```python
from .satin_renderer import SatinRenderer
```

在 `effects_renderer.py` 的 if/elif 分派链里（搜索 `ColorOverlayRenderer.can_render`
附近）插入一个分支，并按 PS 的绘制顺序决定位置：

```python
elif SatinRenderer.can_render(effect):
    arr = SatinRenderer.render(effect, layer, ext_alpha, new_h, new_w)
```

> 目前是**显式 if/elif 链**，不是注册表：新增效果必须同时改 Facade，
> 这也是为什么这里要写清"按 PS 绘制顺序插在哪一支"。

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
- [ ] `satin_renderer.py` 提供 `can_render(effect)` / `render(effect, layer, alpha, h, w)` 静态方法（无基类）
- [ ] Facade 串入且顺序正确
- [ ] `core/psd/parser.py` 能把 PSD 的 Satin 塞进 IR
- [ ] 不扩展画布：`compute_expand() == 0`；扩展：返回合理值
- [ ] 用 `ImageArrayUtils` 做 PIL↔numpy 转换，不手写 `np.array(img,...)/255`
- [ ] baseline diff 通过（无 Satin 的 PSD 零差异）
- [ ] `03-topics/effects-rendering.md` 的渲染器清单补一行
