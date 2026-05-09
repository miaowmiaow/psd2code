# 模块：`common/` 与 `config/`

> **本文解决什么**：讲清通用工具库与全局配置的用法。
> **不讨论什么**：业务逻辑（只是工具）。

---

## `common/utils.py`

图片命名与全局计数器。

```python
_image_counter: int = 0
_used_filenames: set[str] = set()

def reset_image_counter() -> None       # 每次转换开始前调用（兼容降级路径）
def next_image_id() -> int              # 线性自增，降级路径用
def reset_filename_registry() -> None   # 每次转换开始前调用，清空冲突注册表
def sanitize_filename(name, max_length=50) -> str  # 走 common.semantic 抽语义 token
def make_image_filename(
    name, max_length, format,
    *, content_hash: str | None = None, ltype: str = "image"
) -> str  # "{semantic}-{hash6}.{format}"
```

### 图片命名规则（2026-04 起）

产物格式：`<semantic>-<hash6>.<ext>`，例如 `btn-receive-279914.png`。

- **semantic**：由 `common.semantic.extract_semantic_token` 从图层名抽取；
  PS 默认名（"矩形 3"/"图层 5"/"矢量智能对象" 等）会被过滤，走 `ltype` 兜底
  （`image→img`、`shape→shape`）。
- **hash6**：图片内容 md5 前 6 位。这让产物**稳定**（PSD 没改 → 文件名不变，
  git diff/CDN 缓存友好），并且天然与 `LayerExporter._save_image_dedup` 的内容
  去重逻辑一致。
- **冲突兜底**：同 `semantic` 且 hash6 撞车时自动加 `-2/-3` 后缀。

> **向后兼容**：老调用签名 `make_image_filename(name, max_length, fmt)`（不传
> `content_hash`）仍能跑，但会降级为全局递增编号 `nXXXX`，**文件名不稳定**，
> 新代码请始终传 `content_hash`。

> `reset_image_counter` / `reset_filename_registry` 被 `ParseToIrStage` 调用；
> 新 Stage 若需要"按运行次数重置全局状态"，应在 Stage 开始处显式调用对应 reset
> 函数，**不要**依赖模块加载顺序。

## `common/semantic.py`（语义命名 Fallback 层）

`extract_semantic_token` 现在是 [`semantic`](./semantic-pipeline.md) 流水线
中的 **Fallback 层**（confidence=0.5），位于 Layer 1 (词典 0.85) 与 Layer 2
(DOM 角色 0.6~0.95) 之后兜底。**不要直接调用**，请走 `NameResolver.resolve_token`。

```python
def extract_semantic_token(name, ltype="") -> str   # 返回 kebab-case 短 token 或 ""
def is_default_ps_name(name) -> bool                # PS 默认名识别（日志用）
```

### 抽取优先级（fallback 路径）

1. 剥去 "拷贝 N" / "copy N" 后缀；
2. 命中语义关键词表 `_KEYWORDS`（中英文都支持，按优先级从长到短） → 返回对应英文词；
3. 命中 PS 默认名正则（`图层 \d+` / `矩形 \d+` / `矢量智能对象` 等） → 返回 `""`
   交给调用方用 `ltype` 兜底；
4. 纯 ASCII（且非纯数字） → kebab 化；
5. 含中文且未命中词表 → 取**首个**中文片段（最多 3 个汉字）拼音；
6. 空串 / 纯数字 / 纯标点 → 返回 `""`。

### 业务扩词的位置（重要）

