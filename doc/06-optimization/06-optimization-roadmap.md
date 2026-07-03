# 三大优化方向详解

## 优化方向 1️⃣：layer_exporter.py 文件体积优化

### 📊 当前现状

- **文件大小**：113 KB（超过可读性上限 100 KB）
- **行数**：~2500 行
- **主要职责**：PSD 图层导出、光效穿透渲染、图片缓存、异步 IO
- **可维护性**：🔴 困难（单文件过大，方法查找成本高）

### 🎯 拆分目标

将单个 113 KB 文件拆分为 4 个小文件（每个 25-35 KB）：

```
layer_exporter.py (113 KB)
    ↓
layer_exporter_core.py      (30 KB) - 核心导出逻辑
layer_exporter_light_effects.py (28 KB) - 光效穿透 + 缓存
layer_exporter_async.py     (25 KB) - 异步 IO + 对象池
layer_exporter_caching.py   (20 KB) - 统一缓存接口
```

### 📋 具体拆分方案

#### **第1步：layer_exporter_core.py**（原 LayerExporter 主类）

**职责**：
- 入口函数 `export_from_psd()`
- 顶层递归逻辑 `_export_group()` / `_export_single_layer()`
- 坐标系统、BBox 计算
- 基础数据结构 setup

**包含的方法**（~50 个）：
- `export_from_psd()`
- `_export_group()`
- `_export_single_layer()`
- `_merge_clipping_group()`
- `_constrain_bbox_to_canvas()`
- 等基础方法

**示例**：
```python
# layer_exporter_core.py
class LayerExporter:
    def __init__(self, psd, config):
        self.psd = psd
        self.config = config
        self.light_effect_handler = LightEffectHandler(config)
        self.cache_manager = CacheManager()
        self.async_writer = AsyncWriter()
    
    def export_from_psd(self, psd_path):
        # 顶层入口
        ...
    
    def _export_group(self, group_layer):
        # 递归处理组
        ...
```

---

#### **第2步：layer_exporter_light_effects.py**（光效穿透 + 缓存）

**职责**：
- 光效穿透渲染核心算法（Phase 1/2/3）
- 光效叠加缓存（LightEffectCache）
- 采样策略（sample_rate 计算）
- 光效特化的 composite 操作

**包含的方法**（~40 个）：
- `_render_with_light_penetration()`
- `_compute_light_effect_phases()`
- `_sample_light_at_point()`
- `_composite_light_layers()`
- `LightEffectLayerInfo` 数据类
- LightEffectCache 类

**特点**：
- 导出时可以 `from layer_exporter_light_effects import LightEffectHandler`
- 与 core 的交互点清晰：只通过 `LightEffectHandler.render()` 接口
- 便于独立测试和优化

**示例**：
```python
# layer_exporter_light_effects.py
class LightEffectHandler:
    def __init__(self, config, cache_manager):
        self.config = config
        self.cache = cache_manager
    
    def render_with_light_penetration(self, layer, bbox, expand):
        """核心光效穿透渲染"""
        phases = self._compute_light_effect_phases(layer)
        # Phase 1/2/3 逻辑
        return composite_result
    
    def _compute_light_effect_phases(self, layer):
        # 三阶段判定逻辑
        ...
```

---

#### **第3步：layer_exporter_async.py**（异步 IO + 对象池）

**职责**：
- 异步文件写入（AsyncWriter）
- 对象池管理（避免频繁分配 PIL Image、numpy array）
- 错误重试逻辑（磁盘写失败重试）
- IO 统计和监控

**包含的方法**（~25 个）：
- `AsyncWriter` 类（当前缺失重试）
- `ObjectPool` 类（PIL Image、numpy array 复用）
- `_write_with_retry()`
- `_batch_write()`
- 统计收集方法

**改进**：
- 原来的 async_writer.py 是"尽力而为"式，现在加入重试：
  ```python
  def write_with_retry(self, path, data, max_retry=3):
      for attempt in range(max_retry):
          try:
              self._write_sync(path, data)
              return True
          except IOError as e:
              if attempt < max_retry - 1:
                  time.sleep(0.5 * (attempt + 1))
              else:
                  raise
  ```

