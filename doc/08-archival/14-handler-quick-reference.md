# Handler 架构快速参考卡

## 📍 Handler 位置

```
scripts/targets/html/postprocess/layout_optimizer/transformers/
└─ dom_restructure/
   ├─ handlers/
   │  ├─ __init__.py                 (导出所有 Handler)
   │  ├─ base.py                     (DOMHandler 基类)
   │  ├─ background_handler.py       (✅ 完整实现)
   │  ├─ tall_decor_handler.py       (框架)
   │  ├─ clustering_handler.py       (框架)
   │  ├─ rendering_handler.py        (框架)
   │  └─ reclassify_handler.py       (框架)
   └─ restructure.py                 (主类，待改造)
```

---

## 🎯 5 个 Handler 对照表

| Handler | 对应 Mixin | 主要方法 | 状态 |
|---------|----------|--------|------|
| **BackgroundHandler** | BackgroundMixin | `extract_leaves()` | ✅ 完整 |
| | | `absorb_normal_backgrounds()` | ✅ 完整 |
| | | `passes_safety_filter()` | ✅ 完整 |
| **TallDecorHandler** | TallDecorMixin | `extract_leaves()` | ⏳ 框架 |
| **ClusteringHandler** | ClusteringMixin | `cluster_and_build()` | ⏳ 框架 |
| | | `is_stack_group()` | ⏳ 框架 |
| **RenderingHandler** | RenderingMixin | `render()` | ⏳ 框架 |
| | | `apply_flex_to_container()` | ⏳ 框架 |
| **ReclassifyHandler** | ReclassifyMixin | `upgrade_stack_to_col()` | ⏳ 框架 |
| | | `absorb_container_backgrounds_pass()` | ⏳ 框架 |

---

## 💡 使用示例

### 初始化（在主类中）
```python
from .handlers import BackgroundHandler, ClusteringHandler, RenderingHandler

class DOMRestructure:
    def __init__(self, soup, css_rules, stats, images_dir=None):
        # ... 原有初始化 ...
        
        # ✅ 新增
        self.background = BackgroundHandler(self)
        self.clustering = ClusteringHandler(self)
        self.rendering = RenderingHandler(self)
        # ... 其他 Handler ...
```

### 调用（原 → 新）
```python
# 原有 Mixin 方式
bg_leaves, fg_leaves = self._extract_background_leaves(leaves)
tree = self._cluster(leaves)
child_elem = self._render_tree(tree)

# ✅ 新 Handler 方式
bg_leaves, fg_leaves = self.background.extract_leaves(leaves)
tree = self.clustering.cluster_and_build(leaves)
child_elem = self.rendering.render(tree)
```

### 在 Handler 中访问主对象
```python
class BackgroundHandler(DOMHandler):
    def extract_leaves(self, leaves):
        # 访问主对象的属性
        envelope = self._envelope([l.bbox for l in leaves])
        
        # 访问 config（通过基类）
        tol = self.config.background_contain_tolerance_px
        
        # 调用其他 Handler（如果需要）
        # self.owner.clustering.cluster_and_build(...)
```

---

## 🔧 BackgroundHandler 作为参考

**文件**：`handlers/background_handler.py` (450+ 行)

**学习要点**：
1. 继承 `DOMHandler`
2. 使用 `self.owner` 访问主对象
3. 所有原 Mixin 逻辑保留
4. 方法名简化（去掉前置 `_`）

**复制模板**：
```python
from .base import DOMHandler

class XXXHandler(DOMHandler):
    """功能说明"""
    
    def public_method(self, params):
        """公共方法 docstring"""
        # 实现
        return result
    
    def _private_helper(self):
        """私有方法"""
        # 实现
        pass
```

---

## 📋 迁移检查清单

### Phase 1: 基础设施 ✅ 完成
- [x] 创建 `handlers/` 子模块
- [x] 实现 `DOMHandler` 基类
- [x] 完整实现 `BackgroundHandler`
- [x] 建立其他 Handler 框架

### Phase 2: 完整实现
- [ ] 转换 `TallDecorHandler` 完整代码
- [ ] 转换 `ClusteringHandler` 完整代码
- [ ] 转换 `RenderingHandler` 完整代码
- [ ] 转换 `ReclassifyHandler` 完整代码
- [ ] 为每个 Handler 编写单元测试

