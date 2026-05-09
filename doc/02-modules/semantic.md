# semantic 模块

负责把 PSD 图层的原始名字（中英混排、含 emoji / "拷贝 N" / 数字编号）转为
**kebab-case 语义 token**（如 `btn-receive` / `bg-section` / `coupon`），供
codegen 阶段拼出 CSS class 名和图片文件名。

## 1. 模块拓扑

```
scripts/semantic/
├── __init__.py                   # 公开 NameResolver / NameCandidate
├── name_resolver.py              # 主入口：仲裁多层 candidate
├── layer1_cleaner.py             # Layer 1：清洗 + 扩展词典
└── layer2_role_inferer.py        # Layer 2：DOM 角色推断

scripts/common/
├── cn_dict.json                  # Layer 1 词典（约 470 条，分 11 组）
└── semantic.py                   # Fallback：legacy 关键词 + 拼音
```

调用方：

```
core/extract/layer_exporter.py
  └── common/utils.make_image_filename(name, ltype)
        └── get_default_resolver().resolve_token(name, ltype)        # 无 dom_context

targets/html/codegen/naming.py::SimpleNamer
  └── self._resolver.resolve_token(name, ltype, layer_id, dom_context)  # 带 dom_context
```

`make_image_filename` 与 `SimpleNamer` 共享同一个**进程级 NameResolver**
（`get_default_resolver()`）→ 同一图层的图片名 / class 名共用同一份缓存与
report。

## 2. 多层流水线

| 层级 | source | confidence | 输入 | 触发条件 |
|---|---|---|---|---|
| Layer 2 R1 | `layer2` | 0.95 | dom_context + 已有 candidates | layer1/fallback 给 `btn-*` 但 group 子节点 ≥ 5 含 group → 改写为 `slogan-*` |
| Layer 1 | `layer1` | 0.85 | 原始 name | 清洗后整名 `in` 词典某 pattern；shapes_fallback 组用弱 0.6 |
| Layer 2 R2 | `layer2` | 0.7 | dom_context | shape + 宽 30~400/高 20~120/比例 1.5~8 + 无强 candidate → `btn` |
| Layer 2 R3 | `layer2` | 0.6 | dom_context | group + 占父 ≥ 80% 面积 → `bg-section` |
| Layer 2 R4 | `layer2` | 0.6 | dom_context | group 全部子节点 type=text 且 ≥ 2 个 → `text-block` |
| Fallback | `fallback` | 0.5 | 原始 name | `extract_semantic_token` (legacy 关键词 + 拼音兜底) |

**仲裁规则**（`NameResolver._arbitrate_with_pick`）：

1. 收集所有非空 candidate
2. 按 `(confidence 降序, source_priority 降序)` 排序
3. 取首个非空 candidate 的 `name` 为最终结果

`source_priority`：`layer1=10 > layer2=8 > layer3=6 > vision=4 > fallback=0`
（同 confidence 时，更"显式"的层优先）

## 3. cn_dict.json 词典结构

顶层 11 个分组，每组是 `{中文/英文 pattern: kebab-token}`：

```json
{
  "_meta": { "version": "1.0", "...": "..." },
  "buttons_actions":  { "立即领取": "btn-receive", "去签到": "btn-checkin", ... },
  "structure_layout": { "标题": "title", "导航": "nav", "卡片": "card", ... },
  "decorative_visual": { "背景": "bg", "装饰": "decoration", ... },
  "typography_text":  { "正文": "body", "副标题": "subtitle", ... },
  "user_avatar":      { "头像": "avatar", "用户头像": "avatar-user", ... },
  "ecommerce_marketing": { "优惠券": "coupon", "秒杀价": "flashsale", ... },
  "social_engagement": { "点赞": "like", "评论": "comment", ... },
  "game_activity":    { "宝箱": "chest", "签到": "checkin", "排行榜": "leaderboard", ... },
  "icons_marks":      { "icon": "icon", "图标": "icon", "VIP 标识": "vip-badge", ... },
  "form_input":       { "搜索框": "search-bar", "输入框": "input", ... },
  "shapes_fallback":  { "矩形": "rect", "圆角矩形": "rounded", ... }   // 弱信号 0.6
}
```

**匹配方式**：
- 加载时按 `len(pattern)` **降序**排序——避免 "立即领取按钮" 命中 "按钮" 而错过 "立即领取"
- 对清洗后的 layer name 做**子串匹配**（`pattern in cleaned`）
- `shapes_fallback` 组单独标记为弱 candidate（confidence=0.6），让 Layer 2 的 R2 能压过

**增删词只需改 cn_dict.json，不动 Python 代码。**

## 4. 清洗规则（layer1_cleaner.clean_name）

按顺序执行：

1. 去 emoji / 零宽字符（`_EMOJI` 正则）
2. 全角 → 半角（`（）！？，` → `()!?,`）
3. 剥 "拷贝/copy/副本 N" 后缀（`_COPY_SUFFIX`）
4. 去括号备注（"按钮(已选中)" → "按钮"）
5. 去末尾纯数字编号（"按钮 3" / "btn_02" → "按钮" / "btn"）
6. 折叠多余空白

