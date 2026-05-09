# 代码约定

> 面向"阅读别人代码 + 让自己的代码融入"两个目标。

## 语言与版本

- Python 3.10+（`from __future__ import annotations` 几乎所有文件都加上）
- pydantic 2+（语义参考 `core/ir/*`）
- psd-tools 1.14+
- Pillow 10+
- beautifulsoup4（仅 `targets/html/postprocess` 使用）
- numpy

## Import 顺序

1. 标准库
2. 第三方（`psd_tools` / `PIL` / `numpy` / `pydantic` / `bs4`）
3. 一级内部包：`common`, `config`, `pipeline`, `core`, `targets`
4. 同包相对 import：`from .xxx import ...`

**不要** 在函数体内部做 `import numpy as np`；统一顶层 import。
**例外**：为了打破循环依赖 / 延迟加载重型依赖，才允许函数内 import，需加注释说明。

## 类型注解

- 公开 API **必须** 注解参数类型与返回类型。
- 数据类：`@dataclass` 或 `pydantic.BaseModel`，**不要** 用 plain dict 在模块间传递业务数据。
- 联合类型：用 `Union[...]` 或 `X | None`（`from __future__ import annotations` 下效果一致）。
- 未完全规范的字段：`dict` 可以用，但**必须**在文档里说明 key 结构。

## 日志 / 打印

- 临时调试用 `print(...)` 可以，但正式代码倾向于：
  - 进度/统计：走 `ctx.log(msg)`（仅 `verbose=True` 时打印）
  - 强提示（清理目录、失败回退）：`print(...)` 可以保留
- **不要** 引入标准 `logging` 模块的全局 logger，除非后续统一 logging 改造。
  临时方案未定之前，保持现有风格。

## 图像相关

- PIL↔numpy 转换 **统一** 走 `common.image_utils.ImageArrayUtils`：
  - `pil_to_float_array` / `pil_l_to_float_array`
  - `float_array_to_pil` / `float_to_uint8_rgba`
- bbox 操作 **统一** 走 `BBoxUtils`。
- 不要在业务代码里再手写 `np.array(img, dtype=np.float32)/255.0` 等模式。

## 命名

- 模块/文件：`snake_case.py`
- 类：`CamelCase`
- 私有辅助：`_leading_underscore`
- Stage 子类末尾带 `Stage`（`HtmlCodegenStage`）
- Handler 子类末尾带 `Handler`（`GroupHandler`）
- 渲染器子类末尾带 `Renderer`（`StrokeRenderer`）

## 装饰器

- `@register("html")` / `@register_renderer("image")` 等必须"一进入模块就执行"，
  所以**不要** 放在函数内部或 `if __name__ == "__main__":` 里。
- `__init__.py` 里 re-export 对应 class，以便 import 包时触发装饰器。

## 注释与 docstring

- 每个公开类/函数：1-3 行 docstring 说明"做什么"。
- 复杂算法：在函数内部用 **编号步骤** 注释（`# 1. ...` `# 2. ...`）而不是"解释每行"。
- **不要** 写自然语言描述的解释性废话（"这里我们用 numpy 算一下"）；注释应说"为什么"。

## 异常

- 核心数据流（Load/Parse/Codegen）：**让异常上抛**，由 `Pipeline.run` 统一捕获并通知 hook。
- 附加优化/后处理：可以 try/except 保留原始产物，**必须** 同时打印 `traceback.print_exc()` 帮助排查。

## `__init__.py` 规范

- 不删除；字节码已全局禁用，不会产生 `.pyc`。
- 作 re-export 时用显式 `__all__` 列表。
- 不在 `__init__.py` 写业务逻辑。

## 目录"前端 / 后端"边界

**必须遵守：**

| 规则 | 违反的后果 |
| ---- | ---------- |
| `core/*` 不得 import `targets/*` | 破坏前后端边界，后果是引入循环依赖 |
| `targets/*` 不得直接依赖 psd-tools 对象 | 破坏 IR 契约，意味着"换一个输入源就崩" |
| `pipeline/*` 不得 import 业务模块 | 破坏通用性 |
| `common/*` 不得 import 任何上游模块 | 破坏工具层的独立性 |
