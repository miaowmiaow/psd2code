# 项目中用到的设计模式

> **本文解决什么**：把代码里用到的每个设计模式落到具体文件，方便照着"抄作业"新增功能。
> **不讨论什么**：设计模式的通用教材式定义。

---

## 速查表

| 模式 | 位置 | 作用 |
| ---- | ---- | ---- |
| Pipeline / Chain of Stages | `framework/pipeline.py` + `targets/html/pipeline.py` | 顺序执行可替换的 Stage |
| Observer | `framework/hooks.py` (`PipelineHook`, `LoggingHook`) | 监听 Stage 生命周期而不改 Pipeline |
| Registry | `targets/registry.py` | `@register("html")` 自注册 target |
| Template Method | `targets/base.Target.run()` → `build_pipeline()` | 父类定义流程、子类只装配 stages |
| Chain of Responsibility | `core/extract/handlers.py` | 图层导出决策链（背景跳过 → 按钮组合并 → ...） |
| Strategy + Registry | `targets/html/codegen/renderers/*` | 节点渲染按 kind 分派（Group / Image / Text） |
| Facade | `core/render/effects/effects_renderer.py` 的 `render_layer_with_effects()` | 对外暴露统一入口，内部调用多个效果渲染器 |
| Composition | `targets/html/codegen/html_generator.HTMLGenerator` | `CodegenContext + LayerRenderer + HtmlBuilder` |
| Adapter | `core/ir/adapters.to_legacy_layers()` | IR → legacy dict 兼容旧 codegen |
| Discriminated Union | `core/ir/nodes.Node` | pydantic `Field(discriminator="kind")` |

---

## 1. Pipeline / Stage

```python
# framework/stage.py
class Stage(ABC):
    name: str = ""
    @abstractmethod
    def run(self, ctx: PipelineContext) -> PipelineContext: ...

# framework/pipeline.py
class Pipeline:
    def run(self, ctx):
        for stage in self._stages:
            ctx = stage.run(ctx)
        return ctx
```

- **插入点**：任何需要"一串可替换步骤"的场景。
- **约束**：Stage 必须是 **幂等** 的（重复运行不会坏事）；只读写 `ctx` 已声明字段。

## 2. Observer (Hook)

```python
# framework/hooks.py
class PipelineHook(ABC):
    def on_pipeline_start(self, ctx): ...
    def on_stage_start(self, stage, ctx): ...
    def on_stage_end(self, stage, ctx, elapsed_ms): ...
    def on_error(self, stage, ctx, err): ...
```

- 使用：`ctx.hook = LoggingHook(verbose=True)`，无须改 Pipeline 源码。
- 典型用途：打日志、上报耗时、写 trace 文件。

## 3. Registry + 自注册 Target

```python
# targets/registry.py
_REGISTRY: dict[str, Type[Target]] = {}

def register(name: str):
    def _wrap(cls):
        _REGISTRY[name.lower()] = cls
        return cls
    return _wrap

# targets/html/target.py
@register("html")
class HtmlTarget(Target): ...
```

- **入口如何发现 target**：`psd_to_code.py` `import targets.html` 触发装饰器 → registry 填充。
- **扩展方法**：见 [`../04-extending/add-a-target.md`](../04-extending/add-a-target.md)

## 4. Template Method：Target

```python
# targets/base.py
class Target(ABC):
    @abstractmethod
    def build_pipeline(self, ctx) -> Pipeline: ...

    def run(self, ctx) -> PipelineContext:
        return self.build_pipeline(ctx).run(ctx)
```

- 父类 `run()` 固定了"装配→执行"骨架；子类只实现 `build_pipeline`。
- `HtmlTarget.build_pipeline = build_html_pipeline`。

## 5. Chain of Responsibility：Layer Handler

```python
# core/extract/handlers.py
class LayerHandler(ABC):
    def can_handle(self, ctx: HandlerContext) -> bool: ...
    def handle(self, ctx: HandlerContext) -> HandlerResult: ...

# run_handlers(handlers, ctx) 按注册顺序轮询
```

