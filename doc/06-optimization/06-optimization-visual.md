# 三大优化方向 - 可视化对比

## 优化方向 1️⃣：layer_exporter.py 拆分

### 当前架构（113 KB 怪兽文件）
```
layer_exporter.py (113 KB)
│
├─ LightEffectLayerInfo 数据类
├─ LayerExporter 主类
│  ├─ export_from_psd()          ┐
│  ├─ _export_group()            │  混在一起
│  ├─ _export_single_layer()     │  难以维护
│  ├─ _render_with_light_effect()├─ 方法查找困难
│  ├─ _composite_light_layers()  │  单测困难
│  ├─ _merge_group_as_image()    │  版本迭代困难
│  ├─ _write_async()             │
│  └─ ... 50+ 个方法              ┘
│
└─ 缓存、工具函数等
```

### 目标架构（四个 25-35 KB 的模块）
```
layer_exporter_core.py (30 KB)
├─ LayerExporter 主类
├─ export_from_psd()
├─ _export_group()
├─ _export_single_layer()
├─ BBox 计算等基础
└─ 与其他模块的清晰接口

    ↓ 通过接口调用

layer_exporter_light_effects.py (28 KB)
├─ LightEffectLayerInfo
├─ LightEffectHandler 类
├─ _render_with_light_penetration()
├─ Phase 1/2/3 判定
└─ 光效缓存管理

    ↓

layer_exporter_caching.py (20 KB)
├─ CacheManager 统一接口
├─ LightEffectCache
├─ EffectRenderCache
└─ PropertyCache

    ↓

layer_exporter_async.py (25 KB)
├─ AsyncWriter (with retry)
├─ ObjectPool
└─ 异步 IO 管理
```

### 改变对比
```
┌──────────────────────────────────────────────────────────────┐
│                       改变对比矩阵                             │
├─────────────────┬─────────────┬──────────────┬──────────────┤
│   方面          │    当前      │    目标      │    收益      │
├─────────────────┼─────────────┼──────────────┼──────────────┤
│ 文件体积        │ 113 KB      │ 35 KB (max)  │ ⬇️ 69%      │
│ 方法集中度      │ 50+ 方法    │ 10-15 方法   │ ⬇️ 70%      │
│ 类的职责数      │ 2-3 个      │ 1 个         │ 单一职责     │
│ 导入复杂度      │ 1 个 import │ 4-5 个       │ 更清晰       │
│ 单元测试难度    │ 困难(全加载) │ 易(单个模块) │ ⬇️ 3-5 倍   │
│ IDE 查找速度    │ 慢(扫整文件)│ 快(定位模块) │ ⬆️ 5-10 倍  │
└─────────────────┴─────────────┴──────────────┴──────────────┘
```

### 调用流程变化
```
【当前】直接调用，难以追踪
━━━━━━━━━━━━━━━━━━━━━━━━━
from layer_exporter import LayerExporter

exporter = LayerExporter(psd_path)
result = exporter.export_from_psd()  ← 方法在哪里？搜索整个文件...
                                       ← 涉及哪些缓存？翻 800 行代码...
                                       ← 异步怎么工作？又翻 300 行...

【目标】清晰的依赖树
━━━━━━━━━━━━━━━━━━━━━━━━━
from layer_exporter_core import LayerExporter
from layer_exporter_light_effects import LightEffectHandler
from layer_exporter_caching import CacheManager

exporter = LayerExporter(psd_path)
  ├─ 这里创建 LightEffectHandler
  ├─ 这里创建 CacheManager
  └─ 这里创建 AsyncWriter

result = exporter.export_from_psd()  ← 知道它在 core 模块
                                      ← 光效由 LightEffectHandler 处理
                                      ← 缓存由 CacheManager 处理
```

---

## 优化方向 2️⃣：DOMRestructure Mixin → 组合

### 当前架构（继承链混乱）
```
           【当前 MRO】
              ↓
      DOMRestructure
            ↙   ↓  ↘  ↙   ↘
    ┌──────────────────────────┐
    │ BackgroundMixin          │
    │ TallDecorMixin           │
    │ ClusteringMixin          │
    │ RenderingMixin           │
    │ ReclassifyMixin          │
    └──────────────────────────┘
            ↓
         object

【问题】
- DOMRestructure.method_x 在哪个 Mixin？ → 按 MRO 逐一搜索
- 两个 Mixin 的方法重名了怎么办？ → 隐式覆盖，容易踩坑
- 想单独测试 BackgroundMixin？ → 做不了，需要整个链
- 改 Mixin 的方法签名？ → 需要检查其他 4 个是否兼容
```

