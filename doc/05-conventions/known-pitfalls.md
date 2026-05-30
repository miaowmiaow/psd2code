# 已知硬约束与陷阱（必读）

> 这份清单汇总了历史上踩过的坑，**每一条都有血的教训**。
> 开始任何改动前请通读。如果你发现新坑，**请更新本文件**。

---

## 1. 子组必须用 `composite(viewport=...)` 渲染

**位置：** `core/extract/layer_exporter.py` 的 `_render_subgroup`

**错误做法：** 递归调用 `_render_group_expanded` 手动渲染子组后再裁切。

**后果：** 圆角矩形等带效果的子组会在底部/边缘多出 ~75px/行 的多余描边。
原因是手动渲染无法复现 PS 的"组级效果裁切"行为。

**正确做法：**
```python
sub_img = sub_grp.composite(viewport=grp_bbox)
```

**仅** 在 `composite()` 异常失败时降级为手动渲染。不要因为"看起来更一致"就回退到手动路径。

---

## 2. 组的效果溢出必须用"混合渲染"

**位置：** `core/extract/layer_exporter.py` 的 `_merge_group_as_image` "效果溢出分支"

**错误做法 A：** 只用手动渲染（`_render_group_expanded`）
→ 内部画质比 PS 差（描边偏细、过渡偏硬）。

**错误做法 B：** 只用 `group.composite(viewport=expanded_bbox)`
→ PS 单组 composite **不输出超出组 bbox 的效果像素**；外部溢出为空。

**正确做法：**
```python
canvas = render_group_expanded(group, grp_bbox, expand)   # 手动（保留溢出）
inner  = group_layer.composite(viewport=grp_bbox)         # PS 原生（内部高质量）
canvas[bbox_rect] = inner                                  # 内部覆盖
```

**验证：** 与 PS composite 的 Alpha 差异 max=0, mean=0.00。

详见 [`../03-topics/group-rendering.md`](../03-topics/group-rendering.md)。

---

## 3. 不要重构 `LayerExporter` 的渲染分支

**背景：** `layer_exporter.py` 里 `_export_single_layer` / `_merge_group_as_image`
的渲染分支看起来很长、像是"可以拆出去"。

**教训：** 历史上尝试过，结果引入像素级回归。根本原因是：
- 分支之间有隐式的 bbox/扩展量依赖
- 拆到独立类后难以保持参数一致

**建议：** 想重构时先做 baseline diff 的 CI，做完重构再跑，零差异才算通过。
否则保持现状即可。

---

## 4. `bg_layer_ids` 是 set of `id(layer)`，不是名字

**位置：** `core/extract/handlers.py` `BackgroundSkipHandler`

- `id(...)` 是 Python 对象 id，只在本次运行中有效。
- 不要改成 `layer.name` 之类的持久标识符。
- 也不要 pickle / 跨运行传递。

---

## 5. 文本图层命名冲突

- `SimpleNamer` 给同名图层加序号后缀；同名图层多时可能命名差异较大。
- 设计稿里建议每个 instance 有唯一名字，提升可读性。
- 未来可考虑给 `SimpleNamer` 加"父路径前缀"去重。

---

## 6. 入口禁用了字节码缓存

**位置：** `psd_to_code.py` 顶部 `sys.dont_write_bytecode = True`

- 不要在子模块里手动写入 `__pycache__`。
- 仓库根部和 skill 目录各有一份 `.gitignore` 兜底。
- 如果你的 IDE 仍然生成 `.pyc`，请让 IDE 也遵守 `PYTHONDONTWRITEBYTECODE`。

---

## 7. `__init__.py` 不是垃圾

看上去空空如也的 `__init__.py` 往往承担 **触发装饰器注册** 的职责：

- `targets/html/__init__.py` 会 `from .target import HtmlTarget` 触发 `@register("html")`
- `common/__init__.py` / `framework/__init__.py` 作为 re-export 入口

**不要删除**。新增 target 时必须同样新增 `__init__.py`。

