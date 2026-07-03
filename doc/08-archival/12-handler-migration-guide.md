# Handler 架构迁移指南

## 📌 当前状态（Phase 1 完成）

✅ 基础设施已建立：
- `handlers/` 子模块创建完毕
- `DOMHandler` 基类已实现（提供 owner 引用和工具访问）
- `BackgroundHandler` 完整实现（450+ 行代码，功能等价）
- 其他 4 个 Handler 框架已建立（使用代理模式过渡）

---

## 🎯 Phase 2：主类迁移方案

### 选项 A：激进方案（立即切换）

优点：快速收益全部激活  
缺点：一次性改动大，风险高

```python
# restructure.py - 改造步骤

# Step 1: 添加 Handler 初始化
class DOMRestructure:
    def __init__(self, soup, css_rules, stats, images_dir=None):
        self.soup = soup
        self.css_rules = css_rules
        self.stats = stats
        self.images_dir = images_dir
        self.parser = CSSParser()
        self.config = ClusterConfig()
        self._virtual_seq = 0
        
        # 新增：初始化 Handler
        from .handlers import (
            BackgroundHandler, TallDecorHandler,
            ClusteringHandler, RenderingHandler, ReclassifyHandler,
        )
        self.background = BackgroundHandler(self)
        self.tall_decor = TallDecorHandler(self)
        self.clustering = ClusteringHandler(self)
        self.rendering = RenderingHandler(self)
        self.reclassify = ReclassifyHandler(self)

# Step 2: 移除 Mixin 继承
class DOMRestructure:  # ← 删除所有 Mixin 继承
    pass

# Step 3: 批量替换方法调用
# 使用正则表达式查找替换：
self._extract_background_leaves → self.background.extract_leaves
self._extract_tall_decor_leaves → self.tall_decor.extract_leaves
self._cluster → self.clustering.cluster_and_build
self._is_stack_group → self.clustering.is_stack_group
self._render_tree → self.rendering.render
self._apply_flex_to_existing_container → self.rendering.apply_flex_to_container
# ... 等等
```

### 选项 B：保守方案（逐步过渡）

优点：低风险，可随时回滚  
缺点：需要维护两套调用方式

```python
# restructure.py - 过渡方案

class DOMRestructure(
    BackgroundMixin,  # ← 保留，暂不删除
    TallDecorMixin,
    ClusteringMixin,
    RenderingMixin,
    ReclassifyMixin,
):
    def __init__(self, ...):
        # ... 原有初始化 ...
        
        # 新增：初始化 Handler（但不替换 Mixin 调用）
        self.background = BackgroundHandler(self)
        self.tall_decor = TallDecorHandler(self)
        # ... 等等

    # 保留所有原有 Mixin 方法，但可标记为 @deprecated
    # 现有代码继续用 self._xxx()，新代码可用 self.handler_name.xxx()
    
    # 逐步替换，一次一个方法：
    def restructure_dom(self):
        all_groups = self._collect_all_groups()
        
        for group in all_groups:
            try:
                # 这里仍然是 self._restructure_group(group)
                # 内部逐步改为使用 Handler
                self._restructure_group(group)
            except ...
```

---

## 🔧 具体改造清单（选项 B - 保守）

### 在 `restructure.py` 中进行改造

#### Step 1: 添加 Handler 初始化
在 `__init__()` 末尾添加：
```python
from .handlers import (
    BackgroundHandler,
    TallDecorHandler,
    ClusteringHandler,
    RenderingHandler,
    ReclassifyHandler,
)

self.background = BackgroundHandler(self)
self.tall_decor = TallDecorHandler(self)
self.clustering = ClusteringHandler(self)
self.rendering = RenderingHandler(self)
self.reclassify = ReclassifyHandler(self)
```

#### Step 2: 逐一替换调用（推荐顺序）

**2a. 替换 BackgroundHandler 调用**

查找所有 `self._extract_background_leaves` 和 `self._absorb_normal_backgrounds`：

```python
# 原有
bg_leaves, fg_leaves = self._extract_background_leaves(work_leaves)

# 改为
bg_leaves, fg_leaves = self.background.extract_leaves(work_leaves)
```

注意：BackgroundHandler 中的 `extract_leaves()` 对应原有的 `_extract_background_leaves()`

**2b. 替换 TallDecorHandler 调用**

```python
# 原有
decor_leaves, remaining = self._extract_tall_decor_leaves(leaves)

# 改为
decor_leaves, remaining = self.tall_decor.extract_leaves(leaves)
```

**2c. 替换 ClusteringHandler 调用**