### 目标架构（显式组合）
```
           【目标结构】
              ↓
      DOMRestructure
      ├─ background ──→ BackgroundHandler
      ├─ tall_decor ──→ TallDecorHandler  
      ├─ clustering ──→ ClusteringHandler
      ├─ rendering  ──→ RenderingHandler
      └─ reclassify ──→ ReclassifyHandler
            ↓
       DOMHandler (base)
            ↓
         object

【优势】
- DOMRestructure.background.method_x → 一目了然
- 各 Handler 完全独立 → 可单独测试
- 接口清晰 → IDE autocomplete 更好
- 新增 Handler 只需继承 base → 扩展性强
```

### 代码对比

#### 【当前】方法查找成本高
```python
class DOMRestructure(
    BackgroundMixin,
    TallDecorMixin,
    ClusteringMixin,
    RenderingMixin,
    ReclassifyMixin,
):
    def restructure_dom(self):
        for group in self._collect_all_groups():
            self._extract_background_leaves(group)  # 在哪个 Mixin？
            self._extract_tall_decor(group)          # 搜索 MRO...
            self._cluster_and_build_tree(group)      # ...
            self._render_group(group)                # ...
            self._upgrade_stack_to_col(group)        # ...

# IDE 提示
self._ex⁇⁇⁇  # 不知道有什么方法...
```

#### 【目标】方法调用清晰
```python
class DOMRestructure:
    def __init__(self, ...):
        self.background = BackgroundHandler(self)
        self.tall_decor = TallDecorHandler(self)
        self.clustering = ClusteringHandler(self)
        self.rendering = RenderingHandler(self)
        self.reclassify = ReclassifyHandler(self)
    
    def restructure_dom(self):
        for group in self._collect_all_groups():
            self.background.extract_leaves(group)     # 明确
            self.tall_decor.extract(group)            # 明确
            self.clustering.cluster_and_build(group)  # 明确
            self.rendering.render(group)              # 明确
            self.reclassify.upgrade(group)            # 明确

# IDE 提示
self.background.⁇⁇⁇  # ✅ 知道有 extract_leaves、get_stats 等
self.clustering.⁇⁇⁇  # ✅ 知道有 cluster_and_build、get_clusters 等
```

### 改变对比
```
┌──────────────────────────────────────────────────────────────┐
│                       改变对比矩阵                             │
├─────────────────┬─────────────┬──────────────┬──────────────┤
│   方面          │    当前      │    目标      │    收益      │
├─────────────────┼─────────────┼──────────────┼──────────────┤
│ 继承深度        │ 6 层        │ 2 层         │ ⬇️ 66%      │
│ MRO 复杂度      │ 高(5 Mixin) │ 低(单继承)   │ ⬇️ 80%      │
│ 方法查找成本    │ O(n)        │ O(1)         │ ⬆️ 5-10 倍  │
│ 单元测试隔离    │ 困难        │ 易           │ ⬇️ 70%      │
│ IDE 智能提示    │ 差(混乱)     │ 优(清晰)     │ ⬆️ 显著     │
│ 接口文档化      │ 无          │ 清晰         │ 新增契约     │
│ 新增 Handler    │ 困难        │ 易           │ ⬆️ 扩展性   │
└─────────────────┴─────────────┴──────────────┴──────────────┘
```

### 调用流程对比

```
【当前】方法在哪里？
━━━━━━━━━━━━━━━━━━
group_restructure = DOMRestructure(...)
group_restructure._extract_background_leaves(group)
                  ↓ 搜索顺序
                  ├─ BackgroundMixin._extract_background_leaves? ✓
                  │  （找到，行 156 处）
                  ├─ 但这个方法用到 _xxx_helper，又在哪个 Mixin？
                  ├─ 继续搜索...
                  └─ 3 个 Mixin 后才找全

【目标】方法在哪里？
━━━━━━━━━━━━━━━━━━
group_restructure = DOMRestructure(...)
group_restructure.background.extract_leaves(group)
                  ↓ 直接找到
                  └─ handlers/background_handler.py 第 XX 行
                     清晰明了！
```

---

## 优化方向 3️⃣：性能基准 CI