---

## 8. `ImageArrayUtils` 是唯一的图像数组入口

不要在业务代码里再手写这些模式：

| 不要写 | 要用 |
| ------ | ---- |
| `np.array(img, dtype=np.float32) / 255.0` | `ImageArrayUtils.pil_to_float_array(img)` |
| `np.array(mask.convert("L"), ...)/255.0` | `ImageArrayUtils.pil_l_to_float_array(mask)` |
| `(np.clip(arr,0,1)*255).astype(np.uint8)` | `ImageArrayUtils.float_to_uint8_rgba(arr)` |
| `Image.fromarray((np.clip(arr,0,1)*255).astype(np.uint8), 'RGBA')` | `ImageArrayUtils.float_array_to_pil(arr)` |

统一入口为未来的精度/通道/性能优化预留了唯一改动点。

---

## 9. IR 字段提升必须双读过渡

把 `node.meta['legacy']['foo']` 提升为 IR 一等字段 `node.foo` 时：

1. 先加字段并在 parser 填充。
2. 消费端改成 `node.foo or node.meta['legacy']['foo']`（**双读**），跑 baseline。
3. 下个迭代才能改成仅读 `node.foo`。

**不要**一次性切换单读，容易漏掉边缘 case。

---

## 10. LayoutOptimizer 依赖 `data-type` 属性

- 布局优化器是 **纯字符串后处理**，读不到 IR。
- 它通过 `data-type="image|text|group"` 等属性识别语义。
- 如果你改了 codegen 不再输出这些属性 → 优化器会退化，但不会崩。
- 加新语义时，考虑往 DOM 里写 `data-*` 属性供优化器读。

---

## 11. `reset_image_counter` 必须在每次转换开始时调用

**位置：** `common/utils.py` 的 `_image_counter` 是模块级全局变量。

- 由 `ParseToIrStage.run` 负责调用 `reset_image_counter()`。
- 新引入的"前置 Stage"若再次产生图片，也需要自行调用它。
- 不调用的后果：多次运行会产生 `img_1.png` / `img_501.png` 这样的奇怪编号（不会崩，但不美观）。

---

## 12. React target 必须复用 HTML target 的前 4 段 Stage

**位置：** `targets/react/pipeline.py`

**错误做法：** 把 `LoadPsdStage` / `ParseToIrStage` / `HtmlCodegenStage` /
`LayoutOptimizeStage` 复制到 `targets/react/` 再按需修改。

**后果：** HTML target 以后每次升级（新效果、新布局规则、新资产策略），
React target 都要手动同步；一旦漏同步就产生行为差异，而且极难排查。

**正确做法：** 直接 import 复用
```python
from targets.html.pipeline import (
    LoadPsdStage, ParseToIrStage, HtmlCodegenStage, LayoutOptimizeStage,
)
```

**例外：** 若你真的需要在前 4 段里改点什么（罕见），应该把该改动**下沉到 HTML target**
（这样两个 target 共享），而不是在 React target 里 fork 一份。

---

## 13. JSX 文本中 `{` / `}` 的转义必须"单遍替换"

**位置：** `targets/react/codegen/html_to_jsx.py` 的 `_escape_jsx_text`

**错误做法：**
```python
out = text.replace("{", "{'{'}").replace("}", "{'}'}")
```
这会把第一次替换得到的 `{` 又再次替换，得到嵌套爆炸的 `{'{'{'}'}...`。

**正确做法：** 用单遍正则 + 表驱动：
```python
_JSX_TEXT_SUBS = {"{": "{'{'}", "}": "{'}'}", "<": "&lt;", ">": "&gt;"}
_JSX_TEXT_RE = re.compile("|".join(re.escape(k) for k in _JSX_TEXT_SUBS))
def _escape_jsx_text(text):
    return _JSX_TEXT_RE.sub(lambda m: _JSX_TEXT_SUBS[m.group(0)], text)
```

---

## 14. React target 不用 CSS Module

