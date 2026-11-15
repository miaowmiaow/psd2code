# 测试与验证

> **本文解决什么**：回答"我改了代码，怎么证明没坏？"

---

## 核心方法：Baseline Diff

psd2code 的输出是大量文件（HTML + CSS + JS + metadata + 一堆 PNG）。
肉眼看 diff 不现实。策略是 **整棵产物目录字节对比**：

```bash
# 1. 改动前：先跑一次产出 baseline
cp -r .codebuddy/skills/psd2code/output /tmp/psd2code-baseline

# 2. 做你的改动

# 3. 重新跑
python3 .codebuddy/skills/psd2code/psd_to_code.py sample.psd

# 4. 整树比对
diff -rq /tmp/psd2code-baseline .codebuddy/skills/psd2code/output
# 期望：无任何输出（零差异）
```

## 双 baseline

推荐同时和 **两个** baseline 比对：

1. **自己改动前的快照**（上面流程）
2. **历史 `psd2html` skill 的输出**（验证"与历史版本保持兼容"）

两者都 diff -rq 零输出 → 你的改动是"零回归"的。

## 哪些情况可以接受差异

| 场景 | 允许差异 | 做法 |
| ---- | -------- | ---- |
| 故意重命名 class / 文件名 | ✓ | 在 PR 里说明并重建 baseline |
| 修 bug（原先就错） | ✓ | 准备 before/after 截图证明现在对 |
| 新增效果 / 新字段 | ✓（输出变更） | 既有 PSD 不含新效果时应保持零差异 |
| 像素有任何变化 | **✗** 默认不接受 | 若无明确理由，属于回归，需修复 |

## 多个 sample

- 选至少 3 个代表性 PSD：
  - 一个普通场景
  - 一个带大量效果（外描边 / 投影 / 发光）
  - 一个带嵌套组 / 剪切蒙版 / 文字混排
- 所有 sample 都 diff 零差异，才能视作"通过"。

## Lint

```bash
ruff check .          # 配置见 pyproject.toml [tool.ruff]
```

CI 会拦截 lint 错误。当前规则集是刻意收敛的「真问题」子集：

| 规则 | 覆盖内容 |
| ---- | -------- |
| `E4` / `E7` / `E9` | import 位置、语句结构、语法类错误 |
| `F` | 未使用导入/变量、未定义名、f-string 占位符 |

风格类规则（行宽 / import 排序 / 命名）暂未开启 —— 代码里仍有大量历史风格，
一次性开启会让门禁长期变红。要引入请先整体格式化，再单独提交。

## 自动化门禁（CI）

`.github/workflows/ci.yml` 在 Python 3.10 / 3.11 / 3.12 上依次执行：

1. `python -m compileall -q scripts psd_to_code.py` —— 语法检查
2. `ruff check .` —— lint
3. `pytest -q` —— 全量单测 + e2e

### e2e 冒烟 + 产物快照

`tests/test_e2e_smoke.py` 用 `tests/psd_factory.py` **现场生成**一份最小合法
PSD（40×30，1 组 3 层），跑完整 CLI（含 React / Vue target），断言：

- 产物清单、图片数量、语义类名（中文图层名 → `bg` / `icon` / `card`）；
- `index_optimized.html` 已剥离 dev metadata；
- `style_optimized.css?v=<md5>` 与最终 CSS 内容一致（缓存版本号回归）；
- 产物与 `tests/snapshots/e2e_minimal/` 的归一化快照一致；
- `_layout_stats.json` 的 `failures` 为空（否则优化器某步崩了也不会有人知道）；
- `style_optimized.css` 不含无效的 `scale(calc(...))`，缩放逻辑在 `main.js: fitCanvas()`；
- `metadata.json` / `config.__version__` / `codegen.version` 三处产物格式版本一致；
- `--strict`、`--css-style expanded`、`--no-css-pretty`、`--no-smart-merge` 四个变体各跑一遍。

改动了 codegen / 优化器的**预期行为**时，需要同步更新快照：

```bash
P2C_UPDATE_SNAPSHOTS=1 pytest tests/test_e2e_smoke.py
git diff tests/snapshots/     # 逐行 review，确认是"预期变化"而不是回归
```

快照里的图片名做了归一化（`-<6位哈希>.png` → `-<hash>.png`），因此换
Pillow / zlib 版本不会造成假失败。

## 手动烟测

1. 打开 `output/<stem>/index.html` 和 `index_optimized.html`，目视对比。
2. 浏览器打开，看一下是否正常渲染（至少没有整块白屏）。
3. 看 DevTools Console 有无 JS 错误。

## 常见失败模式与定位

| 症状 | 可能原因 | 下手点 |
| ---- | -------- | ------ |
| 某张图 md5 变了但名字没变 | 渲染算法变了 | 比较 `images/` 下两个文件的 PIL 展示；检查 `core/render/*` |
| 图层位置偏移 | bbox 计算变了 | 检查 `core/extract/image_ops._constrain_bbox_to_canvas` 或扩展 bbox 逻辑 |
| 组渲染多/少了内容 | Handler 决策改变 | 检查 `handlers.py` 顺序与 can_handle |
| 文字变图片 | `TextExtractor.has_transform` 判定不同 | 检查对应方法 |
| Layout 优化后 HTML diff | 优化规则改变 | 先临时禁用 `LayoutOptimizeStage`；再二分定位 transformer |
| IR 校验失败 | pydantic 字段约束没满足 | 看报错信息的 `loc` 与 `msg` |

## 性能回归

暂无严格基准。但如改动"看起来会变慢"（如引入全量扫描），请：

1. 用 `time python3 psd_to_code.py sample.psd` 前后各跑 3 次取中位。
2. 偏差 > 20% 需要调研并在 PR 里说明。
