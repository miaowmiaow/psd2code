# 给协作 AI 的上手 Checklist

> 这份文档是 **专门写给未来的协作 AI** 的，让它接手项目后能快速进入状态，
> 而不会"重新发明"或破坏现有约束。

---

## 必读文件（按顺序）

1. [`../README.md`](../README.md)（doc 首页）—— 了解项目定位与文档地图
2. [`../01-architecture/overview.md`](../01-architecture/overview.md) —— 理解分层模型
3. [`../01-architecture/data-flow.md`](../01-architecture/data-flow.md) —— 理解数据从 PSD 到 HTML
4. [`./known-pitfalls.md`](./known-pitfalls.md) —— **跳过这个你一定会踩坑**
5. [`../03-topics/ir-contract.md`](../03-topics/ir-contract.md) —— 理解"为什么有双轨"
6. [`./testing-and-validation.md`](./testing-and-validation.md) —— 知道如何证明改动没坏

> 以上六份读完，你就具备"不搞破坏"的基本能力。

## 工作法（对 AI 特别重要）

### 1. 改任何东西前先做 baseline 快照

```bash
cp -r .codebuddy/skills/psd2code/output /tmp/before
```

改完后：

```bash
diff -rq /tmp/before .codebuddy/skills/psd2code/output
# 期望：零输出
```

如果有输出：解释每一条差异是**故意的**还是**回归**。回归必须修。

### 2. 先读后改

- 修改一个文件前，先用 `read_file` 读它的当前内容（不要只靠 grep 猜结构）。
- 使用 `replace_in_file` 时，`old_str` 要精确到连空格都一致。
- 不能假设自己的记忆；文件可能已被其它进程改过。

### 3. 尊重"硬约束"

[`known-pitfalls.md`](./known-pitfalls.md) 的每条都是"踩过坑才写下来的"。
不要觉得"这条看起来可以重构" —— 历史已经证明不能。

**典型反例：**
- "子组用手动渲染看起来更统一" → 会引入 75px 的多余描边
- "把 `_merge_group_as_image` 拆成小函数更整洁" → 会引入像素级回归
- "把 `__init__.py` 全删了好干净" → 会破坏 target 注册

### 4. 不要引入"看起来更好"的重构

除非用户明确要求，否则：
- 不改变现有文件布局
- 不改变现有类/方法签名
- 不替换依赖（如"用 attrs 代替 dataclass"）

**可以做** 的改动：
- 添加新功能时，遵循现有模式（Pipeline + Stage + Handler + Strategy）
- 修 bug 时，把修复范围控制到必要的最小文件
- 补 docstring / 注释

### 5. 文档优先

遇到"不知道怎么办"的问题，先搜 `doc/`：

```bash
grep -rni "关键词" .codebuddy/skills/psd2code/doc/
```

如果没有 → 代码里有答案（尤其看 `known-pitfalls.md` 引用的文件）。
如果代码也没有 → 这可能是新场景，需要问用户。

### 6. 完成后更新文档

任何改动满足以下条件，**必须**同步改文档：
- 新增 Stage / Target / Handler / Renderer
- 新增硬约束
- 新增 / 修改 IR 字段
- 新增 `ctx.artifacts` key

不同步 = 留给下一个 AI 的坑。

## 速查：常见任务去哪里

| 任务 | 先读 |
| ---- | ---- |
| 新增一个产物目标（Vue/小程序） | [`../04-extending/add-a-target.md`](../04-extending/add-a-target.md)（含「模式 A」「模式 B」选择） |
| 改 React JSX 生成规则 | [`../02-modules/targets-react.md`](../02-modules/targets-react.md) + [`./known-pitfalls.md`](./known-pitfalls.md) 第 13/14/15 条 |
| 给 HTML 加后处理 Stage | [`../04-extending/add-a-stage.md`](../04-extending/add-a-stage.md) |
| 新增一种图层导出策略 | [`../04-extending/add-a-layer-handler.md`](../04-extending/add-a-layer-handler.md) |
| 新增一种 PSD 效果 | [`../04-extending/add-an-effect.md`](../04-extending/add-an-effect.md) |
| 改变布局优化规则 | [`../03-topics/layout-optimizer.md`](../03-topics/layout-optimizer.md) |
| 改变组渲染行为 | [`../03-topics/group-rendering.md`](../03-topics/group-rendering.md) + [`./known-pitfalls.md`](./known-pitfalls.md) 第 1/2/3 条 |
| 新增 IR 字段 | [`../03-topics/ir-contract.md`](../03-topics/ir-contract.md) + [`./known-pitfalls.md`](./known-pitfalls.md) 第 10 条 |

## 最后：问自己三个问题

在 commit 之前：

1. **我做了什么改动？** 能用一句话说清。
2. **它会影响哪些场景？** 列一个清单。
3. **我怎么证明它不破坏现有场景？** 给出 baseline diff 结果或明确的"预期差异"说明。

三个都答得上来，提交；任何一个答不上来，**停下来**再读一次相关文档。