**位置：** `targets/react/codegen/css_to_module.py`（命名是历史残留）

**背景：** HTML target 产出的类名是 BEM 风格，已全局唯一，不需要哈希。
同时 CSS 里大量使用属性选择器（`[class*="__image"]`），在 CSS Module 下不会
匹配哈希后的类名。

**做法：**
- CSS：**不包** `:global`，**不做** 哈希，仅把 `url("images/...")` 改写为
  `url("./assets/images/...")`。
- JSX：`className="foo bar"` 保留字符串形式，不引 `styles`。

**如果未来要加 CSS Module：** 必须同时满足「类名全局唯一」和「不使用属性选择器」
两个前提，且要把 `html_to_jsx` 改成生成 `className={styles['foo']}` 表达式。
在此之前**不要**擅自把 CSS 用 `:global` 包起来，那样会让类名映射失效。

---

## 15. npm 包名净化必须限定 ASCII

**位置：** `targets/react/stages.py` 的 `_sanitize_npm_name`

**错误做法：**
```python
"".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower())
```
Python 的 `str.isalnum()` 对中文字符返回 `True`，导致 PSD 文件名 `南瓜大作战 H53`
被净化为 `南瓜大作战-h53` —— 写入 `package.json` 后 `npm install` 立即报
`Invalid name: "南瓜大作战-h53"`。

**正确做法：** 显式限定 ASCII 小写字母、数字、`-_`：
```python
for ch in name.lower():
    if ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch in "-_":
        buf.append(ch)
    else:
        buf.append("-")
```
另外记得**折叠连续 `-`** 并 `strip("-_")`，否则 `"a__b"` 会得到 `"a--b"`。

---

## 16. CSS `url()` 改写正则必须容忍文件名中的括号

**位置：** `targets/react/codegen/css_to_module.py` 的 `_URL_IMG_RE`

**错误做法：**
```python
_URL_IMG_RE = re.compile(r'url\((["\']?)images/([^\)"\']+)\1\)')
```
否定字符组 `[^\)"\']+` 把 `)` 当成终止符，可以处理无引号的 `url(images/x.png)`，
但**带引号场景**下，如果文件名自身含 `)`（例如 PSD 图层 `image (1)` 导出的
`image_(1)_24.png`），正则会在文件名里的第一个 `)` 就停下，整体匹配失败，
`App.css` 里这条 `url("images/image_(1)_24.png")` 将保持原样、不会被改写到
`./assets/images/…`。Vite 解析时因路径不存在而拿不到资源，**页面上对应的
`background-image` 元素直接空掉**（比如"第一行的宝箱"彻底消失）。

**正确做法：** 分「双引号 / 单引号 / 无引号」三条分支，前两种分支内容里允许
`)`：
```python
_URL_IMG_RE = re.compile(
    r'''url\(\s*(?:"images/(?P<dq>[^"]+)"|'images/(?P<sq>[^']+)'|images/(?P<nq>[^)"'\s]+))\s*\)'''
)
```
替换函数里根据哪个命名组命中，复原对应的引号风格即可。

**自检用例（必测）：**
- `url("images/image_(1)_24.png")` —— 带双引号 + 文件名含 `)`
- `url(images/x.png)` —— 无引号
- `url('images/y.png')` —— 单引号
- `url("./assets/images/already.png")` —— 已改写，不应再动

---

## 17. 每个 target 必须落入独立子目录（项目根 vs target 产物根）

**约定：** 所有 target 的产物都必须放在
`<base>/<psd_stem>/<target_name>/`，项目根
`<base>/<psd_stem>/` 作为多 target 共享的**父目录**，绝不允许某个 target 把自己
的产物散在项目根下。