**示例**：
```python
# layer_exporter_async.py
class AsyncWriter:
    def __init__(self, workers=4):
        self.executor = ThreadPoolExecutor(max_workers=workers)
        self.pool = ObjectPool()
    
    def write_with_retry(self, path, data, max_retry=3):
        """带重试的异步写入"""
        ...

class ObjectPool:
    def acquire_numpy_array(self, shape, dtype):
        """对象池复用 numpy 数组"""
        ...
```

---

#### **第4步：layer_exporter_caching.py**（统一缓存接口）

**职责**：
- 缓存系统统一管理（光效缓存 + 效果渲染缓存 + 属性预计算缓存）
- 缓存策略（LRU / TTL）
- 缓存 I/O（pickle / JSON）
- 缓存统计

**包含的方法**（~20 个）：
- `CacheManager` 类
- `LightEffectCache` 类（从 layer_exporter_light_effects 导入）
- `EffectRenderCache` 类
- `PropertyCache` 类
- 缓存序列化 / 反序列化

**示例**：
```python
# layer_exporter_caching.py
class CacheManager:
    def __init__(self, cache_dir, policy="lru"):
        self.cache_dir = cache_dir
        self.policy = policy
        self.light_effect_cache = LightEffectCache(cache_dir)
        self.effect_render_cache = EffectRenderCache(cache_dir)
        self.property_cache = PropertyCache(cache_dir)
    
    def get_cached_effect(self, layer_id, effect_name):
        return self.effect_render_cache.get(layer_id, effect_name)
    
    def set_cached_effect(self, layer_id, effect_name, data):
        self.effect_render_cache.set(layer_id, effect_name, data)
```

---

### ✅ 拆分检查清单

- [ ] 创建 4 个新文件，添加 `__all__` 导出清单
- [ ] 在 layer_exporter_core.py 中通过 `from layer_exporter_light_effects import LightEffectHandler` 组装依赖
- [ ] 运行 baseline diff：确保转换产出相同的 IR
- [ ] 为每个新模块编写单元测试（光效、缓存、异步 IO 各 2-3 个用例）
- [ ] 性能基准对比：确保拆分后不变或更快（缓存命中率可能上升）
- [ ] 更新 docstring，说明各模块职责

### 📈 预期收益

| 指标 | 当前 | 目标 | 收益 |
|------|------|------|------|
| 最大文件体积 | 113 KB | 35 KB | 可读性 ⬆️ 70% |
| 方法搜索成本 | 高（整文件扫描） | 低（定位到子模块） | 查找时间 ⬇️ 3-5 倍 |
| 单元测试隔离 | 困难（全部依赖） | 易（只测单个模块） | 测试覆盖 ⬆️ 20-30% |
| 代码复用性 | 中 | 高 | 新增 target 集成时间 ⬇️ 30% |

---

## 优化方向 2️⃣：DOMRestructure Mixin 链优化（Mixin → 组合）

### 📊 当前现状

```python
class DOMRestructure(
    BackgroundMixin,        # 背景剥离
    TallDecorMixin,         # 装饰层提取
    ClusteringMixin,        # 聚类算法
    RenderingMixin,         # DOM 渲染
    ReclassifyMixin,        # 升级逻辑
):
    pass
```

**问题**：
1. **方法查找困难**：一个方法可能来自 5 个 Mixin 之一，需要按 MRO 顺序逐一搜索
2. **隐式依赖**：Mixin 间的相互调用缺乏明确文档，新人容易误改
3. **单测困难**：无法单独测试某个 Mixin，必须加载整个 DOMRestructure
4. **重构风险**：改一个 Mixin 的方法签名，需要检查其他 4 个 Mixin

### 🎯 优化目标

改为**显式的组合模式**：