职责链条（按注册顺序，5 个 Handler）：
1. `BackgroundSkipHandler` —— 已合并背景图层跳过
2. `ClippingGroupHandler` —— 剪切蒙版组
3. `InvisibleLayerHandler` —— 隐藏 / opacity=0 跳过
4. `GroupHandler` —— 组：先尝试合并为单图，失败则递归
5. `LeafLayerHandler` —— 叶图层（图片 / 文本 / shape）

- **扩展方法**：见 [`../04-extending/add-a-layer-handler.md`](../04-extending/add-a-layer-handler.md)
- **约束**：Handler **无状态**。所有副作用走 `LayerExporter.*` 方法（`_z_counter`、
  `exported_count`、打印等）。

## 6. Strategy + Registry：HTML 节点渲染

```python
# targets/html/codegen/renderers/base.py
class NodeRenderer(ABC):
    def can_render(self, layer) -> bool: ...
    def render(self, layer) -> str: ...

# targets/html/codegen/renderers/__init__.py 注册
REGISTRY = [ImageRenderer(...), TextRenderer(...), GroupRenderer(...)]
```

- LayerRenderer 遍历节点时按 `can_render` 顺序选择策略。
- 新增节点类型（例如 `VideoNode`）：加一个渲染器即可。

## 7. Facade：效果渲染入口

```python
# core/render/effects/effects_renderer.py
def render_layer_with_effects(layer):
    """对外唯一入口。内部按顺序调用 stroke / shadow / glow / overlay 渲染器。"""
```

- 上层（`LayerExporter`）只用这一个函数，不关心内部有多少个效果渲染器。
- 新增效果渲染器：在 Facade 里把它串进去即可。见
  [`../04-extending/add-an-effect.md`](../04-extending/add-an-effect.md)。

## 8. Composition：HTMLGenerator

```python
# targets/html/codegen/html_generator.py
class HTMLGenerator:
    def __init__(self, ...):
        self.ctx = CodegenContext(...)
        self._layer_renderer = LayerRenderer(self.ctx)
        self._html_builder = HtmlBuilder(self.ctx)
```

- 不用多继承 / mixin；每个协作者是独立组件。
- 共享状态集中在 `CodegenContext`。

## 9. Adapter：IR → legacy

```python
# core/ir/adapters.py
def to_legacy_layers(doc: Document) -> list[dict]:
    """把强类型 IR 还原成旧 codegen 吃的 list[dict]。"""
```

过渡期双轨策略的关键胶水。详见 [`../03-topics/ir-contract.md`](../03-topics/ir-contract.md)。

## 10. Discriminated Union：IR Node

```python
# core/ir/nodes.py
Node = Annotated[
    Union[GroupNode, ImageNode, TextNode, ShapeNode],
    Field(discriminator="kind"),
]
```

- pydantic 依据 `kind` 字段自动选择正确的子模型。
- 遍历代码 `isinstance(n, GroupNode)` 天然类型安全。

---

## 模式之间如何协作

```
Target (Template Method)
  └─ build_pipeline() 装配 Pipeline
       └─ Pipeline (Chain of Stages)
            ├─ Stage 通过 ctx 传递数据
            ├─ ctx.hook 接收 Observer 通知
            └─ 某个 Stage (HtmlCodegenStage) 内部:
                  HTMLGenerator (Composition)
                    └─ LayerRenderer
                         └─ RendererRegistry (Strategy)
                              └─ 分派给 ImageRenderer / TextRenderer / GroupRenderer

并行的另一条决策链（发生在 ParseToIrStage 内）:
  LayerExporter
    └─ run_handlers([...]) (Chain of Responsibility)
         ├─ BackgroundSkipHandler
         ├─ ClippingGroupHandler
         ├─ InvisibleLayerHandler
         ├─ GroupHandler
         └─ LeafLayerHandler
```

## 延伸阅读

- [`../04-extending/`](../04-extending/) 系列文档都以"照着哪个模式抄"为目的编写。