**为什么：** 之前 HTML target 把产物直接散在 `<psd_stem>/` 根下，React target
只能用"先占用根，再把 react 塞到 `react/` 子目录"的妥协方式，导致：
- 两个 target 同时存在时目录混乱（HTML 的 `index.html` 与 React 的 `index.html` 谁是谁）；
- 单跑 `--target html` 再跑 `--target react` 时，React 管线里的 `LoadPsdStage`
  会 `rmtree(<psd_stem>/)`，把用户先前的 html 产物连根拔起；
- 新增 target（Vue / SSR / …）都要重复发明子目录方案。

**实现要点（`scripts/targets/html/pipeline.py: LoadPsdStage`）：**
```python
class LoadPsdStage(Stage):
    def __init__(self, subdir_name: str | None = None) -> None:
        self._subdir_name = subdir_name  # 允许显式覆盖（见下面 React 场景）

    def run(self, ctx):
        base = Path(ctx.output_dir) if ctx.output_dir else Path(Config.OUTPUT_BASE_DIR)
        project_root = base / ctx.psd_path.stem
        subdir = self._subdir_name or ctx.target_name or "out"
        out = project_root / subdir
        if out.exists(): shutil.rmtree(out)   # 只清自己这一块
        out.mkdir(parents=True, exist_ok=True)
        ctx.project_root = project_root       # 兄弟 target 共享的父目录
        ctx.output_dir = out                  # 本 target 的产物根
        ...
```

**PipelineContext 必须同时暴露两个字段：**
- `project_root` — 跨 target 共享的父目录（如 `output/<psd>/`）
- `output_dir` — 当前 target 的产物根（如 `output/<psd>/html/`）

下游 stage 需要**跨 target 取路径时**（比如 React 要在 `<project_root>/react/`
里写 JSX），应取 `ctx.project_root / "react"`，**不要**拼 `output_dir.parent`
—— 那只是巧合成立的偶合依赖。

**复用中间产物的特殊情形：** 若某 target（如 React）的前几段实际在生成另一种
target 的中间产物（如 React 前 4 段生成 HTML），应把 `LoadPsdStage` 构造成
`LoadPsdStage(subdir_name="html")`，让中间产物落到 `html/`、真正的 target 产物
落到 `react/`，两边都对得上"文件夹名 = 产物类型"的直觉。

**新增 target 时的检查清单：**
1. `pipeline.py` 第一个 stage 直接用 `LoadPsdStage()`（不要再写子类）；
2. 真正写入 target 最终产物的 stage 里，目标目录用 `ctx.project_root /
   "<target_name>"` 或直接用 `ctx.output_dir`（两者应相等）；
3. 如果前若干段复用了别的 target 的中间产物，用 `LoadPsdStage(subdir_name=
   "<intermediate>")` 把那段中间产物放到命名清晰的子目录里；
4. 运行前后手动跑一遍 "先 A target 再 B target"，确认 A 的产物目录**不会**被 B
   的 `LoadPsdStage` 清空。

---

## 18. 类名是"多类字符串"——语义类必须放首位

**约定：** `SimpleNamer.generate_class_name()` 返回**空格分隔**的多类串，格式：

```
"<semantic>[-<siblingIndex>]__<idSuffix> <role>"
```

- `<semantic>`：从图层名抽取的语义词（如 `btn-receive`、`title-sub`、`bg`）。
  来自 `common.semantic.extract_semantic_token` —— **与图片文件名共享词表**。
- `<siblingIndex>`：同名兄弟去重序号（首个省略、后续 `-2/-3/...`）。
- `<idSuffix>`：layer.id 剥掉 `layer-`/`group-` 前缀后的数字，保证全局唯一。
- `<role>`：`layer-group`（组节点）或 `layer`（非组叶子），保留给 layout_optimizer
  的子串匹配用（`'layer-group' in class`）。

示例：

```html
<!-- 组节点，按钮语义，id=27 -->
<div class="btn-receive__27 layer-group" data-type="group">
  <!-- 子元素：同级第 3 个 prop -->
  <div class="prop-3__34 layer">...</div>
</div>
```

