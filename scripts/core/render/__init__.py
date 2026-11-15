"""Pixel rendering: effect renderers + psd-tools adjustments patch.

- ``effects/``: 描边 / 投影 / 发光 / 叠加等图层效果渲染
  （``effects_renderer`` 统一入口，各 ``*_renderer`` 为具体实现）
- ``adjustments_patch``: 修补 psd-tools 描边特效的绘制缺陷

> 历史的 ``layer_renderer.py``（P2 迁移期的图层渲染器）已于 2026-11 删除：
> 真正的图层渲染在 ``targets/html/codegen/layer_renderer.py``，本文件无任何引用。
"""