```python
class DOMRestructure:
    def __init__(self, ...):
        self.background = BackgroundHandler(self)
        self.tall_decor = TallDecorHandler(self)
        self.clustering = ClusteringHandler(self)
        self.rendering = RenderingHandler(self)
        self.reclassify = ReclassifyHandler(self)
    
    def restructure_dom(self):
        # 调用各处理器
        self.background.extract_backgrounds()
        self.clustering.cluster_leaves()
        # ...
```

### 📋 具体改造方案

#### **第1步：创建 Handler 基类** (`dom_restructure/handler_base.py`)

```python
# dom_restructure/handler_base.py
from abc import ABC, abstractmethod

class DOMHandler(ABC):
    """所有 DOM 处理器的基类"""
    
    def __init__(self, context: 'DOMRestructure'):
        """
        Args:
            context: DOMRestructure 主实例，作为共享上下文
                   处理器可通过 self.context.soup、self.context.css_rules 访问共享数据
        """
        self.context = context
        self.soup = context.soup
        self.css_rules = context.css_rules
        self.stats = context.stats
        self.parser = context.parser
    
    @abstractmethod
    def validate(self):
        """验证输入数据是否满足本处理器的前置条件"""
        pass
    
    @abstractmethod
    def execute(self):
        """执行处理"""
        pass
    
    def log(self, msg):
        """统一日志接口"""
        print(f"  [{self.__class__.__name__}] {msg}")
```

#### **第2步：改造各 Handler**

**例如 BackgroundHandler**：

```python
# dom_restructure/handlers/background_handler.py
from .handler_base import DOMHandler

class BackgroundHandler(DOMHandler):
    """背景剥离处理器"""
    
    def validate(self):
        """检查是否有 group 需要处理"""
        return len(self.context.get_all_groups()) > 0
    
    def execute(self):
        """执行背景剥离"""
        for group in self.context.get_all_groups():
            bg_leaves, fg_leaves = self._extract_background_leaves(group)
            self._absorb_background_into_container(group, bg_leaves)
        self.log(f"背景剥离完成：{len(self.stats['bg_removed'])} 个元素移除")
    
    def _extract_background_leaves(self, group):
        """原来的方法直接复制过来"""
        # ... 实现
        pass
    
    # 其他方法...
```

**改造其他 4 个 Handler 类似**：
- `TallDecorHandler` (tall_decor.py → handlers/tall_decor_handler.py)
- `ClusteringHandler` (clustering.py → handlers/clustering_handler.py)
- `RenderingHandler` (rendering.py → handlers/rendering_handler.py)
- `ReclassifyHandler` (reclassify.py → handlers/reclassify_handler.py)

#### **第3步：改造主类**

```python
# dom_restructure/restructure.py
from .handler_base import DOMHandler
from .handlers.background_handler import BackgroundHandler
from .handlers.tall_decor_handler import TallDecorHandler
from .handlers.clustering_handler import ClusteringHandler
from .handlers.rendering_handler import RenderingHandler
from .handlers.reclassify_handler import ReclassifyHandler

class DOMRestructure:
    """DOM 重构转换器 - 使用组合模式"""
    
    def __init__(self, soup, css_rules, stats, images_dir=None):
        self.soup = soup
        self.css_rules = css_rules
        self.stats = stats
        self.images_dir = images_dir
        self.parser = CSSParser()
        self.config = ClusterConfig()
        self._virtual_seq = 0
        
        # 显式组合各处理器
        self.background = BackgroundHandler(self)
        self.tall_decor = TallDecorHandler(self)
        self.clustering = ClusteringHandler(self)
        self.rendering = RenderingHandler(self)
        self.reclassify = ReclassifyHandler(self)
    
    def restructure_dom(self):
        """主处理流程"""
        print("  📦 步骤0: DOM重构（空间聚类 + Flex-Ready 产出）...")
        
        all_groups = self._collect_all_groups()
        
        for group in all_groups:
            try:
                # 流程：背景 → 装饰 → 聚类 → 渲染 → 升级
                self._restructure_group(group)
            except Exception as e:
                print(f"  ⚠️  组 {group.get('id')} 处理失败: {e}")
        
        print(f"  ✅ DOM重构完成")
    
    def _restructure_group(self, group):
        """处理单个 group"""
        
        # Step 1: 背景剥离
        if self.background.validate():
            self.background.execute_for_group(group)
        
        # Step 2: 装饰层提取
        if self.tall_decor.validate():
            self.tall_decor.execute_for_group(group)
        
        # Step 3: 聚类
        if self.clustering.validate():
            self.clustering.execute_for_group(group)
        
        # Step 4: 渲染
        if self.rendering.validate():
            self.rendering.execute_for_group(group)
        
        # Step 5: 升级
        if self.reclassify.validate():
            self.reclassify.execute_for_group(group)
    
    # 共享工具方法
    def _collect_all_groups(self):
        """收集所有 group"""
        ...
    
    def get_all_groups(self):
        """供 Handler 调用的接口"""
        return self._collect_all_groups()
```