**为什么是多类？** 角色类 `layer-group` / `layer` 给 layout_optimizer 提供稳定的
识别锚点（历史遗留依赖，详见 #7 约定），但**不能**放在 CSS 选择器里使用；真正
用来写样式的必须是语义类（全局唯一的 `btn-receive__27`）。

**所有 renderer 生成 CSS 时必须取首个 token 作选择器：**

```python
css_class = class_name.split()[0]    # "btn-receive__27"
css = f".{css_class} {{
..."        # 绝不要 f".{class_name}"，会变后代选择器
```

`dom_restructure.py` 的 `css_class = f".{child_class.split()[0]}"` 和上面行为
一致，可以直接相互配合。

**新增 target 或 renderer 时的检查：**

1. 拿到 `class_name` 后**先 split**，再拼 CSS 选择器；
2. HTML 的 `class="..."` 属性**直接塞** `class_name`（多类空格分隔合法）；
3. 需要识别"这个 div 是 group 吗"的场景，**优先用** `data-type="group"`
   （从 GroupRenderer 起就加了这个属性，比依赖类名更干净）；
4. **不要**直接解析图层名拼类名——所有语义抽取**必须**走
   `semantic.NameResolver.resolve_token`（或 `get_default_resolver()`），
   否则 CSS 类和图片文件名的语义会分裂（同一图层在 CSS 里叫 `btn`、图片里叫
   `anniu`）。

**扩词入口：** `common/cn_dict.json`（Layer 1 词典），按业务域分组追加即可，
长 pattern 自动优先匹配（无需排序）。**不再扩** `common/semantic.py::_KEYWORDS`
（仅保留作 Fallback 兜底）。

## 19. 语义命名调试用 `_naming_report.md`

每次转换都会在 `<output>/_naming_report.md` 写出每个图层的命名链路：

| layer_id | raw_name | ltype | token | source |
| --- | --- | --- | --- | --- |
| `group-67` | 立即领取按钮 | group | `btn-receive` | layer1 |
| `group-376` | 组 11 | group | `bg-section` | layer2 |
| `layer-100` | 节日氛围图 | image | `jieri` | fallback |
| `group-101` | 组 8 | group | `group` | none |

**调试场景：**

1. CSS 类名不符合预期 → grep 该 layer_id，看是 layer1/layer2/fallback 中
   哪一层给的 token，再去对应模块改规则或加词条
2. `none` 占比过高（> 40%）→ PSD 大量用 PS 默认名，可考虑给设计师反馈或在
   `cn_dict.json` 加更多关键词
3. `layer2` 异常多（> 5%）→ Layer 2 阈值可能太松（典型如 R3 把内容容器误判
   `bg-section`），调 `Layer2RoleInferer` 顶部常量

**注意 cache 隔离：** `NameResolver` cache key 含 `has_dom_context: bool`
维度。原因：`make_image_filename`（无 dom）会先于 `SimpleNamer`（有 dom）
被调用，如不分槽位，Layer 2 永远抢不到 cache → 失效。新增"无 dom"的调用点
不需要做特殊处理；新增"带 dom"的调用点必须传 `dom_context=DomContext(...)`，
不能传空 dict 之类的占位。

详见：[`../02-modules/semantic.md`](../02-modules/semantic.md)。

---

## 20. flex_applier 必须保留 v-stack / v-list / v-row / v-col wrapper 的 `position`

**位置：** `targets/html/postprocess/layout_optimizer/transformers/flex_applier.py`
的 `_apply_vertical_layout` / `_apply_horizontal_layout`

**背景：** dom_restructure 会把"高瘦跨行装饰组"或"伪多行装饰堆叠"升级为
`v-stack` wrapper（CSS 写 `position: relative`），让其内部 absolute 子节点用它作
containing block。`SiblingGroupDetector` 同理产出 `v-list` wrapper。

**错误做法：** flex_applier 在父容器 flex 化时**无差别**写 `del child_css['position']`，
把 wrapper 的 `position: relative` 删了。

