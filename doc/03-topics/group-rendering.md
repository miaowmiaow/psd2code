# 主题：组渲染（含效果溢出）

> **本文解决什么**：讲清"组合并为单图"路径里最容易踩坑的混合渲染策略。
> **不讨论什么**：哪些图层会走合并路径（在 `core-extract.md` 的 Handler 链）。

---

## 背景

PSD 中一个"组"可能包含若干子图层（图片、shape、文字、子组等），各自带效果
（描边、阴影、发光）。当我们决定把组合并为单图时，需要在代码里模拟
Photoshop 的组级合成结果。

难点：
- 子图层的 **外描边 / 外发光 / 投影** 会溢出组的 bbox。
- 简单裁到 bbox → 丢掉溢出像素，画面残缺。
- 单纯手动逐层叠加 → 效果渲染质量比 PS 原生低（描边偏细、过渡偏硬）。

## 当前方案：混合渲染

实现位置：`core/extract/layer_exporter.py` 的 `_merge_group_as_image` 中
"效果溢出分支"：

```
1. 检测到子图层有 expanding 效果：
   grp_bbox = 组的 bbox；expand = 所有子图层扩展量的最大值
   expanded_bbox = expand(grp_bbox, expand)

2. 手动渲染（保留溢出）
   canvas_manual = render_group_expanded(group, grp_bbox, expand)
   —— 内部用 GroupRenderer 在 expanded_bbox 画布上逐层叠加

3. PS 原生组级合成（仅 bbox 内高质量）
   canvas_ps = group_layer.composite(viewport=grp_bbox)

4. 合并：把 canvas_ps 覆盖到 canvas_manual 的 bbox 内部区域
   canvas_final = canvas_manual
   canvas_final[bbox_rect] = canvas_ps

结果：
- 外部（超出 bbox）：手动渲染的溢出效果像素
- 内部：PS 原生级画质
```

## 为什么要这样？

| 方案 | 问题 |
| ---- | ---- |
| 只用手动渲染 | 描边/发光过渡偏硬，内部像素不匹配 PS |
| 只用 `composite(viewport=expanded_bbox)` | PS 的单组 composite **不输出超出组 bbox 的效果像素**；外部依旧空 |
| `render_layer_with_effects` 在扩展画布上渲染整个组 | 需要把整个组当成单图层 layer，对嵌套子组不适用 |

**混合** 是目前已知唯一能同时满足"内部高质量 + 外部保留"的方案。

## 子组（嵌套组）的特别处理

位置：`core/extract/layer_exporter.py` 的 `_render_subgroup`

```python
# 关键：子组必须用 composite(viewport=...) 渲染
sub_img = sub_grp.composite(viewport=grp_bbox)
```

**不要** 改回"递归调用 `_render_group_expanded` + 裁切"。
历史经验：这样做在"solgan/副标题"这类场景会沿圆角轮廓多出 ~75px/行 的描边。
只有 `composite()` 正确复现了 PS 的组级效果裁切行为。

## 所需工具

- `core/render/layer_renderer.GroupRenderer.render_group_expanded(...)`
- `group_layer.composite(viewport=bbox)`（psd-tools 原生）
- `common.image_utils.ImageArrayUtils.pil_to_float_array / float_array_to_pil`

## 硬约束（不要破坏）

1. **子组必须用 `composite(viewport=grp_bbox)` 渲染**，不得降级为手动递归。
2. **外溢效果必须走混合策略**：`_render_group_expanded` + composite 覆盖。
3. **不要重构 `_merge_group_as_image` 的"效果溢出分支"为单边方案**。

违反以上任何一条都很可能引入像素级回归。修改前请先跑一次 baseline diff。
详见 [`../05-conventions/known-pitfalls.md`](../05-conventions/known-pitfalls.md)。

## 调试技巧

- 把 `canvas_manual` 和 `canvas_ps` 分别落盘，看差异。
- 检查 `expand` 是否足够覆盖所有子图层的最大扩展量（外描边 size + 投影距离+blur 等）。
- 若仅仅底部残留 / 边缘残留：大概率是子组没走 `composite()`。
- 若内部画质偏弱：大概率是没覆盖步骤 4。