### Phase 3: 主类改造
- [ ] 在 `DOMRestructure.__init__()` 添加 Handler 初始化
- [ ] 替换所有 `self._xxx()` 为 `self.handler.xxx()`
- [ ] 删除 Mixin 继承声明
- [ ] 删除 Mixin import 语句

### Phase 4: 验证
- [ ] 运行单元测试
- [ ] 运行集成测试
- [ ] 性能基准测试
- [ ] 输出一致性检查
- [ ] 清理并合并代码

---

## 🎓 关键概念

### DOMHandler 基类提供的接口
```python
class DOMHandler:
    def __init__(self, owner):
        self.owner = owner      # ← 主 DOMRestructure 对象
    
    # 便捷属性（通过 @property）
    @property
    def soup(self):             # ← BeautifulSoup 对象
    @property  
    def css_rules(self):        # ← CSS 规则字典
    @property
    def parser(self):           # ← CSS 解析器
    @property
    def config(self):           # ← 配置对象
    @property
    def stats(self):            # ← 统计信息
    @property
    def images_dir(self):       # ← 图片目录
    
    # 共享工具方法
    def _next_virtual_id(self, kind):      # ← 生成虚拟 ID
    def _envelope(self, bboxes):           # ← 计算包络
```

### Handler 命名约定
- **公共方法**：无前缀 `extract_leaves()` / `render()` / `cluster_and_build()`
- **私有方法**：`_` 前缀 `_helper()` / `_calculate()` / `_check_xxx()`

### 跨 Handler 调用
```python
# ❌ 错误（在 ClusteringHandler 中）
self.background.extract_leaves()

# ✅ 正确
self.owner.background.extract_leaves()
```

---

## 📊 收益量化

### 改造前（113 KB 怪兽文件）
- 查找一个方法：平均搜索 5 个 Mixin
- IDE 智能提示：差（混乱）
- 单测隔离：低（需要加载全部）
- 文件模块度：低

### 改造后（25-35 KB × 5）
- 查找一个方法：直接定位 1 个 Handler
- IDE 智能提示：优（清晰列表）
- 单测隔离：高（只需导入该 Handler）
- 文件模块度：高（职责单一）

---

## 🚀 立即行动

1. **了解架构**
   ```bash
   # 查看 BackgroundHandler 的完整实现
   cat handlers/background_handler.py | head -200
   
   # 查看 DOMHandler 基类
   cat handlers/base.py
   ```

2. **验证语法**
   ```bash
   python3 -m py_compile scripts/targets/html/postprocess/layout_optimizer/transformers/dom_restructure/handlers/*.py
   ```

3. **阅读文档**
   - `doc/11-mixin-to-composition-refactor-roadmap.md` - 路线图
   - `doc/12-handler-migration-guide.md` - 迁移指南
   - `doc/13-optimization-direction-2-status.md` - 状态报告

4. **计划下一步**
   - 选择迁移策略（激进 or 保守）
   - 分配工作任务
   - 设定完成日期

---

## 📞 常见问题

**Q: BackgroundHandler 是否已可使用？**  
A: 是的，已完整实现，可独立使用或集成测试。

**Q: 其他 Handler 何时完成？**  
A: 框架已建立，可按需逐个完整实现。预计 Phase 2 完成。

**Q: 改造会影响现有功能吗？**  
A: 否，所有 Mixin 保留，后向兼容，零影响。

**Q: 什么时候删除原 Mixin？**  
A: 全部 Handler 实现完成 + 测试通过后删除。

**Q: 性能会下降吗？**  
A: 不会，改变的是组织方式，逻辑相同。实际上会更快（减少 MRO 查找）。

---

## 📈 预期时间表

| 任务 | 耗时 | 开始 | 完成 |
|------|------|------|------|
| Phase 2: Handler 完整实现 | 3-4h | - | - |
| Phase 3: 主类改造 | 1-2h | - | - |
| Phase 4: 测试验证 | 1-2h | - | - |
| **总计** | **5-8h** | - | - |

---

**最后更新**：2026-07-03 11:00  
**版本**：1.0 - Handler 架构 Phase 1 完成