**后果：** wrapper 内部所有 absolute 子节点（如 `icon-refresh`、`v-col-N`）跳过
wrapper 找下一个 positioned 祖先（往往是 `#canvas`），瞬间飘到屏幕左边缘 / 顶部。
**典型场景：领奖.psd `wenan__93`**——内部 5 条说明文本和 icon 全部漂出。

**正确做法：**
```python
is_stack_wrapper = any(
    cls in (child_info.get('classes') or [])
    for cls in ('v-stack', 'v-list', 'v-row', 'v-col')
)
if 'position' in child_css:
    if is_stack_wrapper:
        child_css['position'] = 'relative'    # 保留 containing block
    else:
        del child_css['position']
elif is_stack_wrapper:
    child_css['position'] = 'relative'
```

**配套要求：** `analyzers/layout_analyzer.py::analyze_children_layout` 必须在
`children_info` 字典里写出**完整的** `classes` 列表（不能只写首个 `class`），否则
flex_applier 拿不到 wrapper 标识。

**同类陷阱：** 任何**新增**的下游 transformer，只要会写 `del css['position']`，
都必须复刻这个 wrapper 例外保护。`dom_restructure._apply_flex_to_existing_container`
内部已自带保护，但**独立** transformer 都得各自加。

---

## 21. dom_restructure 的 N=2 簇必须用"双强信号"判 v-col

**位置：** `targets/html/postprocess/layout_optimizer/transformers/dom_restructure.py`
的 Stack→Col 反向升级 pass

**背景：** N=2 的子节点簇，单纯按"Y 间距小"判 v-col 会把"水平并排两元素"误升级为
v-col；单纯按"X 重叠"判会把"上下贴边但 X 偏移大的两层装饰"误升级。

**正确做法：** N=2 时必须**同时**满足两个强信号：

| 阈值 | 含义 | 默认 |
| ---- | ---- | ---- |
| `reclassify_n2_min_x_overlap` | X 投影重叠率 ≥ 阈值 | 0.95 |
| `reclassify_n2_max_gap_px`    | Y 方向间距 ≤ 阈值像素 | 50 |

N ≥ 3 时按通用判据走（X 共线 + Y 间距规整即可），不需要双强信号。

**为什么不放宽：** 历史上把 X overlap 调到 0.7 后立刻误伤大量"装饰图 + 文本块"
组合。0.95 是回归测试得到的安全下限。

---

## 22. 高瘦装饰剥离用"X 投影区间重叠"而非"left/right 对齐"

**位置：** `dom_restructure._extract_tall_decor_leaves` 的 cond4

**错误做法（已踩过的坑）：** 判"被高瘦元素跨过的 leaves 是否同列"时，比较两两
`left` / `right` 是否对齐（绝对差 ≤ tol）。

**后果：** 左对齐 + 右端不齐的多行文本（典型：`btn-exchange r=450` vs
`checkout r=343`），|450-343|=107px 远超 tol，cond4 失败 → 高瘦装饰不被剥离 →
被切行算法吸进同行 → 整组 v-row 排错。

**正确做法：** 改用"X 投影区间重叠"——任意两个 leaves 之间
`overlap_x / min(width) ≥ (1 - tall_decor_x_align_tolerance)`（默认 0.8）即可。
这能正确处理"左对齐 + 右端不齐"的多行文本块。

**4 条 AND 完整列表：** 高度比、aspect 比、跨行数、X 投影重叠。任一条不满足都
不剥离，直接走切行/列。详见 [`../03-topics/layout-optimizer.md`](../03-topics/layout-optimizer.md)。

---

## 23. CSS 数值规范化必须排除 `url(...)` 与标识符内数字

**位置：** `common/css_utils.py` 的 `_URL_RE` / `_NUMBER_RE`

**错误做法（已踩过的坑）：** 用 `_NUMBER_RE = r'-?\d+\.\d+|-?\d+'` 直接全文替换
做精度归一（如 `22.000px → 22px`、`opacity: 1.0 → 1`）。