### 当前流程（手工、无自动检测）
```
【Dev 本地】
━━━━━━━━━━━━━━━
开发功能 → (hope) 性能不变 → git push PR

【CI】
━━━━━━━━━━━━━━━
❌ 无性能检测
✅ 通过单元测试
✅ 通过代码审查

【Merge】
━━━━━━━━━━━━━━━
合并到 main

【部署后发现】
━━━━━━━━━━━━━━━
❗ 用户反馈：变慢了 20%
😱 追查发现：是某个 PR 做了无谓优化
😤 成本：诊断 2 天 + 回滚 + 重新优化
```

### 目标流程（自动检测、秒级反馈）
```
【Dev 本地】
━━━━━━━━━━━━━━━
开发功能 → git push PR

【CI 自动检测】
━━━━━━━━━━━━━━━
✅ 单元测试
✅ 代码审查
🔴 性能基准
   ├─ 跑 benchmark 测试
   ├─ 对比 main 分支基线
   ├─ 下降 > 5% → ❌ CI 失败
   ├─ 下降 < 5% → ✅ 通过
   └─ 提升? → 📊 记录新基线

【即时反馈】
━━━━━━━━━━━━━━━
❌ 性能下降评论：
   "⚠️ 性能回归检测
    - full_pipeline: 28.5s → 32.1s (+12.6%)
    请确认这是有意的。"

【Dev 修复】
━━━━━━━━━━━━━━━
✅ 修改代码
✅ 再次 push
✅ CI 自动重测
✅ 性能恢复 → merge
```

### 性能指标对比
```
┌─────────────────────────────────────────────────────────────────┐
│                   性能基准检测的收益                              │
├──────────────────────┬──────────┬──────────┬──────────────────┤
│ 方面                 │  当前    │  目标    │  收益             │
├──────────────────────┼──────────┼──────────┼──────────────────┤
│ 性能回归检测         │ ❌ 无    │ ✅ 自动  │ 100% 覆盖        │
│ 问题发现时间         │ 晚       │ 秒级     │ ⬇️ 从天级到秒级   │
│ MTTR(修复时间)       │ 2-3 天   │ 1 小时   │ ⬇️ 90%           │
│ 性能数据可追踪       │ 中等     │ 完全     │ commit 级追踪     │
│ PR 审查效率          │ 低       │ 高       │ ⬆️ 50%           │
│ 维护者信心           │ 低       │ 高       │ ⬆️ 70 分         │
│ 成本(infrastructure) │ 0        │ 小       │ CI 时间 +5 min   │
└──────────────────────┴──────────┴──────────┴──────────────────┘
```

### 检测时间轴
```
PR 提交
│
├─ 0:00 ← PR 发起
│
├─ 2:00 ← lint 检查
├─ 3:00 ← 单元测试 (100+ 用例)
├─ 4:00 ← 性能基准 ✨ 新增
│        ├─ warmup 1 次: 29s
│        ├─ run 3 次: 28.8s, 28.6s, 28.9s
│        └─ 对比基线 (28.5s ± 5%)
│           ✓ PASS
│
├─ 5:00 ← 代码审查 (等待人类审查)
│
└─ merge ✅

【对比】无性能检测的流程
━━━━━━━━━━━━━━━━━━━━━
PR 提交
├─ 2:00 ← lint
├─ 3:00 ← 单元测试
├─ 5:00 ← merge ❌ 未检测性能
│
部署到生产
│
❗ 用户反馈性能变慢
│
后续追查...
```

### 基准数据管理
```
【第一次生成基线】
━━━━━━━━━━━━━━━━━━
稳定版本 main 分支
    ↓
pytest tests/benchmarks/ --benchmark-json=baseline.json
    ↓
保存到 .github/benchmarks/baseline.json
    ↓
baseline.json:
{
  "benchmarks": [
    {
      "name": "test_full_pipeline_time",
      "mean": 28.5,        # 平均耗时
      "stddev": 1.2,       # 标准差
      "tolerance": 0.05,   # ±5% 容差
      "unit": "seconds"
    },
    ...
  ]
}

【之后每个 PR】
━━━━━━━━━━━━━━━━━━
feature 分支
    ↓
pytest --benchmark-json=current.json
    ↓
对比：current vs baseline
    ├─ 若 mean(current) > mean(baseline) * 1.05
    │  → ❌ CI 失败 + 评论
    ├─ 若 mean(current) < mean(baseline) * 0.95
    │  → ✅ 通过 + 记录改进
    └─ 若 -5% < change < +5%
       → ✅ 通过 + 无评论

【性能改进时】
━━━━━━━━━━━━━━━━━━
若新基线确认：
    → 更新 baseline.json
    → commit: "perf: update benchmark baseline to 25.3s"
    → 下一个 PR 基于新基线检测
```