#### **第4步：目录结构**

```
dom_restructure/
├── __init__.py
├── data_types.py              # LayoutNode 等数据结构
├── restructure.py             # 主类（改用组合）
├── handler_base.py            # 处理器基类 ✨ 新增
├── background.py              # 仅保留纯函数工具（如果有）
├── clustering.py              # 仅保留纯函数工具
├── rendering.py               # 仅保留纯函数工具
├── tall_decor.py              # 仅保留纯函数工具
├── reclassify.py              # 仅保留纯函数工具
└── handlers/                  # ✨ 新增目录
    ├── __init__.py
    ├── background_handler.py
    ├── tall_decor_handler.py
    ├── clustering_handler.py
    ├── rendering_handler.py
    └── reclassify_handler.py
```

### ✅ 改造检查清单

- [ ] 创建 handler_base.py，定义 DOMHandler 抽象基类
- [ ] 创建 handlers/ 目录，逐一迁移 5 个 Mixin 为 Handler
- [ ] 改造主类 restructure.py，使用组合模式
- [ ] 为每个 Handler 编写单元测试（isolation 测试）
- [ ] 运行集成测试，确保转换结果相同（baseline diff）
- [ ] 更新文档，说明新的调用链和依赖关系
- [ ] 性能对比：确保组合模式不引入 overhead

### 📈 预期收益

| 指标 | 当前 | 目标 | 收益 |
|------|------|------|------|
| 方法查找 MRO | 5 层（BackgroundMixin → ... → ReclassifyMixin → object） | 1 层（直接在 Handler） | 理解成本 ⬇️ 60% |
| 隐式依赖 | 高（Mixin 间相互调用无文档） | 低（显式依赖注入） | 重构风险 ⬇️ 50% |
| 单测隔离 | 困难（需要加载整个 DOMRestructure） | 易（只加载单个 Handler） | 测试执行时间 ⬇️ 70% |
| 代码变更安全 | 低（改一个 Mixin 需要检查 4 个其他） | 高（改一个 Handler 只需检查自己和主类） | 重构信心 ⬆️ +40 分 |
| 新增处理器 | 困难（需要理解 Mixin 继承链） | 易（创建新 Handler 继承 base） | 扩展性 ⬆️ +30% |

---

## 优化方向 3️⃣：性能基准 CI 集成

### 📊 当前现状

- **基准数据**：有完整的性能数据（第1-5周优化记录）
- **基准工具**：无（无 pytest-benchmark 配置）
- **CI 检查**：无（无性能回归检测）
- **文档**：完整（Performance-Optimization.md）
- **风险**：PR 可能无意中降低性能，无人发现

### 🎯 优化目标

建立**性能回归检测体系**，每个 PR 自动对比基准，若下降 >5% 则 CI 失败。

```
PR 提交
    ↓
✅ 运行性能基准测试
    ↓
对比与 main 分支的差异
    ↓
如果性能下降 > 5%：
    ❌ CI 失败 + 发起讨论
    
如果性能提升：
    ✅ 记录新基准 + 通过 CI
```

