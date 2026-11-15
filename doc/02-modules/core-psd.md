# 模块：`core/psd/`

> **本文解决什么**：讲清从 PSD 文件到 IR 的入口链。
> **不讨论什么**：图层导出的决策（在 `core/extract/`）和像素渲染（在 `core/render/`）。

## 位置

```
core/psd/
├── __init__.py
├── parser.py            # parse_psd_to_ir() —— 主入口
├── classifier.py        # LayerClassifier
└── text_extractor.py    # TextExtractor
```

## `parse_psd_to_ir()`

位置：`core/psd/parser.py`

```python
def parse_psd_to_ir(psd_path, output_dir, psd=None) -> tuple[Document, LayerExporter, list[dict]]
```

流程：
1. 实例化 `LayerExporter(psd, output_dir)`
2. `legacy_tree = exporter.export_layers(psd)`
3. 将 legacy dict 树包装成 `Document`（`GroupNode` 作为 root，
   `meta['legacy_roots']` 保留原 tree 供 adapter 还原）

**返回三元组** 的设计目的：让 `ParseToIrStage` 既拿到强类型 IR，
又保留对 `LayerExporter` 的引用以便后续读取统计信息。

## `LayerClassifier`

位置：`core/psd/classifier.py`

```python
class LayerClassifier:
    def __init__(self, canvas_width, canvas_height): ...
    def is_text_layer(self, layer) -> bool
    def is_group_layer(self, layer) -> bool
    def is_pixel_layer(self, layer) -> bool
    def has_expanding_effects(self, layer) -> bool
```

用途：
- **判断图层类型**：是文本 / 组 / 像素
- **判断效果是否需要扩展画布**：外描边 / 外发光 / 投影 → 返回 `True`
  供 `LayerExporter` 决定是否使用扩展渲染路径

## `TextExtractor`

位置：`core/psd/text_extractor.py`

静态工具类，主要方法：

```python
class TextExtractor:
    @staticmethod
    def has_transform(layer) -> bool:
        """是否有旋转/倾斜变换（有则应降级为图片）"""
    # ... 还有样式抽取、多段 run 合并等方法
```

**关键点**：文字图层一律栅格化为图片——`LayerExporter` 不再调用
`TextExtractor` 抽取文本，而是直接导出 PNG（layer dict 的 `type` 仍保留
`"text"`），由 `TextRenderer` 输出 `background-image` + `data-type="text"`。
本模块保留为文本信息的独立工具（调试 / 未来需要文本语义时可直接引用）。

## 在 Pipeline 中的位置

```
Stage 2 ParseToIrStage
  └─ parse_psd_to_ir()
       └─ LayerExporter.export_layers()
            ├─ 内部使用 LayerClassifier 判类型
            ├─ 文字图层直接导出 PNG（不再抽文本）
            └─ 调用 run_handlers(...) 决策每个图层/组
```

## 扩展

- 想识别新的图层语义（如"视频占位"）：在 `classifier.py` 加判定；
  然后在 `handlers.py` 加对应 Handler。
- 想重新支持可编辑文本：从 `TextExtractor` 取 `text` / `text_style` 写回
  layer dict，并在 codegen 里按 `type == "text"` 输出文本节点。
