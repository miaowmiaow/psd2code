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
# pyright / mypy （可选）
# 至少保证无编辑器 lint 红波浪

# 如果项目有 ruff / black 规则：
ruff check .codebuddy/skills/psd2code/scripts
```

提交前确保 **全项目 lint 零错误**（包括 type 检查与风格检查）。

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
