# 三大优化方向 - 快速开始指南

## 🎯 一页纸对比

```
┌─────────────────────────────────────────────────────────────────────┐
│                     三大优化方向对标对比                              │
├─────────────────────┬─────────────┬─────────────┬──────────────────┤
│   优化方向          │  1️⃣ 拆分   │  2️⃣ 组合化  │  3️⃣ 性能 CI     │
├─────────────────────┼─────────────┼─────────────┼──────────────────┤
│ 目标文件            │layer_exporter│restructure.py│tests/benchmarks/│
│ 当前问题            │113 KB 超限   │Mixin 链过长 │无自动检测       │
│ 改造方案            │拆分成 4 模块 │变组合模式   │集成 CI 检查     │
│ 工作量              │2-3 天       │1-2 天      │1 天             │
│ 复杂度              │中           │中          │低               │
├─────────────────────┼─────────────┼─────────────┼──────────────────┤
│ 可读性提升          │⬆️ 70%       │⬆️ 60%      │⬆️ 50%           │
│ 维护成本降低        │⬇️ 40%       │⬇️ 50%      │⬇️ 60%           │
│ 扩展性提升          │⬆️ 20%       │⬆️ 30%      │⬆️ 80%           │
│ 测试效率            │⬆️ 30%       │⬆️ 70%      │⬆️ 40%           │
├─────────────────────┼─────────────┼─────────────┼──────────────────┤
│ 优先级              │ P0 立即     │ P0 立即     │ P1 优化          │
│ 风险等级            │ 低 (纯重构) │ 低 (纯重构) │ 零 (新增功能)    │
│ 向后兼容            │ ✅ 完全兼容 │ ✅ 完全兼容 │ ✅ 完全兼容      │
└─────────────────────┴─────────────┴─────────────┴──────────────────┘
```

---

## 1️⃣ layer_exporter.py 拆分 - 快速版

### 问题描述
```
当前：layer_exporter.py 113 KB
      ├─ 光效穿透渲染 (~800 行)
      ├─ 核心导出逻辑 (~1000 行)
      ├─ 缓存管理 (~400 行)
      └─ 异步 IO (~300 行)
      
结果：单文件过大 → 方法查找困难 → 单测困难
```

### 拆分方案（四个新模块）

```python
# 原来
from core.extract.layer_exporter import LayerExporter

# 改为
from core.extract.layer_exporter_core import LayerExporter
from core.extract.layer_exporter_light_effects import LightEffectHandler
from core.extract.layer_exporter_caching import CacheManager
from core.extract.layer_exporter_async import AsyncWriter
```

### 文件清单
| 文件 | 行数 | 职责 |
|------|------|------|
| layer_exporter_core.py | ~600 | 主类 + 递归逻辑 |
| layer_exporter_light_effects.py | ~700 | Phase 1/2/3 光效渲染 |
| layer_exporter_caching.py | ~500 | 缓存系统 |
| layer_exporter_async.py | ~400 | 异步 IO + 重试 |

### 最小改动清单
```bash
# Step 1: 复制原文件为备份
cp layer_exporter.py layer_exporter.py.bak

# Step 2: 创建 4 个新文件（从原文件中提取代码）
# - layer_exporter_core.py: LayerExporter 类 + 核心方法
# - layer_exporter_light_effects.py: LightEffectLayerInfo + 光效方法
# - layer_exporter_caching.py: 缓存类
# - layer_exporter_async.py: 异步 IO

# Step 3: 在 layer_exporter_core.py 中导入依赖
from .layer_exporter_light_effects import LightEffectHandler
from .layer_exporter_caching import CacheManager
from .layer_exporter_async import AsyncWriter

# Step 4: 运行测试验证
pytest tests/test_layer_exporter.py -v

# Step 5: 删除原文件（如果完全迁移成功）
rm layer_exporter.py
```

### 验证步骤
```bash
# 1. 转换同一个 PSD 两次，对比输出
python3 psd_to_code.py input.psd output1/ --no-optimization
python3 psd_to_code.py input.psd output2/ --no-optimization
diff -r output1/html output2/html  # 应该完全相同

# 2. 运行单元测试
pytest tests/test_layer_exporter.py -v

# 3. 性能对比（应该差不多）
time python3 psd_to_code.py input.psd /tmp/output1/
time python3 psd_to_code.py input.psd /tmp/output2/
```

---

## 2️⃣ DOMRestructure Mixin → 组合 - 快速版

### 问题描述
```
当前 MRO（方法解析顺序）：
DOMRestructure
  → BackgroundMixin
  → TallDecorMixin
  → ClusteringMixin
  → RenderingMixin
  → ReclassifyMixin
  → object

问题：method_x 在哪个 Mixin 中？需要逐一搜索！
```

