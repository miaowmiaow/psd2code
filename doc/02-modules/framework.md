# 模块：`framework/`

> **本文解决什么**：讲清通用流水线抽象的三类和一个 Hook。
> **不讨论什么**：具体 Stage 做什么（见 `targets-html.md`）。

## 位置

`scripts/framework/`
```
framework/
├── __init__.py
├── context.py     # PipelineContext
├── stage.py       # Stage
├── pipeline.py    # Pipeline
└── hooks.py       # PipelineHook / NullHook / LoggingHook
```

此包 **业务无关**：不 import psd-tools、不 import targets、不 import core。

## PipelineContext

位置：`framework/context.py`

```python
@dataclass
class PipelineContext:
    psd_path: Path
    output_dir: Optional[Path] = None
    target_name: str = "html"
    verbose: bool = False

    psd: Optional[PSDImage] = None        # Stage 1 填充
    ir: Optional[Document] = None         # Stage 2 填充

    artifacts: dict[str, Any] = {}        # 自由空间
    hook: PipelineHook = NullHook()

    def log(self, msg): ...
    def set(self, key, value): ...
    def get(self, key, default=None): ...
```

**使用规范：**

| 字段 | 可写时机 | 读取建议 |
| ---- | -------- | -------- |
| `psd_path` / `output_dir` / `target_name` | 由入口创建 | 任何 Stage 可读 |
| `psd` | Stage 1 `LoadPsdStage` 写 | 其后 Stage 可读 |
| `ir` | Stage 2 `ParseToIrStage` 写 | 其后 Stage 可读 |
| `artifacts[...]` | 任何 Stage `ctx.set(k, v)` | 后续 Stage `ctx.get(k)` |

当前使用的 artifact key：

| key | 写入者 | 读者 | 含义 |
| --- | ------ | ---- | ---- |
| `layer_exporter` | ParseToIrStage | HtmlCodegenStage | 图片导出器实例（拿统计） |
| `legacy_layers`  | ParseToIrStage | (备用) | legacy 字典树 |
| `html_generator` | HtmlCodegenStage | (调试) | HTMLGenerator 实例 |
| `html_path` | HtmlCodegenStage / LayoutOptimizeStage | 入口打印 | 最终产物路径 |
| `layout_stats` | LayoutOptimizeStage | 入口打印 | 优化统计 |

> 新 Stage 写新 key 时请 **补充到上表**。

## Stage

位置：`framework/stage.py`

```python
class Stage(ABC):
    name: str = ""
    def __init__(self, name=None): ...
    @abstractmethod
    def run(self, ctx: PipelineContext) -> PipelineContext: ...
```

**约定：**

1. Stage **幂等**：给相同输入多次 run 不会坏事。
2. Stage **只依赖 ctx 已声明字段**：不碰全局状态（必要时通过 `common.utils.reset_*` 显式重置）。
3. Stage 可以**跳过自己**：检测到前置条件不满足时 `ctx.log("skipped ...")` 后直接返回。
4. Stage **异常上抛**：Pipeline 负责通知 hook 并重抛。
   唯一例外是"失败不应阻断整条链"的后处理（如 LayoutOptimizeStage），可自行 try/except。

## Pipeline

位置：`framework/pipeline.py`

```python
class Pipeline:
    def __init__(self, stages=None): ...
    def add(self, stage): ...
    def extend(self, stages): ...
    def run(self, ctx):
        hook.on_pipeline_start(ctx)
        for stage in self._stages:
            hook.on_stage_start(stage, ctx)
            try:
                ctx = stage.run(ctx)
            except BaseException as err:
                hook.on_error(stage, ctx, err)
                raise
            hook.on_stage_end(stage, ctx, elapsed_ms)
        hook.on_pipeline_end(ctx)
        return ctx
```

- Stage 按列表顺序执行，不支持分支/并行（暂无业务需求）。
- 想要分支：用两个不同的 Target。

## Hook (Observer)

位置：`framework/hooks.py`

```python
class PipelineHook(ABC):
    def on_pipeline_start(self, ctx): ...
    def on_pipeline_end(self, ctx): ...
    def on_stage_start(self, stage, ctx): ...
    def on_stage_end(self, stage, ctx, elapsed_ms): ...
    def on_error(self, stage, ctx, err): ...

class NullHook(PipelineHook): """默认"""
class LoggingHook(PipelineHook): """打印耗时"""
```

**开启日志：** 入口把 `ctx.hook = LoggingHook(verbose=True)` 即可。

**自定义 Hook 示例：**

```python
class JsonTraceHook(PipelineHook):
    def __init__(self, path): self.entries = []; self.path = path
    def on_stage_end(self, stage, ctx, ms):
        self.entries.append({"stage": stage.name, "ms": ms})
    def on_pipeline_end(self, ctx):
        Path(self.path).write_text(json.dumps(self.entries))
```

## 扩展场景

- 插入新 Stage：见 [`../04-extending/add-a-stage.md`](../04-extending/add-a-stage.md)
- 新增 Hook：直接子类化 `PipelineHook`，在入口赋值即可。

## 高级：并行管线

第5周新增 `core/pipeline_parallel.py`，支持多 target 并行导出：

```python
from core.pipeline_parallel import ParallelPipeline

pipeline = ParallelPipeline(
    stages=[parse_stage, html_stage, react_stage, vue_stage],
    enable_parallel=True,
    max_workers=3
)
ctx = PipelineContext(psd_path=...)
result = pipeline.run(ctx)  # 自动按依赖分组，并行执行
```

**特点**：
- 自动依赖分析：从 ctx.artifacts 推断 Stage 间依赖
- 线程安全：RLock 保护共享状态
- 性能预期：+2-3% (HTML + React + Vue 并行)

详见 [Performance-Optimization.md](./Performance-Optimization.md)。
