# 扩展：新增一条图层导出决策（Layer Handler）

> 例：把所有名字叫 `@sprite-*` 的组合并成一张精灵图。

## 背景

`core/extract/handlers.py` 是一条 Chain of Responsibility。
每个 Handler 只负责一条决策："这种图层/组是不是我的菜？是→处理并 `handled=True`"。

## 套路

1. 写一个 `LayerHandler` 子类，实现 `can_handle` + `handle`。
2. 在 `DEFAULT_HANDLERS` 的合适位置插入（顺序重要！）。
3. 复用 `exporter.*` 私有方法，不要重新发明。

## 示例

### 1. Handler 实现

在 `core/extract/handlers.py` 末尾追加：

```python
class SpriteGroupHandler(LayerHandler):
    """名字以 '@sprite-' 开头的组 → 合并为单图。"""

    def can_handle(self, ctx: HandlerContext) -> bool:
        layer = ctx.item
        if isinstance(layer, tuple):
            return False
        return (
            hasattr(layer, "is_group") and layer.is_group()
            and (layer.name or "").startswith("@sprite-")
        )

    def handle(self, ctx: HandlerContext) -> HandlerResult:
        exp = ctx.exporter
        layer = ctx.item
        name = layer.name
        full_name = f"{ctx.parent_name}/{name}" if ctx.parent_name else name

        merged = exp._merge_group_as_single_image(
            layer, name, full_name,
            ctx.depth, ctx.parent_left, ctx.parent_top,
            clip_bbox=ctx.parent_clip_bbox,
        )
        if merged:
            return HandlerResult(produced=[merged], handled=True)

        # 合并失败回退：交给下一个 handler（通常是 GroupHandler 递归）
        return HandlerResult(handled=False)
```

### 2. 插入到链

注意：要在 `GroupHandler` **之前** 插入，否则 `GroupHandler.can_handle` 会先吃掉它。

```python
DEFAULT_HANDLERS = [
    BackgroundSkipHandler(),
    ClippingGroupHandler(),
    InvisibleLayerHandler(),
    SpriteGroupHandler(),          # ←★ 新增：在 GroupHandler 之前
    GroupHandler(),
    LeafLayerHandler(),
]
```

## 核心约束（不要破坏）

1. **Handler 无状态**：不要 `self.counter += 1`；副作用都回到 `exporter`
   （`exporter._z_counter`、`exporter.exported_count` 等）。
2. **名字要能识别**：避免歧义（别起 "CustomHandler" 之类无信息名）。
3. **`handled=True` 表示"本链已处理"**，会终止后续 handler；不要用 True 表示"成功"。
4. **合并失败要允许回退**：`return HandlerResult(handled=False)` 把控制权交给下家。

## Checklist

- [ ] 顺序合理（在会吃掉你的 Handler 之前）
- [ ] `can_handle` 判定明确，无副作用
- [ ] `handle` 复用 `exporter.*`，不新开渲染路径
- [ ] 合并失败走回退，不崩溃
- [ ] baseline diff 通过；如有新 case，为它加一个 sample PSD
- [ ] `02-modules/core-extract.md` 的决策链表格补一行