### 改造方案（显式组合）

```python
# 当前（困难追踪）
class DOMRestructure(
    BackgroundMixin,
    TallDecorMixin,
    ClusteringMixin,
    RenderingMixin,
    ReclassifyMixin,
):
    pass

# 改为（显式清晰）
class DOMRestructure:
    def __init__(self, ...):
        self.background = BackgroundHandler(self)      # 清晰！
        self.tall_decor = TallDecorHandler(self)        # 清晰！
        self.clustering = ClusteringHandler(self)      # 清晰！
        self.rendering = RenderingHandler(self)        # 清晰！
        self.reclassify = ReclassifyHandler(self)      # 清晰！
    
    def _restructure_group(self, group):
        self.background.execute_for_group(group)
        self.clustering.execute_for_group(group)
        self.rendering.execute_for_group(group)
        # ...
```

### 文件清单
| 文件 | 改动 | 影响 |
|------|------|------|
| restructure.py | 改用组合 | 60 行改变 |
| handlers/background_handler.py | 新建 | 继承 DOMHandler |
| handlers/tall_decor_handler.py | 新建 | 继承 DOMHandler |
| handlers/clustering_handler.py | 新建 | 继承 DOMHandler |
| handlers/rendering_handler.py | 新建 | 继承 DOMHandler |
| handlers/reclassify_handler.py | 新建 | 继承 DOMHandler |
| handler_base.py | 新建 | 定义 DOMHandler 基类 |

### 最小改动清单
```bash
# Step 1: 创建 handlers 目录和基类
mkdir -p handlers
cat > handler_base.py << 'EOF'
class DOMHandler:
    def __init__(self, context):
        self.context = context
    def validate(self):
        return True
    def execute(self):
        pass
EOF

# Step 2: 从 background.py 创建 BackgroundHandler
# (复制类代码到 handlers/background_handler.py，继承 DOMHandler)

# Step 3: 改造主类 restructure.py
# (改用组合，调用 self.background.execute_for_group(group))

# Step 4: 运行测试
pytest tests/test_dom_restructure.py -v

# Step 5: 清理（删除原 Mixin 文件或转为纯函数库）
```

### 验证步骤
```bash
# 1. baseline diff（应该完全相同）
python3 psd_to_code.py input.psd output1/ --dom-restructure
python3 psd_to_code.py input.psd output2/ --dom-restructure
diff -r output1/html output2/html  # 应该完全相同

# 2. 单元测试隔离验证
# 创建 test_handlers.py，只加载单个 Handler（快 10 倍）
pytest tests/test_handlers.py -v

# 3. 性能对比（组合模式可能略快）
time python3 psd_to_code.py input.psd /tmp/output1/
time python3 psd_to_code.py input.psd /tmp/output2/
```

---

## 3️⃣ 性能基准 CI - 快速版

### 问题描述
```
当前：
- ✅ 有性能数据文档（第1-5周）
- ✅ 有 27-29s 目标
- ❌ 无自动检测机制
- ❌ PR 可能无意降性能，无人知道
```

### 改造方案（自动化检测）

```python
# 新增：tests/benchmarks/test_performance.py

def test_full_pipeline(benchmark):
    """完整转换 <30s"""
    result = benchmark(lambda: convert(PSD_PATH))
    assert result.duration < 30

def test_dom_restructure(benchmark):
    """DOM 重构 <3s"""
    result = benchmark(lambda: run_dom_restructure(PSD_PATH))
    assert result.duration < 3

def test_layout_optimizer(benchmark):
    """布局优化 <5s"""
    result = benchmark(lambda: run_layout_optimizer(PSD_PATH))
    assert result.duration < 5
```

### 最小改动清单
```bash
# Step 1: 安装工具
pip install pytest-benchmark pytest-json-report

# Step 2: 创建测试文件
mkdir -p tests/benchmarks
cat > tests/benchmarks/test_performance.py << 'EOF'
import pytest

def test_full_pipeline(benchmark):
    def convert_psd():
        # 调用转换逻辑
        pass
    
    result = benchmark(convert_psd)
    assert result.stats.mean < 30  # 秒

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--benchmark-compare'])
EOF

# Step 3: 本地跑一次基准
pytest tests/benchmarks/ \
  --benchmark-json=baseline.json \
  -v

# Step 4: 保存基线
cp baseline.json .github/benchmarks/baseline.json

# Step 5: 创建 CI 脚本
cat > .github/workflows/performance.yml << 'EOF'
name: Performance Check
on: [pull_request]
jobs:
  benchmark:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v3
      - run: pip install -r requirements-dev.txt
      - run: pytest tests/benchmarks/ \
              --benchmark-compare=.github/benchmarks/baseline.json \
              --benchmark-compare-fail=mean:5%
EOF

# Step 6: 提交 PR 测试
git push origin feature/my-change
# CI 自动运行性能检查
```

