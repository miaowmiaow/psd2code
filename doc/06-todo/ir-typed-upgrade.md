# P2-2: IR Typed 升级计划

> 状态：**待实施** | 优先级：P2 | 创建时间：2026-05-30

---

## 1. 背景

当前 IR 本质上是一个**结构包装器**，真正的数据流仍走 legacy 通道：

```
PSD → legacy dict 树 → parser 包装为 IR（核心数据塞入 meta["legacy"]）
                                         ↓
                            HtmlCodegenStage: to_legacy_layers() → 取回原始 dict → HTMLGenerator
```

IR 中已定义但**从未填充**的 typed 字段：

| 字段 | 定义位置 | 当前状态 |
|------|----------|----------|
| `Style.font: FontStyle` | `ir/styles.py` | 始终 None |
| `Style.background_color: Color` | `ir/styles.py` | 始终 None |
| `Style.border_radius_px` | `ir/styles.py` | 始终 None |
| `Style.z_index` | `ir/styles.py` | 始终 None |
| `TextNode.runs: List[dict]` | `ir/nodes.py` | 始终 `[]` |
| `EffectSpec` 系列 | `ir/effects.py` | 始终 `[]` |

所有丰富数据藏在 `node.meta["legacy"]` 中，下游通过 `to_legacy_layers()` 取回原始 dict 消费。

---

## 2. 目标

将 `text_style`/`runs`/`z_index`/`blend_mode` 等字段从 `meta['legacy']` 逃生舱提升为 IR 的一等公民字段，最终移除 `to_legacy_layers()` 兼容层。

---

## 3. 现状数据流

### 写入端（Parser）

| 文件 | 行为 |
|------|------|
| `scripts/core/psd/parser.py:86` | `meta = {"legacy": d}` — 整个原始 dict 挂载到节点 |
| `scripts/core/psd/parser.py:162` | `meta["legacy_roots"] = legacy_tree` — 完整 legacy 树存根节点 |

### 适配层

| 文件 | 行为 |
|------|------|
| `scripts/core/ir/adapters.py:16` | `to_legacy_layers(doc)` — 优先返回 `meta["legacy_roots"]`，否则逐节点合成 |
| `scripts/core/ir/adapters.py:33` | `_legacy_from_node(node)` — 从 `meta["legacy"]` 取回原始 dict |

### 消费端

| 文件 | 消费内容 |
|------|----------|
| `targets/html/pipeline.py:131` | `to_legacy_layers(ctx.ir)` — HTML 生成入口 |
| `targets/html/codegen/renderers/text_renderer.py` | `layer['text_style']` → font_size/color/align/leading |
| `targets/html/codegen/html_builder.py:124-127` | `_text_style_css()` 兼容包装 |
| `targets/react/pipeline.py` | `to_legacy_layers()` |
| `targets/vue/pipeline.py` | `to_legacy_layers()` |
| `layout_optimizer` 全链路 | 直接操作 legacy dict 的 bbox/children 等 |

---

## 4. 分阶段实施方案

### Phase 1：填充 typed 字段（纯增量，零破坏）

**改动范围**：

| 文件 | 改动 |
|------|------|
| `scripts/core/psd/parser.py` | `_node_from_legacy()` 中提取 `text_style` → `Style.font: FontStyle` |
| `scripts/core/psd/parser.py` | 提取 `z_index` → `Style.z_index` |
| `scripts/core/ir/styles.py` | 确认 `FontStyle` 字段覆盖 font_size/color/text_align/leading |

**原则**：
- `meta["legacy"]` 继续保留，不删除
- 下游消费端**不动**，仍走 `to_legacy_layers()`
- IR 变为自描述，新 target 可直接消费 typed 字段

**预计改动**：~80 行 | 风险：🟢 低

---

### Phase 2：下游逐步切换读取源（逐个替换）

**改动范围**：

| 文件 | 改动 |
|------|------|
| `text_renderer.py` | 优先读 `Style.font`，fallback 到 `layer['text_style']` |
| `html_builder.py` | 同上 |
| 各 Renderer | 逐一迁移到 IR typed 字段 |
| `layout_optimizer` | 涉及的 bbox/opacity 已用 IR；其余逐步迁移 |

**原则**：
- 每个 Renderer 独立替换、独立验证
- 保留 fallback 路径，确保向后兼容
- React/Vue pipeline 同步迁移

**预计改动**：~200 行 | 风险：🟡 中

---

### Phase 3：移除 escape hatch（最终清理）

**改动范围**：

| 文件 | 改动 |
|------|------|
| `scripts/core/ir/adapters.py` | 删除 `to_legacy_layers()` / `_legacy_from_node()` |
| `scripts/core/psd/parser.py` | 移除 `meta = {"legacy": d}` 和 `meta["legacy_roots"]` |
| `targets/*/pipeline.py` | 移除 `to_legacy_layers()` 调用 |
| 测试 | ~20 个测试需适配 |

**前提条件**：
- 确认所有下游不再读 `meta["legacy"]`
- 全量回归测试通过

**预计改动**：~-150 行（净删除）| 风险：🔴 高

---

## 5. 风险矩阵

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| 回归面大（HTMLGenerator + Renderer + optimizer 都消费 legacy dict） | 🔴 高 | 分阶段渐进，Phase 1/2 保留 fallback |
| React/Vue pipeline 需同步迁移 | 🟡 中 | Phase 2 统一处理 |
| `layout_optimizer` 深度依赖 legacy dict 结构 | 🟡 中 | 最后迁移，可独立为 Phase 2.5 |
| 测试覆盖 | 🟢 良好 | 1174 测试 + adapter 专项测试保障回归 |

---

## 6. 涉及的 `text_style` 字段映射

```
legacy dict                    →    IR FontStyle
─────────────────────────────────────────────────
text_style.font_size: float    →    FontStyle.size_px: float
text_style.color: str          →    FontStyle.color: Color
text_style.text_align: str     →    FontStyle.text_align: str
text_style.leading: float      →    FontStyle.line_height_px: Optional[float]
```

---

## 7. 验收标准

- [ ] **Phase 1**: `Style.font` 在 TextNode 上有值；现有测试全部通过；`to_legacy_layers()` 仍可用
- [ ] **Phase 2**: `TextRenderer` 直接读 IR typed 字段；移除对 `layer['text_style']` 的直接依赖
- [ ] **Phase 3**: `to_legacy_layers()` 被删除；`meta["legacy"]` 不再写入；全量测试通过

---

## 8. 依赖与前置

- [x] P1-2: 删除废弃 `converter.py` ✅
- [x] P2-3: 拆分 `dom_restructure.py` ✅
- [ ] 确认 `FontStyle` dataclass 字段完整覆盖 `text_style` 所有属性
- [ ] 确认 React/Vue pipeline 的 legacy 消费模式与 HTML 一致