---

## 📊 总体改进效果估算

### 投入 vs 产出
```
┌────────────────────────────────────────────────────────────┐
│  三大优化对 psd2code 项目的改进（量化估算）                  │
├─────────────┬────────┬─────────────┬──────────────────────┤
│ 优化方向    │ 投入   │ 直接收益    │ 长期收益             │
├─────────────┼────────┼─────────────┼──────────────────────┤
│1️⃣ 拆分      │ 2-3 天 │ 可读性⬆️70% │ 维护成本⬇️40%/年    │
│  文件拆分   │        │ 单测⬆️30%   │ 新人上手快 3 倍     │
│             │        │ 代码查找⬆️5×│ 新增 target 快 30%  │
├─────────────┼────────┼─────────────┼──────────────────────┤
│2️⃣ 组合      │ 1-2 天 │ 理解成本⬇️60%│ 缺陷⬇️30%/年        │
│  改造       │        │ 单测⬇️70%   │ 重构风险⬇️50%       │
│             │        │ 方法查找⬆️5×│ 扩展性⬆️30%         │
├─────────────┼────────┼─────────────┼──────────────────────┤
│3️⃣ 性能 CI   │ 1 天   │ 回归⬇️100% │ MTTR⬇️90%           │
│  自动检测   │        │ 问题发现快9×│ 性能稳定性⬆️70%     │
│             │        │ 审查快⬆️50% │ 信心度⬆️70 分       │
├─────────────┼────────┼─────────────┼──────────────────────┤
│   总计      │ 6 天   │ 多维度提升  │ 年节省 ~100-150 小时│
└─────────────┴────────┴─────────────┴──────────────────────┘
```

### 时间价值计算
```
【年度节省】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
维护时间节省：
  - 代码查找时间: 每次 5min → 1min (减 80%)
  - 新人理解: 从 2 周 → 3 天 (快 5 倍)
  - 单元测试编写: 快 70%
  - BUG 诊断: 快 50%
  ≈ 12-15 工作日 / 年

性能诊断时间：
  - 问题发现: 秒级 (vs 天级)
  - 性能回归修复: 平均从 2 天 → 1 小时
  ≈ 10-12 工作日 / 年 (假设每月 1-2 次性能问题)

总计：每年节省 22-27 工作日 ≈ $55,000-67,500 (按 $200-250/小时)
投入成本：6 人天 ≈ $12,000
ROI：4.6-5.6 倍
```

---

## 🎯 风险评估

### 改动影响范围
```
【低风险】
━━━━━━━━━━━
1️⃣ 文件拆分：
  - 纯重构，逻辑 100% 相同
  - baseline diff 应显示无差异
  - 回滚成本：低（可瞬间回滚）

2️⃣ 组合改造：
  - 纯重构，调用方式改变但结果相同
  - baseline diff 应显示无差异
  - 回滚成本：低

【零风险】
━━━━━━━━━━━
3️⃣ 性能 CI：
  - 新增功能，不改动转换逻辑
  - 只添加检测，不改动产出
  - 回滚成本：零（删除工作流文件）
```

### 前置条件检查
```
✅ 已有充分的单元测试 (1,260+ 用例)
✅ 已有 baseline 稳定版本
✅ 已有文档完整度高 (32 个 .md)
✅ 代码没有紧急 bug 需要修复
✅ 团队有时间做重构 (6 天 sprint)
```

---

## ✨ 最终建议

```
优先级排序：
┌────────────────────────────────────────┐
│ 1️⃣ 优先做 P0 两个优化（layer + mixin）  │
│    ├─ 最紧急的技术债
│    ├─ 直接降低维护成本
│    └─ 新人上手快
│
│ 2️⃣ 再做 P1 性能 CI（下一个冲刺）       │
│    ├─ 依赖于代码稳定
│    ├─ 保护已有的成果
│    └─ 建立长期质量保证
│
│ 3️⃣ 后续优化（P2，未来 3 月）          │
│    ├─ 类型标注完善
│    ├─ ADR 体系
│    └─ 高阶抽象
└────────────────────────────────────────┘

预期总耗时：
第 6 周：P0 两个优化 (3-4 天)
第 7 周：P1 性能 CI (1-2 天)
       + 集成测试 + 稳定期 (3-4 天)
```

---

**下一步**：打开 `doc/06-optimization-roadmap.md` 查看详细实施步骤。
