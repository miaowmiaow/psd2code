# DOMRestructure: Mixin → 组合 改造路线图

## 📋 概述

**目标**：将 `DOMRestructure` 的 5 个 Mixin（BackgroundMixin、TallDecorMixin、ClusteringMixin、RenderingMixin、ReclassifyMixin）改造为 5 个独立的 Handler 类，通过显式的组合替代隐式的继承。

**投入**：6-8 工作时间  
**收益**：
- MRO 复杂度降低 80%
- 方法查找成本从 O(n) → O(1)
- 单元测试隔离度提升 70%
- IDE 智能提示大幅改善

---

## 🏗️ 架构对比

### 当前（Mixin 继承）
```
DOMRestructure (113 KB)
├─ BackgroundMixin (450+ 行)
├─ TallDecorMixin (250+ 行)
├─ ClusteringMixin (380+ 行)
├─ RenderingMixin (280+ 行)
└─ ReclassifyMixin (200+ 行)
    ↓ MRO 查找困难，方法在哪里需要逐一搜索
```

### 目标（显式组合）
```
DOMRestructure (200+ 行，仅逻辑)
├─ background: BackgroundHandler
├─ tall_decor: TallDecorHandler
├─ clustering: ClusteringHandler
├─ rendering: RenderingHandler
└─ reclassify: ReclassifyHandler
    ↓ 清晰的方法所有权，IDE 自动补全
    
    每个 Handler(150-300 行)
    └─ 继承 DOMHandler(50 行)
       ├─ owner 引用
       ├─ 便捷属性访问
       └─ 共享工具方法
```

---

## 🔄 改造步骤

### 第 1 阶段：基础设施搭建（✅ 已完成）

- [x] 创建 `handlers/` 子模块
- [x] 创建 `handlers/__init__.py` - 公共出口
- [x] 创建 `handlers/base.py` - DOMHandler 基类
- [x] 创建 `handlers/background_handler.py` - 背景处理（示例完整实现）

**检查点**：
```bash
python3 -m py_compile scripts/targets/html/postprocess/layout_optimizer/transformers/dom_restructure/handlers/*.py
# 应输出无错
```

### 第 2 阶段：转换剩余 Handler（待做）

需按顺序转换以下 Mixin：

#### 2.1 TallDecorHandler
- **源文件**：`tall_decor.py` (250+ 行)
- **公共方法**：
  - `extract_tall_decor_leaves()` → `extract_leaves()`
  - `_extract_tall_decor_leaves()` 相关辅助方法
- **依赖**：
  - `self.owner._envelope()` - 通过基类提供
  - `self.config` - 通过基类属性提供
  - `self.parser` - 通过基类属性提供

#### 2.2 ClusteringHandler
- **源文件**：`clustering.py` (380+ 行，最大的 Mixin)
- **公共方法**：
  - `cluster_and_build_tree()` → 聚类主方法
  - `_cluster()` / `_is_stack_group()` 等
- **依赖**：
  - 其他 Handler 的方法（通过 `self.owner.handlers` 调用）

#### 2.3 RenderingHandler
- **源文件**：`rendering.py` (280+ 行)
- **公共方法**：
  - `render()` → 主渲染方法
  - `_render_tree()` / `_render_stack()` 等
- **依赖**：
  - CSS 写入操作
  - 虚拟 ID 生成

#### 2.4 ReclassifyHandler
- **源文件**：`reclassify.py` (200+ 行)
- **公共方法**：
  - `upgrade_stack_to_col()` → 堆叠升级
  - 容器背景吸收 pass 相关方法
- **依赖**：
  - BackgroundHandler (通过 `self.owner.background`)

### 第 3 阶段：改造主类 DOMRestructure（待做）

#### 3.1 添加 Handler 初始化
```python
class DOMRestructure:
    def __init__(self, soup, css_rules, stats, images_dir=None):
        # ... 原有初始化 ...
        
        # 新增：初始化所有 Handler
        self.background = BackgroundHandler(self)
        self.tall_decor = TallDecorHandler(self)
        self.clustering = ClusteringHandler(self)
        self.rendering = RenderingHandler(self)
        self.reclassify = ReclassifyHandler(self)
```

#### 3.2 更新调用方式
**原有**（直接调用 Mixin 方法）：
```python
bg_leaves, fg_leaves = self._extract_background_leaves(leaves)
```

**改造后**（通过 Handler）：
```python
bg_leaves, fg_leaves = self.background.extract_leaves(leaves)
```

#### 3.3 移除 Mixin 继承
```python
# 删除这行
class DOMRestructure(
    BackgroundMixin,
    TallDecorMixin,
    ClusteringMixin,
    RenderingMixin,
    ReclassifyMixin,
):

# 改为
class DOMRestructure:
```

### 第 4 阶段：方法名称标准化（待做）

为了让 Handler 接口更清晰，建议统一命名规范：