### 📋 具体实施方案

#### **第1步：安装 pytest-benchmark**

```bash
# requirements-dev.txt 增加
pytest-benchmark>=3.4.1
pytest-json-report>=1.5.0  # JSON 格式输出，便于对比
```

#### **第2步：创建性能基准测试** (`tests/benchmarks/`)

```python
# tests/benchmarks/conftest.py
import pytest

@pytest.fixture
def benchmark_config():
    """性能基准配置"""
    return {
        'psd_path': '/Users/zzz/Downloads/input/那家咖啡屋pc开发稿.psd',
        'output_dir': '/tmp/psd2code_benchmark',
        'warmup_count': 1,  # 预热 1 次
        'min_rounds': 3,    # 最少跑 3 遍
    }
```

```python
# tests/benchmarks/test_psd_to_code_performance.py
import pytest
from psd_to_code import convert

class TestPerformanceBenchmark:
    """性能基准测试"""
    
    def test_full_pipeline_time(self, benchmark, benchmark_config):
        """完整转换流程性能"""
        def run_conversion():
            output = convert(
                psd_path=benchmark_config['psd_path'],
                output_dir=benchmark_config['output_dir'],
            )
            return output
        
        result = benchmark(run_conversion)
        # 预期：<30 秒
        assert result['duration'] < 30
    
    def test_dom_restructure_time(self, benchmark, benchmark_config):
        """DOM 重构性能"""
        def run_dom_restructure():
            # 分离测试 DOM 重构阶段
            ...
        
        result = benchmark(run_dom_restructure)
        # 预期：<3 秒
        assert result['duration'] < 3
    
    def test_layout_optimizer_time(self, benchmark, benchmark_config):
        """布局优化性能"""
        def run_layout_optimizer():
            # 分离测试布局优化阶段
            ...
        
        result = benchmark(run_layout_optimizer)
        # 预期：<5 秒
        assert result['duration'] < 5
    
    def test_css_dedup_time(self, benchmark, benchmark_config):
        """CSS 去冗余性能"""
        def run_css_dedup():
            # 分离测试 CSS 去冗余
            ...
        
        result = benchmark(run_css_dedup)
        # 预期：<2 秒
        assert result['duration'] < 2
```

#### **第3步：基准数据基线** (`.github/benchmarks/baseline.json`)

```json
{
  "benchmarks": [
    {
      "name": "test_full_pipeline_time",
      "mean": 28.5,
      "stddev": 1.2,
      "min": 27.1,
      "max": 30.2,
      "unit": "seconds",
      "tolerance": 0.05,
      "last_updated": "2026-07-02"
    },
    {
      "name": "test_dom_restructure_time",
      "mean": 2.8,
      "stddev": 0.3,
      "min": 2.5,
      "max": 3.2,
      "unit": "seconds",
      "tolerance": 0.05,
      "last_updated": "2026-07-02"
    },
    {
      "name": "test_layout_optimizer_time",
      "mean": 4.9,
      "stddev": 0.4,
      "min": 4.5,
      "max": 5.5,
      "unit": "seconds",
      "tolerance": 0.05,
      "last_updated": "2026-07-02"
    },
    {
      "name": "test_css_dedup_time",
      "mean": 1.8,
      "stddev": 0.2,
      "min": 1.6,
      "max": 2.1,
      "unit": "seconds",
      "tolerance": 0.05,
      "last_updated": "2026-07-02"
    }
  ]
}
```

#### **第4步：CI 检查脚本** (`.github/scripts/compare_benchmarks.py`)