**优先改 [`common/cn_dict.json`](#commoncn_dictjson)**（Layer 1 词典），不再扩
`_KEYWORDS`。Layer 1 命中 confidence=0.85 强于 Fallback，能压过拼音兜底。

只在以下场景改 `_KEYWORDS`：拼音兜底逻辑（如想把"端午"映射成 `duanwu` 而不是
出 ""）。

## `common/cn_dict.json`（Layer 1 扩展词典）

约 470 条，按业务域分 11 组（buttons_actions / structure_layout /
decorative_visual / typography_text / user_avatar / ecommerce_marketing /
social_engagement / game_activity / icons_marks / form_input / shapes_fallback）。
加新词只改 JSON，不动代码。

详见：[`semantic-pipeline.md`](./semantic-pipeline.md#3-cn_dictjson-词典结构)。

## `common/css_utils.py`

```python
def parse_css_to_dict(css) -> dict[selector, dict[prop, value]]
def extract_global_css_header(css) -> str   # 保留 * / body / @media / #canvas 等非类块
def dict_to_css(rules, header='', merge_groups=None) -> str
```

用于 LayoutOptimizeStage 在字符串与字典之间切换。`merge_groups` 用于把属性
等价的多个选择器合并为 `.a, .b, .c { ... }`（由 `CssDedup` 产出）。

**注意（2026-04-29 修复）**：``_iter_top_level_blocks`` 按 ``{`` 切分时会把
"块前注释"粘到 selector 头部（如 ``"/* 图层样式 */\n.bg__1"``），导致
``parse_css_to_dict`` / ``extract_global_css_header`` 误判为非 class/id 块。
新增内部辅助 ``_strip_leading_comments`` 在判定前剥掉前置注释，确保第一条
正常 ``.class`` 规则不会被错误地塞进全局头。

最终 CSS 写盘走 ``CssPretty`` 的渲染路径（DOM 序排序 + 属性分段 + 合并组多行），
``dict_to_css`` 仅作为降级路径保留。详见
[`targets-html.md` § Postprocess：CssPretty](./targets-html.md#postprocesscsspretty-css-美化渲染)。

## `common/image_utils.py`（重要）

```python
class BBoxUtils:
    constrain_to_canvas(bbox, w, h)
    expand_bbox(bbox, expand)
    get_dimensions(bbox)
    is_valid(bbox)
    # ... 其他

class ImageArrayUtils:
    pil_to_float_array(img) -> np.ndarray[H,W,4] float32  # 0..1
    pil_l_to_float_array(img) -> np.ndarray[H,W] float32  # L 模式（蒙版 / alpha）
    float_array_to_pil(arr) -> PIL Image (uint8 RGBA)
    float_to_uint8_rgba(arr) -> np.ndarray uint8

class ImageBlendUtils:
    alpha_composite(base_arr, over_arr)
    # ...
```

### 约定

**禁止** 在业务代码里手写：

- `np.array(img, dtype=np.float32) / 255.0` → 用 `ImageArrayUtils.pil_to_float_array`
- `(np.clip(arr, 0, 1) * 255).astype(np.uint8)` → 用 `ImageArrayUtils.float_to_uint8_rgba`
- `Image.fromarray((np.clip(arr,0,1)*255).astype(np.uint8), 'RGBA')` → 用 `ImageArrayUtils.float_array_to_pil`
- `np.array(mask.convert('L'), dtype=np.float32) / 255.0` → 用 `ImageArrayUtils.pil_l_to_float_array`

所有图像相关转换统一走 `ImageArrayUtils`，便于后续做 dtype / 精度 / 通道的批量优化。

---

## `config/config.py`

```python
__version__ = '1.1.0'

class Config:
    OUTPUT_BASE_DIR: str    # 默认输出根目录
    IMAGE_FORMAT: str = 'png'
    MAX_FILENAME_LENGTH: int = 50
    CONSTRAIN_GROUP_TO_CANVAS: bool = True
    CROP_OVERFLOW_IMAGES: bool = True
```

**何时改：**

- `OUTPUT_BASE_DIR`：改默认输出位置
- `IMAGE_FORMAT`：改 png→jpg/webp（需回归 diff）
- `CONSTRAIN_GROUP_TO_CANVAS` / `CROP_OVERFLOW_IMAGES`：处理超大组 / 超出画布内容

**何时不要改：**

- 不要在业务代码里 `from config import Config` 后 `Config.XXX = ...` 动态改；
  保持 Config 为"只读全局配置"。
  如需运行时参数，走 `PipelineContext` 或 CLI 参数。

---

## `__init__.py` 的风格约定

- `common/__init__.py`：空或仅 re-export
- `config/__init__.py`：`from .config import __version__`
- `core/ir/__init__.py`：re-export 一整套类，作为"包外的 API 门面"
- `framework/__init__.py`：re-export `Pipeline / PipelineContext / Stage / PipelineHook`
- 新 target 的 `targets/<name>/__init__.py`：必须 `from .target import <XxxTarget>`
  以触发 `@register` 装饰器

> 不要删 `__init__.py`。字节码缓存在入口处已全局禁用（`sys.dont_write_bytecode=True`），
> `.gitignore` 也兜底忽略 `__pycache__/`。
