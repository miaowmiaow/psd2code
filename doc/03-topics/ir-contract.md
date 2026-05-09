# 主题：IR 契约与演进策略

> **本文解决什么**：为什么要有 IR？现在为什么有"双轨"（IR + legacy dict）？
> 什么时候可以移除 legacy？
> **不讨论什么**：IR 字段字典（见 `../02-modules/core-ir.md`）。

---

## 为什么需要 IR

编译器的前后端必须有一个"中间表示"。对 psd2code 而言：

- **前端（core）** 只知道 PSD。它产出 IR，不关心谁会消费。
- **后端（targets）** 只知道某种产物（HTML / Vue / React）。它读 IR，
  不关心 IR 从 PSD 还是从别的输入（例如未来的 Figma）来。

如果没有 IR，新增一个 target 就要重新写一遍 "解析 PSD"；
新增一种输入就要修改所有 target。IR 把耦合从 `N × M` 降到 `N + M`。

## 为什么现在有"双轨"

1. 历史版本 `psd2html` 直接把 PSD 解析成 `list[dict]`（legacy 树）并喂给 HTML 生成器。
2. psd2code 引入 pydantic IR 是为了类型安全、校验、未来可扩展。
3. 但我们 **要求 `target=html` 的输出字节级不退化**。
   最稳妥的做法：
   - 让 `LayerExporter` 仍然产出 legacy 树（零改动）
   - IR 作为"薄包装"持有这棵 legacy 树（`doc.root.meta['legacy_roots']`）
   - codegen 通过 `to_legacy_layers(doc)` 原样取出 legacy 喂旧 HTMLGenerator

这套做法保证了 **P3 → P4 → P5 每一步都可 diff 回归**。

## 当前状态

```
PSD
 │
 ├─ (A) LayerExporter → legacy dict 树（完整、带所有历史字段）
 │                        │
 │                        ▼
 └─ (B) 包装 → IR Document（语义字段 + meta['legacy'] = 原 dict）

codegen:
  to_legacy_layers(doc) → 返回 meta['legacy_roots']（如果存在） → 旧 generator 处理

未来演进:
  1. 把 legacy 里的字段逐个提升到 IR 一等字段
  2. 修改 generator 改用 IR 字段
  3. 确认所有 target 都不再读 legacy
  4. 删掉 meta['legacy']
```

## 当前 IR 上的一等字段

已在 IR 上的（codegen 可直接读）：

- `Document`: `width / height / root / assets`
- `_NodeBase`: `id / name / style.bbox / style.opacity / style.visible / effects / meta`
- `GroupNode`: `children / merged_asset`
- `ImageNode`: `asset`
- `TextNode`: `text / runs`
- `Style`: `border_radius_px / background_color / font`

尚未全面提升，依赖 `meta['legacy']` 的（举例）：

- Blend mode
- 合并组的详细子图层清单
- z 索引计算

> 当你要用某个字段时，先看 IR 有没有；没有再去 legacy 取，**并在 PR 里记录**
> 以便后续计划提升。

## 契约规则

1. **IR 是 core → targets 的唯一合法数据流**。targets 不得直接 import psd-tools。
2. **legacy dict 是过渡期的"零损失快照"**，允许读，**不推荐作为新特性的依赖**。
3. **新增字段必须向后兼容**：pydantic 字段给默认值，旧解析器 / 旧 target 不受影响。
4. **字段校验**要严格：例如 `BBox` 校验 `right >= left`、`Color` 通道范围。
   校验失败说明上游有 bug，不要为了通过而降级校验。
5. **删字段/改语义要走"弃用期"**：先加新字段并迁移所有 target，再用两个版本的发布周期后删除旧字段。

## 如何提升一个字段

以把 blend_mode 从 legacy 提升为例：

1. 在 `core/ir/styles.py` 或 `nodes.py` 加字段 `blend_mode: Optional[str] = None`。
2. 在 `core/psd/parser.py` 构造 IR 时填充此字段。
3. 在 codegen 的 renderer（如 `group_renderer.py`）改读 `node.style.blend_mode or layer['legacy']['blend_mode']`
   （暂时双读，保证回退兼容）。
4. 跑 baseline diff，零差异。
5. 下个迭代：把 renderer 改成只读 IR 字段。

## 未来输入源（Figma / XD / Sketch）

如果未来引入，只需：
- 新增 `core/figma/parser.py`，产出同样的 `Document` IR。
- 所有 targets 自动受益，无需改动。

这就是 IR 契约的价值所在。