```python
# .github/scripts/compare_benchmarks.py
#!/usr/bin/env python3
"""对比性能基准，若下降 > 5% 则失败"""

import json
import sys
import subprocess
from pathlib import Path

def run_benchmarks():
    """运行基准测试并生成 JSON"""
    result = subprocess.run(
        ['pytest', 'tests/benchmarks/', '--benchmark-json=current.json'],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr)
        sys.exit(1)
    return json.loads(Path('current.json').read_text())

def load_baseline():
    """加载基线"""
    return json.loads(Path('.github/benchmarks/baseline.json').read_text())

def compare_benchmarks(baseline, current):
    """对比基线和当前结果"""
    baseline_map = {b['name']: b for b in baseline['benchmarks']}
    current_map = {b['name']: b for b in current['benchmarks']}
    
    failures = []
    improvements = []
    
    for name, current_data in current_map.items():
        if name not in baseline_map:
            print(f"⚠️  新基准: {name}")
            continue
        
        baseline_data = baseline_map[name]
        baseline_mean = baseline_data['mean']
        current_mean = current_data['mean']
        
        # 计算变化比例
        change_pct = (current_mean - baseline_mean) / baseline_mean * 100
        tolerance = baseline_data.get('tolerance', 0.05) * 100
        
        if change_pct > tolerance:
            # 性能下降
            failures.append({
                'name': name,
                'baseline': baseline_mean,
                'current': current_mean,
                'change_pct': change_pct,
                'unit': baseline_data['unit'],
            })
        elif change_pct < -tolerance * 0.5:  # 下降 > -2.5% 才记录提升（避免噪声）
            improvements.append({
                'name': name,
                'baseline': baseline_mean,
                'current': current_mean,
                'change_pct': change_pct,
                'unit': baseline_data['unit'],
            })
    
    return failures, improvements

def main():
    print("🏃 运行性能基准测试...")
    current = run_benchmarks()
    
    print("📊 加载基线...")
    baseline = load_baseline()
    
    print("⚖️  对比结果...")
    failures, improvements = compare_benchmarks(baseline, current)
    
    # 输出结果
    print("\n" + "="*60)
    if improvements:
        print(f"\n✅ 性能提升 ({len(improvements)} 项):\n")
        for imp in improvements:
            print(f"  📈 {imp['name']}")
            print(f"     基线: {imp['baseline']:.2f} {imp['unit']}")
            print(f"     当前: {imp['current']:.2f} {imp['unit']}")
            print(f"     变化: {imp['change_pct']:+.2f}%\n")
    
    if failures:
        print(f"\n❌ 性能回归 ({len(failures)} 项):\n")
        for fail in failures:
            print(f"  📉 {fail['name']}")
            print(f"     基线: {fail['baseline']:.2f} {fail['unit']}")
            print(f"     当前: {fail['current']:.2f} {fail['unit']}")
            print(f"     变化: {fail['change_pct']:+.2f}% (超限: >5%)\n")
        
        print("="*60)
        print("\n⚠️  性能下降，CI 失败！")
        sys.exit(1)
    
    print("="*60)
    print("\n✅ 性能基准检查通过！")
    sys.exit(0)

if __name__ == '__main__':
    main()
```

#### **第5步：GitHub Actions 工作流** (`.github/workflows/performance.yml`)

```yaml
name: Performance Baseline Check

on:
  pull_request:
    branches:
      - main
      - master

jobs:
  benchmark:
    runs-on: macos-latest
    timeout-minutes: 30
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
          cache: 'pip'
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install -r requirements-dev.txt
      
      - name: Download test PSD
        run: |
          # 假设 PSD 文件在某个位置
          mkdir -p /tmp/psd2code_test
          # cp /Users/zzz/Downloads/input/那家咖啡屋pc开发稿.psd /tmp/psd2code_test/
      
      - name: Run performance benchmarks
        run: |
          cd ${{ github.workspace }}
          pytest tests/benchmarks/ \
            --benchmark-json=current.json \
            --benchmark-compare=.github/benchmarks/baseline.json \
            --benchmark-compare-fail=mean:5% \
            -v
      
      - name: Compare with baseline
        run: |
          python .github/scripts/compare_benchmarks.py
      
      - name: Comment PR on performance change
        if: always()
        uses: actions/github-script@v6
        with:
          script: |
            const fs = require('fs');
            const current = JSON.parse(fs.readFileSync('current.json', 'utf8'));
            const baseline = JSON.parse(fs.readFileSync('.github/benchmarks/baseline.json', 'utf8'));
            
            // 生成评论内容
            let comment = '## 📊 性能基准检查结果\n\n';
            comment += '| 测试 | 基线 | 当前 | 变化 |\n';
            comment += '|------|------|------|------|\n';
            
            // 输出结果...
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: comment
            });
```

