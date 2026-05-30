# 空 PASS_THROUGH 组白色污染问题

## 问题描述

在独立 cluster 合成时，如果 cluster 中包含一个**递归为空的 PASS_THROUGH 组**（即组内所有子层展开后没有任何可见像素），且该组 `opacity < 255`，psd-tools 的 `composite()` 会产生白色污染，导致导出的 PNG 整体偏白/偏亮。

### 典型案例

抽奖活动页面 PSD 中的"吧台"组：

```
吧台 (PASS_THROUGH)
  ├─ 组56 (PT, op=255)
  ├─ 图层138 (NORMAL, op=255)
  ├─ 组59 (PT, op=255)          ← 罪魁祸首
  │    ├─ 组52 (PT, op=153, ★无子层★)
  │    └─ 图层531 (NORMAL, 空)
  └─ 图层42 (MULTIPLY, op=255)
```

导出结果：`batai-6b8b2b.png` 亮度 164.2，而 PSD 预合成图对应区域为 128.8，偏差 +35.4。

---

## 根因分析

### psd-tools composite 机制

在 psd-tools 的 `_apply_passthrough_source`（`psd_tools/composite/composite.py`）中：

1. 空组的 `shape_g = 0`（没有任何子层贡献 alpha）
2. 计算 `color_support = clip(divide(C * mask, shape_g))`
   - `divide(x, 0)` → 极大值 → `clip()` 截断到 **1.0（白色）**
3. 最终颜色混合：`self._color = C * (1 - weight) + weight * 1.0`
   - 其中 `weight = 1 - opacity/255`（如 opacity=153 时 weight≈0.4）
   - 即：原始颜色 × 0.6 + **白色** × 0.4 → 整体偏白

### 为什么全图合成没有问题？

psd-tools 的全图 `composite()` 结果与 Photoshop 预合成图**完全一致**（diff=0.00）。这不是 psd-tools 的 bug——在全图中，白色污染被上方遮盖层（如人物组）中和。

问题出现在**独立 cluster 合成**时：cluster 只包含吧台组的成员，没有上方遮盖层，白色污染直接暴露。

### 数据对比

| 场景 | 亮度 |
|------|------|
| PSD 全图吧台区域 | 128.8 |
| 吧台组独立 composite（含组59） | 166.1 |
| 吧台组独立 composite（无组59） | 107.6 |
| 导出的 batai-6b8b2b.png（修复前） | 164.2 |
| 导出的 batai-524ee3.png（修复后） | 123.6 |

---

## 修复方案

### 方案选择

| 方案 | 策略 | 评价 |
|------|------|------|
| A | `_merge_cluster_layers_as_image` 中 composite() 前临时隐藏空 PT 组 | 渲染层打补丁，治标不治本 |
| **B（采用）** | `detect_compose_clusters` 成员选择时排除递归为空的组 | **源头过滤，干净正确** |
| C | 同 A 的变体 | 同上 |

**选择 B 的理由**：

1. 递归为空的组对最终像素**没有任何正向贡献**——它在全图中被遮盖所以不可见，独立导出时它不应该存在
2. cluster 的成员本来就应该只包含"有视觉贡献"的层
3. 方案 B 把问题消灭在数据准备阶段，而非渲染阶段打补丁
4. 逻辑清晰：**如果一个组递归展开后没有任何可见像素内容，它就不应该成为 cluster 成员**

### 实现

**文件**：`scripts/core/extract/compose_cluster.py`

#### 1. 新增辅助函数 `_is_group_recursively_empty()`

```python
def _is_group_recursively_empty(group_layer: Any) -> bool:
    """递归判断一个组是否完全没有可见像素贡献。"""
    try:
        children = list(group_layer)
    except Exception:
        return True

    for c in children:
        if not getattr(c, 'visible', True):
            continue
        if getattr(c, 'opacity', 255) == 0:
            continue
        if _is_adjustment(c):
            continue  # 调整层自身无像素
        if getattr(c, 'is_group', lambda: False)():
            if not _is_group_recursively_empty(c):
                return False
        else:
            return False  # 叶子图层有像素
    return True
```

"递归为空"判定规则：
- 不可见 / opacity=0 → 无贡献，跳过
- 调整层 → 自身无像素（仅修改下方颜色），视为无贡献
- 文本层 / 像素层 → 有贡献（返回 `False`）
- 子组 → 递归检查

#### 2. 在 `detect_compose_clusters()` 子层过滤阶段添加过滤

```python
children: list[Any] = []
try:
    for c in group_layer:
        if not getattr(c, 'visible', True):
            continue
        if getattr(c, 'opacity', 255) == 0:
            continue
        # B方案：排除递归为空的组
        if (getattr(c, 'is_group', lambda: False)()
                and _is_group_recursively_empty(c)):
            continue
        children.append(c)
except Exception:
    return []
```

---

## 验证结果

修复后 `detect_compose_clusters` 对吧台组的输出：

```
修复前 cluster0: [组56, 图层138, 组59, 图层42]   ← 组59 参与合成
修复后 cluster0: [组56, 图层138, 레이어202, 图层42]  ← 组59 被排除
```

导出亮度从 164.2 降到 123.6，与 PSD 全图参考值 128.8 高度吻合。

---

## 关联知识

- **psd-tools composite 源码**：`psd_tools/composite/composite.py` → `_apply_passthrough_source()`
- **cluster 合成入口**：`scripts/core/extract/layer_exporter.py` → `_merge_cluster_layers_as_image()`
- **相关文档**：`doc/03-topics/group-rendering.md`（组合成策略）