```python
# 原有
tree = self._cluster(leaves)
is_stack = self._is_stack_group(bboxes)

# 改为
tree = self.clustering.cluster_and_build(leaves)
is_stack = self.clustering.is_stack_group(bboxes)
```

**2d. 替换 RenderingHandler 调用**

```python
# 原有
child_elem = self._render_tree(child_tree, parent_origin=fg.bbox)
self._apply_flex_to_existing_container(group, tree)

# 改为
child_elem = self.rendering.render(child_tree, parent_origin=fg.bbox)
self.rendering.apply_flex_to_container(group, tree)
```

**2e. 替换 ReclassifyHandler 调用**

```python
# 原有
self._upgrade_stack_to_col(tree)
self._absorb_container_backgrounds_pass()

# 改为
self.reclassify.upgrade_stack_to_col(tree)
self.reclassify.absorb_container_backgrounds_pass()
```

#### Step 3: 验证并删除 Mixin 继承

当所有调用都替换完成后：

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

#### Step 4: 删除 import 语句

```python
# 删除这些行
from .background import BackgroundMixin
from .tall_decor import TallDecorMixin
from .clustering import ClusteringMixin
from .rendering import RenderingMixin
from .reclassify import ReclassifyMixin
```

---

## 🧪 验证检查清单

迁移后需要进行以下验证：

### 单元测试
```bash
# 运行所有单元测试
pytest tests/ -v

# 预期：所有测试通过，输出与原有一致
```

### 集成测试
```bash
# 转换一个测试 PSD 文件
python3 psd_to_code.py test.psd --target html

# 检查输出 HTML 是否与原有版本一致
diff output/old/ output/new/
```

### 性能测试
```bash
# 测试性能是否下降（应该相同或更快）
time python3 psd_to_code.py large_file.psd --target html

# 预期：耗时基本相同（±5%）
```

### 代码检查
```bash
# 验证语法无误
python3 -m py_compile scripts/targets/html/postprocess/layout_optimizer/transformers/dom_restructure/restructure.py

# 验证 Lint
pylint scripts/targets/html/postprocess/layout_optimizer/transformers/dom_restructure/restructure.py
```

---

## 🎓 学习要点

### Handler 的优势
1. **方法所有权清晰**：看 `self.background.xxx()` 立即知道在哪个模块
2. **单元测试隔离**：只需导入 `BackgroundHandler`，无需加载整个 `DOMRestructure`
3. **IDE 智能补全**：输入 `self.background.` 会列出所有可用方法
4. **并行开发**：多人可同时开发不同的 Handler，无 MRO 冲突

### 迁移陷阱
1. **跨 Handler 调用**：必须通过 `self.owner` 访问其他 Handler
   ```python
   # ❌ 错误
   self.background.extract_leaves()  # 在 ClusteringHandler 中
   
   # ✅ 正确
   self.owner.background.extract_leaves()
   ```

2. **工具方法访问**：通过基类属性
   ```python
   # ✅ 正确方式
   self._envelope(...)  # 基类已实现
   self.config.xxx      # 基类属性提供
   ```

3. **变量生命周期**：Handler 与 DOMRestructure 同生命周期
   ```python
   dom = DOMRestructure(...)
   dom.background.extract_leaves(...)  # ✅ 可用
   
   del dom
   # dom.background 也被回收
   ```

---

## 📋 后续改进方向（Phase 3）

1. **完整 Handler 实现**
   - 当前 Handler 使用代理模式，后续应实现完整逻辑
   - 示例：BackgroundHandler 已完整（参考该文件）

2. **类型注解**
   - 为所有 Handler 方法添加类型注解
   - 改善 IDE 智能提示

3. **文档完善**
   - 为每个 Handler 添加详细的 docstring
   - 记录依赖关系和使用示例

4. **单元测试**
   - 为每个 Handler 创建独立的单元测试文件
   - 提高测试覆盖率

---

## 🚀 快速启动

如果要立即启动迁移：

### 1. 了解现状
```bash
# 查看 BackgroundHandler 的完整实现（参考）
cat handlers/background_handler.py | head -100

# 查看其他 Handler 的框架
ls -l handlers/*_handler.py
```

### 2. 选择迁移策略
- **激进**（全量）：一次性替换所有调用 → 快速但高风险
- **保守**（逐步）：逐个替换方法 → 慢但低风险 ✓ **推荐**

### 3. 执行迁移
按"改造清单"中的步骤执行，验证每一步

### 4. 持续改进
迁移完成后，逐步完成其他 Handler 的完整实现

---

## 📞 需要帮助？

- 查看 BackgroundHandler 的完整实现作为参考
- 参考原 Mixin 文件了解历史逻辑
- 运行测试验证改造的正确性