**后果：** 把标识符里的数字段也吃掉：
- `url("images/bg-f07984.png")` → `url("images/bg-f7984.png")`（前导 0 被归一）
- `images/btn-0b0682.png` → `images/btn0b682.png`（`-0` 被当成数字字面量）

→ Vite 加载图片 404，页面对应区域**整片消失**。

**正确做法：**
1. **先抠 url(...)**：用 `_URL_RE = re.compile(r'url\(\s*(?:"[^"]*"|\'[^\']*\'|[^)]*)\s*\)')`
   把所有 url 块替换成占位符 `\x00URL{N}\x00`，做完数字归一再还原。
2. **数字加边界**：`_NUMBER_RE` 必须前置 lookbehind 排除 `[A-Za-z0-9_\-.]`、后置
   lookahead 排除 `[A-Za-z0-9_]`，并在数字后强制要求**单位组**（px/em/rem/%/vh/vw/...）。

**自检用例（必测）：**
- `url("images/bg-f07984.png")` —— 文件名前导 0、`-0` 都不能动
- `var(--color-1)` / `rgba(255, 0, 0, 0.5)` / `calc(100% - 10px)` —— 不破坏
- `font-size: 22.000px` → `22px`、`opacity: 1.0` → `1`、`line-height: 1.500em` → `1.5em` —— 仍归一

---

## 24. CSS 全局 header 解析必须先剥前置注释

**位置：** `common/css_utils.py` 的 `_iter_top_level_blocks` /
`_strip_leading_comments`

**错误做法（已踩过的坑）：** 旧版 `_iter_top_level_blocks` 按 `{` 切分时把"块前
注释"（如 `/* 图层样式 */`）粘到 selector 头部，得到
`"/* 图层样式 */\n.bg__1"`，导致 `parse_css_to_dict` / `extract_global_css_header`
把第一条 `.class` 规则误判为非 class 块、整段塞进全局头。

**后果：** CssPretty 渲染时全局段重复出现 `.bg__1` 等图层规则；DOM 序段反而
缺第一条规则；浏览器视觉表现可能正常（CSS 重复定义后者覆盖前者），但 grep /
diff 体验崩坏。

**正确做法：** 在按 `{` 切前先调 `_strip_leading_comments(selector)` 剥掉前置
`/* ... */`，再判断是否是 class/id 块。

---

## 25. LayoutOptimizer 不要再加 overflow / border-radius pass

**位置：** `targets/html/codegen/renderers/group_renderer.py`

**背景：** 历史上 `dom_restructure._fix_overflow_after_restructure` 在 DOM 重构
后扫描三类容器（图片容器 / 名字含"圆角"/"按钮"）补 `overflow:hidden` /
`border-radius`。**已彻底删除**，迁移到源头 `group_renderer` 的 emit 阶段。

**为什么迁移：** 后处理 pass 依赖名字关键词（"圆角"/"按钮"）极易漏判，且会跟
其他 transformer 抢顺序；source-of-truth 应当在 codegen 知道每个 group 的
原始几何 / 子图层信息时直接写 CSS。

**绝不要：** 在 LayoutOptimizer 任何位置再加同名 pass，那会让 overflow 决策来源
变多导致回归不稳。如发现现有 group_renderer 漏补某种容器，**应当在 group_renderer
里增强**，而不是在 postprocess 兜底。

---

## 26. psd-tools `draw_stroke_effect` 对全填满形状产出错误 mask

**位置：** `core/render/adjustments_patch.py` 的 `_patched_draw_stroke_effect`

**背景：** psd-tools 原生的 `draw_stroke_effect`（`psd_tools/composite/effects.py`）
在对**全填满的形状层**（shape 数组全为 1.0）生成描边 mask 时存在 bug。

