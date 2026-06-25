# 模块：`core/ir/`

> **本文解决什么**：讲清 IR 数据类的结构 / 字段 / 校验，以及 legacy adapter。
> **不讨论什么**：IR 的契约哲学与演进（见 `../03-topics/ir-contract.md`）。

## 位置

```
core/ir/
├── __init__.py       # re-export（你可以 from core.ir import Document, GroupNode, ...）
├── document.py       # Document（根）
├── nodes.py          # GroupNode / ImageNode / TextNode / ShapeNode + 联合 Node
├── styles.py         # BBox / Color / FontStyle / Style
├── effects.py        # StrokeSpec / DropShadowSpec / ... / EffectSpec 联合
├── assets.py         # AssetRef
└── adapters.py       # to_legacy_layers()
```

所有数据类基于 `pydantic.BaseModel`（v2）。

---

## Document

```python
class Document(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    canvas: Optional[BBox] = None
    source_psd: Optional[str] = None
    title: Optional[str] = None
    root: GroupNode                    # 整棵图层树的根
    assets: List[AssetRef] = []
    meta: dict = {}                    # 自由元信息

    def iter_nodes(self): ...          # 先序遍历所有节点
```

## Node 类族

```python
class NodeKind(str, Enum):
    GROUP = "group"
    IMAGE = "image"
    TEXT = "text"
    SHAPE = "shape"

class _NodeBase(BaseModel):
    id: str
    name: str = ""
    style: Style
    effects: List[EffectSpec] = []
    meta: dict = {}           # 自由提示，例如 meta['legacy'] 保留旧 dict

class GroupNode(_NodeBase):
    kind: Literal[NodeKind.GROUP]
    children: List[Node] = []
    merged_asset: Optional[AssetRef] = None  # 组已被合并成单图时填此字段

class ImageNode(_NodeBase):
    kind: Literal[NodeKind.IMAGE]
    asset: AssetRef

class TextNode(_NodeBase):
    kind: Literal[NodeKind.TEXT]
    text: str = ""
    runs: List[dict] = []     # 可选：多段 rich text

class ShapeNode(_NodeBase):
    kind: Literal[NodeKind.SHAPE]
    asset: Optional[AssetRef] = None   # MVP 阶段 shape 栅格化为 image
```

`Node = Annotated[Union[...], Field(discriminator="kind")]` —— pydantic 基于 `kind` 自动选择子类。

**遍历小贴士：**

```python
for n in doc.iter_nodes():
    if isinstance(n, GroupNode):
        ...
    elif isinstance(n, ImageNode):
        ...
```

## Style / BBox / Color / Font

```python
class BBox(BaseModel):
    left: int; top: int; right: int; bottom: int
    @property width / height
    # 校验：right >= left, bottom >= top

class Color(BaseModel):
    r: int[0..255]; g: int[0..255]; b: int[0..255]; a: float[0..1]
    def to_css(self) -> str

class FontStyle(BaseModel):
    family, size_px, weight, italic, line_height_px,
    letter_spacing_px, align ('left'|'center'|'right'|'justify'), color

class Style(BaseModel):
    bbox: BBox                               # 必填
    opacity: float[0..1] = 1.0
    visible: bool = True
    border_radius_px: Optional[float] = None
    z_index: Optional[int] = None
    background_color: Optional[Color] = None
    font: Optional[FontStyle] = None
    extra: dict = {}                          # target 专属提示
```

## Effects

```python
_EffectBase: enabled: bool = True; opacity: float[0..1] = 1.0

StrokeSpec:          size_px, color, position ('outside'|'center'|'inside')
DropShadowSpec:      color, distance_px, angle_deg, spread_px, blur_px
InnerShadowSpec:     color, distance_px, angle_deg, choke_px, blur_px
OuterGlowSpec:       color, spread_px, blur_px
InnerGlowSpec:       color, choke_px, blur_px
ColorOverlaySpec:    color
GradientOverlaySpec: gradient_type ('linear'|'radial'), angle_deg, stops
```

所有效果都是 `kind: Literal[...]` + pydantic 校验。

## AssetRef

```python
class AssetRef(BaseModel):
    kind: Literal["image"|"font"|"video"|"other"] = "image"
    src: str                           # 相对输出根的路径，如 "images/bg.png"
    absolute_path: Optional[Path] = None
    width / height: Optional[int] = None
    format: Optional[str] = None       # "png"|"jpg"|"webp"|...
    sha1: Optional[str] = None
    extra: dict = {}
```

## legacy adapter

位置：`core/ir/adapters.py`

```python
def to_legacy_layers(doc: Document) -> list[dict]:
    """
    如果 doc 由 parser.parse_psd_to_ir() 构造，
    会在 doc.root.meta['legacy_roots'] 保留旧 dict 树，直接返回它；
    否则从 IR 字段 best-effort 重建。
    """
```

**这是有意保留的"过渡期逃生舱"**：新旧 codegen 可以共存，保证回归零差异。
随着字段逐步提升到 IR 的一等字段，`legacy` 的覆盖面会越来越少。

## 扩展：新增字段

1. 在 pydantic 类上加字段（带默认值向后兼容）。
2. 如果是"语义字段"，在 `core/psd/parser.py` 的映射中填充它。
3. 如果 target 要读，在 codegen 里消费它；暂不读也无妨。
4. **不要** 删 `meta['legacy']`，直到所有 target 都不再依赖它。

## 性能优化

本模块包含第4-5周的性能优化实现：

| 优化项 | 位置 | 效果 |
|-------|------|------|
| **IR 字段补全** | `ir_enricher.py` | 从 legacy 提取关键字段 |
| **TypedIRCache** | `typed_ir_cache.py` | O(1) IR 节点查询 (-2-3%) |
| **DeltaIR 增量** | `delta_ir.py` | 检测变化节点 (-2-3%) |
| **StreamingIterator** | `streaming_iterator.py` | 流式迭代器 (-87% 内存) |

详见 [Performance-Optimization.md](./Performance-Optimization.md)。