清洗后的名字仅用于词典匹配，不改写原 layer.name。

## 5. DomContext 字段（Layer 2 输入）

`semantic.layer2_role_inferer.DomContext`：

```python
@dataclass(frozen=True)
class DomContext:
    width:           Optional[float] = None   # 自身宽（R2/R3 需要）
    height:          Optional[float] = None   # 自身高（R2/R3 需要）
    parent_width:    Optional[float] = None   # 父宽（R3 占比）
    parent_height:   Optional[float] = None   # 父高（R3 占比）
    children_types:  tuple[str, ...] = ()     # 直接子节点 type 列表（R1/R4）
    sibling_count:   int = 0                  # 兄弟数（暂未使用，预留）
```

**字段全部可选**，缺失即让对应规则跳过——不会 crash，也不会出 candidate。

调用方传 `dom_context=None` 等价于跳过整个 Layer 2（典型场景：
`make_image_filename` 没图层 dict 上下文，无法构造 DomContext）。

## 6. cache 机制

NameResolver 有进程级 LRU：

```python
cache_key = (layer_id, name, ltype, has_dom_context: bool)
```

**关键**：`has_dom_context` 维度避免 `make_image_filename`(无 dom) 早于
`SimpleNamer`(有 dom) 调用导致 cache 污染——两条路径分别占用不同槽位。

清缓存：
- `NameResolver.reset()` —— 清自身缓存 + report
- `reset_default_resolver()` —— 重置进程单例（在 `common/utils.reset_image_counter()`
  里联动调用，保证每次 PSD 转换前缓存干净）

## 7. _naming_report.md（命名报告）

每次转换会在 `<output>/_naming_report.md` 写出统计 + 明细：

```markdown
# Naming Report

- 总图层数（含重复 resolve 调用）：**432**
- `layer1`: 184 (42.6%)
- `none`: 133 (30.8%)        ← PS 默认名（"组 N" / "形状 M"），交给 ltype 兜底
- `fallback`: 112 (25.9%)
- `layer2`: 3 (0.7%)

## 明细

| layer_id | raw_name | ltype | token | source |
| --- | --- | --- | --- | --- |
| `layer-12` | 日期 | image | `bg-section` | layer2 |
| `group-67` | 立即领取按钮 | group | `btn-receive` | layer1 |
| `layer-100` | 节日氛围图 | image | `jieri` | fallback |
| ...
```

**调试用法**：
- 看到生成的 class 名不合预期 → grep `_naming_report.md` 找该 layer_id，
  看是哪个 source 给出的 token、走的什么规则
- `none` 占比过高 → PSD 图层名大量是 PS 默认值，可考虑给设计师反馈或在
  `cn_dict.json` 加更多关键词
- `layer2` 占比异常高（> 5%）→ 可能 R3 阈值太松，把内容容器误判背景

## 8. 调参指南

集中在 `Layer2RoleInferer` 顶部类常量：

```python
BG_AREA_RATIO        = 0.8     # R3：占父 80% 才算 bg-section
SHAPE_BTN_W_MIN/MAX  = 30/400  # R2：按钮形 shape 宽范围
SHAPE_BTN_H_MIN/MAX  = 20/120  # R2：高范围
SHAPE_BTN_RATIO_MIN/MAX = 1.5/8.0  # R2：宽高比范围
SLOGAN_MIN_CHILDREN  = 5       # R1：子节点数 ≥ 此 + 含 group → 视为 slogan
STRONG_SEMANTIC_CONF = 0.7     # R2~R4 仅当已有 candidate confidence < 此值时介入
```

**踩过的坑（见 memory id=56674654）**：
- `SLOGAN_MIN_CHILDREN` 早期=3，会把"底框+文字+icon"三件套真按钮误降级为
  slogan；改为 5 + 必须含 group
- R3 早期接受 `ltype="image"`，结果"占父 80% 的 prop / icon 主显示图"被命名
  为 bg；改为只对 group 命中
- `shapes_fallback` 组的 confidence 必须 < `STRONG_SEMANTIC_CONF`，否则
  R2 永远抢不过"矩形→rect"

## 9. 扩展未来层

`name_resolver.py::_SOURCE_PRIORITY` 已留好：

```python
"layer1":   10,
"layer2":   8,
"layer3":   6,    # 文本辅助（OCR 文本块标题/正文判断）
"vision":   4,    # 视觉兜底（CLIP 图块分类）
"fallback": 0,
```

新层接入步骤：
1. 在 `semantic/` 下新建 `layerN_xxx.py`，导出 `analyze(...) -> NameCandidate | None`
2. 在 `NameResolver.__init__` 实例化
3. 在 `_collect_candidates` 末尾追加调用（如果需要看其他层结果做仲裁，注意顺序）
4. 调试时打开 `enable_report()` 看 source 分布