#### **第6步：本地性能测试**

```bash
# 本地运行基准测试
pytest tests/benchmarks/ \
  --benchmark-json=current.json \
  -v

# 生成 HTML 报告
pytest tests/benchmarks/ \
  --benchmark-json=current.json \
  --benchmark-autosave \
  --benchmark-disable-gc \
  -v
```

### ✅ 性能 CI 检查清单

- [ ] 安装 pytest-benchmark 和相关依赖
- [ ] 创建 tests/benchmarks/ 目录，编写分阶段性能测试
- [ ] 生成基线数据 baseline.json（基于当前稳定版本）
- [ ] 创建 compare_benchmarks.py 脚本
- [ ] 创建 .github/workflows/performance.yml
- [ ] 测试 CI 流程（提交 PR，验证自动检查）
- [ ] 文档化基准数据的更新流程（如何重新生成基线）

### 📈 预期收益

| 指标 | 当前 | 目标 | 收益 |
|------|------|------|------|
| 性能回归检测 | ❌ 无 | ✅ 自动化 | 无意回归 ⬇️ 100% |
| PR 审查成本 | 高（需要手工跑 benchmark） | 低（自动化） | 审查效率 ⬆️ +50% |
| 性能数据可追踪 | 中等（只有文档） | 高（commit 级 tracking） | 历史对比 ⬆️ +80% |
| 问题发现时间 | 晚（部署后才知道） | 早（PR 合并前） | MTTR（平均修复时间） ⬇️ 60% |
| 信心度 | 低（无保障） | 高（自动化保护） | 重构信心 ⬆️ +70 分 |

---

## 📋 总结对比表

| 优化方向 | 工作量 | 复杂度 | 收益 | 优先级 |
|---------|--------|--------|------|--------|
| **1️⃣ layer_exporter.py 拆分** | 2-3 天 | 中 | 可读性 ⬆️ 70%，维护 ⬇️ 40% | **P0** |
| **2️⃣ DOMRestructure Mixin 组合化** | 1-2 天 | 中 | 理解成本 ⬇️ 60%，扩展性 ⬆️ 30% | **P0** |
| **3️⃣ 性能基准 CI** | 1 天 | 低 | 回归检测 100%，MTTR ⬇️ 60% | **P1** |

---

## 🚀 实施路线图

### 📅 第 6 周（即时）- P0 两大优化

**周一-周三**：layer_exporter.py 拆分
- 创建 4 个子模块
- baseline diff 验证
- 单元测试编写

**周四-周五**：DOMRestructure Mixin 组合化
- 创建 Handler 基类
- 改造 5 个 Handler
- 集成测试验证

### 📅 第 7 周 - P1 性能 CI 集成

**全周**：性能基准体系
- pytest-benchmark 集成
- 基线数据生成
- CI 工作流配置
- 本地测试验证

### 📅 第 8-10 周 - 后续优化（P1-P2）

- 类型标注完善（mypy --strict）
- ADR 体系建立
- 统一数据结构（LayoutCoord）
- Loader 抽象层

---

## 📚 相关文件位置

| 优化方向 | 核心文件 | 大小 | 行数 |
|---------|---------|------|------|
| 1️⃣ layer_exporter | `scripts/core/extract/layer_exporter.py` | 113 KB | ~2500 |
| 2️⃣ DOMRestructure | `scripts/targets/html/.../dom_restructure/restructure.py` | - | ~480 |
| 3️⃣ 性能 CI | `tests/`, `.github/workflows/` | - | - |