**根因链条：**
1. 矩形/形状图层的 vector mask 全为 1（完全填充）
2. `filters.scharr(shape[:,:,0])` 边缘检测返回全 0（无变化）
3. `utils.divide(0 - 0, 0 - 0)` → 产生 NaN
4. psd-tools 将 NaN 替换为 1.0 → mask 100% 覆盖
5. 描边颜色（如绿色）**完全覆盖**填充颜色（如淡黄色）

**典型症状：** 形状层有内描边（FrFX, InsetFrame）时，composite 输出的颜色
是描边颜色而非填充颜色。例如：黄色矩形 + 2px 绿色内描边 → 导出全绿。

**修复方案：** monkey-patch `draw_stroke_effect`，在 scharr 结果全 0 时
直接返回空 mask（全 0），跳过后续的归一化逻辑：

```python
edges = filters.scharr(shape[:, :, 0])
edge_max = np.max(edges)
if edge_max < 1e-7:
    # shape 完全均匀，没有边缘可以描边
    mask = np.zeros((height, width, 1), dtype=np.float32)
    return color, mask
```

**Monkey-patch 注入方式（坑）：** 直接 `eff_mod.draw_stroke_effect = patched`
**不生效**，因为 `Compositor._apply_stroke_effect` 已经通过导入时的
`__globals__` 绑定了原始函数引用。**必须**同时修改方法的 `__globals__` 字典：

```python
from psd_tools.composite.composite import Compositor
Compositor._apply_stroke_effect.__globals__[
    'draw_stroke_effect'
] = _patched_draw_stroke_effect
```

**测试：** `tests/test_adjustments_patch.py::TestPatchedDrawStrokeEffect`
覆盖了 shape 全 1、全 0、有边缘三种场景。

---

## 27. 形状层自渲染（`_render_shape_base_from_fill`）仅在有描边效果时启用

**位置：** `core/render/effects/effects_renderer.py` 的 `_render_shape_base_from_fill`

**背景：** 该函数用 SoCo 填充色 + origination 几何（Rectangle / RoundedRectangle /
Ellipse）自行合成 shape 图层的基础图，**绕开** psd-tools `composite()` 的描边 bug。

**错误做法（已踩过的坑）：** 对所有有 SoCo 填充的 shape 图层无条件自渲染。

**后果：** 对于**没有描边效果**的圆角矩形，origination 中存储的是 "Live Shape"
参数（`keyOriginRRectRadii`），但 psd-tools `composite()` 使用的是图层存储的
实际 Bézier 路径——两者不一定一致。典型案例：

- 领奖.psd "圆角矩形 1"（38×38px）：origination radii = TL10/TR19/BL19/BR19
- psd-tools 存储的路径是 6 knots 的近似圆形
- 旧代码对四角半径取平均值 `(10+19+19+19)/4 ≈ 17` → 在 38px 图形上接近圆形
- 实际 PS 渲染用的是存储路径（近似圆角矩形），composite() 能正确还原

自渲染**仅**在图层有 stroke effect 时才比 composite() 更准确（因为 composite 的
描边 bug 会把填充色覆盖掉，见 #26）。

**正确做法：** 在函数开头检测是否存在启用的 Stroke 效果，若无则立即返回 None：

```python
has_stroke_effect = False
effects = getattr(layer, 'effects', None)
if effects:
    for eff in effects:
        if type(eff).__name__ == 'Stroke' and getattr(eff, 'enabled', False):
            has_stroke_effect = True
            break
if not has_stroke_effect:
    return None  # 让 composite() 正确渲染 vector path
```

**决策矩阵：**

| 场景 | 策略 | 原因 |
| ---- | ---- | ---- |
| shape + 有 stroke effect | 自渲染 | composite 描边 bug（#26） |
| shape + 无 stroke effect | composite() | vector path 更准确 |
| topil() 不为 None | 直接用 topil() | 最快、最准 |

**附带改进：** 自渲染路径中，圆角矩形改为支持四角独立半径
（`_draw_rounded_rect_variable`），不再取平均值。实现 CSS border-radius 规范的
缩放规则：相邻角半径之和超过边长时按比例缩小。
