# 扩展：新增一个 Stage

> 例：在 HTML target 里加一个 `AccessibilityStage`，给 `<img>` 自动补 `alt`。

## 套路

1. 写一个 `Stage` 子类，`run(ctx) -> ctx`。
2. 在对应 target 的 `pipeline.py` 把它插入 stage 列表。
3. 约定好读/写的 `ctx.artifacts` key。
4. baseline diff 零差异（仅新增 class 属性时需要重建 baseline）。

## 示例

### 1. Stage 实现

在 `scripts/targets/html/postprocess/` 新增 `accessibility.py`：

```python
from pathlib import Path
from framework import PipelineContext, Stage
from bs4 import BeautifulSoup


class AccessibilityStage(Stage):
    name = "accessibility"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        html_path = ctx.get("html_path")
        if not html_path:
            ctx.log("accessibility: skipped (no html_path)")
            return ctx

        html_path = Path(html_path)
        soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")

        fixed = 0
        for img in soup.find_all("img"):
            if not img.get("alt"):
                name = img.get("data-name") or img.get("class", [""])[0] or "image"
                img["alt"] = name
                fixed += 1

        html_path.write_text(str(soup), encoding="utf-8")
        ctx.set("a11y_fixed", fixed)
        ctx.log(f"accessibility: added alt to {fixed} imgs")
        return ctx
```

### 2. 插入到 pipeline

编辑 `scripts/targets/html/pipeline.py`：

```python
from targets.html.postprocess.accessibility import AccessibilityStage

def build_html_pipeline(ctx: PipelineContext) -> Pipeline:
    return Pipeline([
        LoadPsdStage(),
        ParseToIrStage(),
        HtmlCodegenStage(),
        LayoutOptimizeStage(),
        AccessibilityStage(),           # ←★ 新增
    ])
```

### 3. 更新 artifact key 表

在 [`../02-modules/framework.md`](../02-modules/framework.md) 的 key 表里加：

| key | 写入者 | 读者 | 含义 |
| --- | ------ | ---- | ---- |
| `a11y_fixed` | AccessibilityStage | （调试） | 自动补 alt 的 img 数量 |

## 插入时机决策

| 位置 | 典型用途 |
| ---- | -------- |
| Load 之后、Parse 之前 | 预处理 PSD（如过滤图层） |
| Parse 之后、Codegen 之前 | IR 加工（如挂元数据、合并重复） |
| Codegen 之后、LayoutOpt 之前 | 拿到裸 HTML/CSS 做早期修饰 |
| LayoutOpt 之后 | 基于最终 DOM 的后处理（a11y、压缩、注入脚本） |

## Checklist

- [ ] `run(ctx)` 幂等
- [ ] 前置条件不满足时优雅跳过（`ctx.log("skipped ...")` + `return ctx`）
- [ ] 异常策略：
    - 核心数据流 Stage：让异常上抛（`LoadPsdStage` / `ParseToIrStage` 这种）
    - 附加优化 Stage：try/except 不阻断主链（`LayoutOptimizeStage` / `AccessibilityStage` 这种）
- [ ] 插入顺序正确
- [ ] 新写入的 artifact key 补充到 `02-modules/framework.md`
- [ ] baseline diff 通过；如改变输出，重建 baseline 并在 PR 说明