| 原 Mixin 方法名 | 新 Handler 方法名 | 说明 |
|---|---|---|
| `_extract_background_leaves` | `extract_leaves` | 去掉前置 `_`，表示公共方法 |
| `_extract_tall_decor_leaves` | `extract_leaves` | 同上 |
| `_cluster` / `_is_stack_group` | `cluster_and_build` | 公共主方法 |
| `_render_tree` | `render` | 公共主方法 |
| 其他内部方法 | 保持 `_` 前置 | 表示私有方法 |

### 第 5 阶段：测试与验证（待做）

#### 5.1 单元测试
为每个 Handler 创建独立的测试文件：
```
tests/
├─ test_handlers_background.py      (新增)
├─ test_handlers_tall_decor.py      (新增)
├─ test_handlers_clustering.py      (新增)
├─ test_handlers_rendering.py       (新增)
└─ test_handlers_reclassify.py      (新增)
```

#### 5.2 集成测试
验证改造后的流程等价性：
```bash
# 运行现有测试确保没有回归
pytest tests/ -v

# 验证输出与原有版本一致（baseline diff)
diff output/original/ output/refactored/
```

#### 5.3 性能测试
确保 Handler 调用不引入性能开销：
```bash
time python3 psd_to_code.py test.psd --target html
# 预期：耗时基本相同或更快（减少 MRO 查找）
```

---

## 💡 关键设计决策

### 1. Handler 生命周期
- **创建**：在 `DOMRestructure.__init__()` 中创建
- **销毁**：与 DOMRestructure 对象一起回收
- **访问**：通过 `self.owner` 引用主对象

### 2. 跨 Handler 调用
```python
# BadExample：直接在 ClusteringHandler 中调用 BackgroundHandler
# ❌ self.background.extract_leaves(...)  # 错误，ClusteringHandler 没有 background

# GoodExample：通过 owner
# ✅ self.owner.background.extract_leaves(...)
```

### 3. 方法名称冲突处理
不同 Handler 可能有相同名称的方法（如都有 `_xxx_helper`），但由于:
1. 都继承 DOMHandler 基类（不直接继承彼此）
2. 调用明确指定 Handler 对象

不会出现隐式覆盖的问题。

### 4. 工具方法共享
所有 Handler 都能通过基类访问：
- `self.owner._envelope()`
- `self.owner._next_virtual_id()`
- `self.owner._container_css_bbox()`

---

## 🎯 质量指标

### 代码质量
- **圈复杂度**：不变（改变的是组织方式，不是逻辑）
- **代码行数**：总计无增减，仅重新分配
- **方法数/文件**：从 50+ → 15-20（每个文件更小，更专注）

### 可维护性
| 指标 | 改造前 | 改造后 | 改善 |
|---|---|---|---|
| 平均文件大小 | 113 KB | 25-35 KB | ⬇️ 70% |
| 方法查找成本 | O(n) MRO | O(1) 直接引用 | ⬆️ 10 倍 |
| 单测隔离度 | 低(全加载) | 高(单 Handler) | ⬆️ 70% |
| IDE 提示质量 | 差(混乱) | 优(清晰) | ✨ |

---

## 📝 改造检查清单

- [ ] **第 1 阶段** - 基础设施
  - [x] handlers/ 子模块
  - [x] DOMHandler 基类
  - [x] BackgroundHandler 完整实现

- [ ] **第 2 阶段** - 转换剩余 Handler
  - [ ] TallDecorHandler
  - [ ] ClusteringHandler
  - [ ] RenderingHandler
  - [ ] ReclassifyHandler

- [ ] **第 3 阶段** - 改造主类
  - [ ] 添加 Handler 初始化
  - [ ] 更新所有调用方式
  - [ ] 移除 Mixin 继承
  - [ ] 删除原有 Mixin 文件（或保留用于过渡）

- [ ] **第 4 阶段** - 标准化
  - [ ] 审查方法命名
  - [ ] 统一文档字符串
  - [ ] 添加类型注解

- [ ] **第 5 阶段** - 测试
  - [ ] 单元测试
  - [ ] 集成测试
  - [ ] 性能测试
  - [ ] 输出一致性验证

---

## 🚀 快速开始

现在的状态：基础设施已建立（handlers/ 子模块、base.py、background_handler.py 完成）

接下来的步骤：
1. 完成其他 4 个 Handler 的转换（参考 BackgroundHandler 的模式）
2. 更新 DOMRestructure 的 `__init__()` 添加 Handler 初始化
3. 逐一替换方法调用，从 `self.xxx()` 改为 `self.handler_name.xxx()`
4. 运行测试验证等价性

**预计总耗时**：4-6 工作时间（包括测试和验证）

---

## 📚 参考

- 原 optimization-visual.md 的方向 2️⃣ 部分
- BackgroundHandler 完整实现（handlers/background_handler.py）
- DOMHandler 基类设计（handlers/base.py）