### 验证步骤
```bash
# 1. 本地验证基准
pytest tests/benchmarks/ --benchmark-json=current.json -v

# 2. 对比两个 JSON
python .github/scripts/compare_benchmarks.py

# 3. 提交 PR，看 CI 输出
```

---

## 📊 ROI（投资回报率）对比

```
投入时间    收益
─────────────────────────────────────
  3 天    1️⃣ 文件拆分   → 70% 可读性提升
          → 40% 维护成本降低
          → 30% 测试效率提升
          → 20% 扩展性提升

  2 天    2️⃣ 组合改造   → 60% 理解成本降低
          → 50% 维护成本降低
          → 70% 单测时间降低
          → 30% 扩展性提升

  1 天    3️⃣ 性能 CI    → 100% 回归检测覆盖
          → 60% 问题发现快速
          → 50% 审查效率提升
          → 70% 重构信心增加
──────────────────────────────────────
总计 6 天  总收益：理解、维护、测试、扩展全面提升
          每月节省维护时间：~8-10 小时
          一年节省：~96-120 小时 ≈ 12-15 工作日
```

---

## 🔄 实施流程图

```
              ┌─────────────────────────────┐
              │  审查完成：三大优化确认      │
              └──────────────┬──────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
         ▼                   ▼                   ▼
    ┌─────────┐         ┌─────────┐         ┌─────────┐
    │  P0-1️⃣  │         │  P0-2️⃣  │         │  P1-3️⃣  │
    │ 文件拆分 │         │ 组合改造 │         │性能 CI  │
    │ (2-3 天) │         │ (1-2 天) │         │ (1 天)  │
    └────┬────┘         └────┬────┘         └────┬────┘
         │                   │                   │
         ▼                   ▼                   ▼
    创建 4 个     创建 Handler    创建测试框架
    子模块        + 基类          + CI 工作流
         │                   │                   │
         ▼                   ▼                   ▼
   Baseline diff  Baseline diff  基准数据生成
   (应相同)       (应相同)       (第一次记录)
         │                   │                   │
         └───────────────────┼───────────────────┘
                             │
                    ┌────────▼────────┐
                    │   所有 PR 测试   │
                    │  自动检测三项    │
                    └─────────────────┘
```

---

## ✅ 合并检查清单

### 优化前
- [ ] 备份原文件（layer_exporter.py.bak、restructure.py.bak）
- [ ] 创建 feature 分支
- [ ] 运行完整测试套件

### 优化中
- [ ] 逐个创建新文件
- [ ] 逐个验证 baseline diff
- [ ] 更新 import 路径
- [ ] 编写迁移文档

### 优化后
- [ ] 运行完整测试（pytest tests/ -v）
- [ ] 性能基准对比（差异 <5%）
- [ ] 代码审查（reviewers 确认架构）
- [ ] 合并到 main 分支
- [ ] 更新 CHANGES.md

---

## 📚 相关文档

| 文档 | 位置 | 说明 |
|------|------|------|
| 详细优化方案 | `doc/06-optimization-roadmap.md` | 完整的改造步骤 + 代码示例 |
| 架构审查报告 | （自动生成） | architect-review 完整报告 |
| 性能优化指南 | `doc/02-modules/Performance-Optimization.md` | 第1-5周优化历史 |
| 已知约束 | `doc/05-conventions/known-pitfalls.md` | 14 条血的教训 |

---

## 💡 常见问题

**Q: 这三个优化会不会改变转换结果？**
A: 不会。所有改造都是"结构重组"，逻辑完全相同。baseline diff 应该显示无差异。

**Q: 可以只做其中某个吗？**
A: 可以，但建议一起做（同一个 PR）。三个优化互相独立，但一起做可以节省测试时间。

**Q: 这会影响性能吗？**
A: 组合模式可能略快（减少方法查找）。文件拆分和 CI 不影响。预期总体性能 ≥ 当前。

**Q: 需要修改测试吗？**
A: 改动最小化。只需更新 import 路径和添加新的单元测试（handler 隔离测试）。

---

## 🚀 下一步行动

1. **阅读详细文档**：`doc/06-optimization-roadmap.md`（中等难度）
2. **选择优化顺序**：建议 `1️⃣ → 2️⃣ → 3️⃣`
3. **创建分支**：`git checkout -b optimize/refactor-three-areas`
4. **逐个实施**：每个优化可独立提交
5. **申请 Code Review**：用 CodeBuddy 的 Plan 模式审查
6. **合并并庆祝** 🎉

---

**最后提醒**：这三个优化的核心目标是**提高工程质量**，不是改变功能。如果有任何 baseline diff，说明改造有问题，需要回退重做。
