---
name: psd2code-relayout
description: 在 psd2code 生成 HTML 产物之后，以「渲染截图」为唯一结构依据重新做版面布局，产物仅作为素材来源与像素验收基准，最终生成符合前端规范的 index_ai.html。适用于用户要求产物级再工程化、优化已生成页面的结构、把绝对定位拼贴改成 flex/grid、合并碎图、让页面更可维护时。触发词：产物优化、重新布局、重新工程化、截图重排、结构优化、relayout。
---

# psd2code 产物重排（截图驱动）

## 核心前提：原产物的结构不可信

PSD 的图层摆放不遵守前端规范——设计师只关心"看起来对"，图层可以任意嵌套、任意命名、把纯装饰拆成三层、把该对齐的元素错开几个像素。这带来一个直接后果：

> **原产物的 DOM 结构没有语义价值，不能作为改版依据。**

实测证据（南瓜大作战）：

| 现象 | 实测 |
|---|---|
| 同名不同构 | `prop-bg-2-2` ~ `prop-bg-2-18` 命名同类，结构各异（2 层 / 3 层 / 带文本） |
| 该对齐的没对齐 | 截图是规整 4 列格子墙，产物是「3 列 group + 第 4 列散落元素」 |
| 纯装饰拆多层 | `avatar` → `ellipse-2` → `avatar-3` 三层嵌套，视觉上就是一个圆头像 |
| 坐标无统一参考系 | 评论卡 `zuixin` 的 `top` 是 `-101px`（相对旧父级的残留） |
| 命名无信息 | `img-2-3` / `img-23` / `img-3-2` |

所以产物只承担两个职责：

1. **素材来源**——哪张图、哪段文字、什么颜色和字号；
2. **验收基准**——改完之后与原渲染做像素比对。

**结构必须由截图重新生成。**

## 分工

| 角色 | 由谁承担 |
|---|---|
| 看截图、识别版面、设计 flex/grid 骨架 | **你（AI）** |
| 采集截图、解构素材池、像素裁决 | 本 skill 的脚本 |
| 是否允许保留 | **像素/位置校验**，不是你的主观判断 |

不要用启发式规则扫 DOM 决定改什么——那仍然是在依赖不可信的结构。

## 工作流

### Step 0 · 建副本

```bash
cd <html_dir>
cp index_optimized.html index_ai.html
cp style_optimized.css style_ai.css
python3 - <<'EOF'
import re, pathlib
p = pathlib.Path("index_ai.html")
p.write_text(re.sub(r'style_optimized\.css(\?v=[0-9a-f]+)?', 'style_ai.css',
                    p.read_text(encoding="utf-8")), encoding="utf-8")
EOF
```

改 CSS 引用这一步不能省。漏掉会得到"DOM 改了、样式还是旧的"的假失败（实测头像整块空白、`maxΔ=255`）。

### Step 1 · 采集：截图 + 素材池

```bash
python scripts/scan_page.py   <html_dir>     # 全页切片截图 + 结构参考
python scripts/extract_assets.py <html_dir>  # 扁平素材池
```

| 产物 | 用途 |
|---|---|
| `slices/slice-*.png` | **结构判断的唯一依据**，逐张看完 |
| `base.png` | 全页基准图 |
| `assets.json` | 扁平素材池：`images[]`（含 bbox / z / 复用计数）、`texts[]`（含样式）、`palette`、`text_traps[]` |
| `assets_preview.png` | 素材总览图，肉眼核对素材种类与重复情况 |
| `dom.json` | **仅用于按 bbox 定位素材**，不要读它的父子关系 |

`assets.json` 是刻意做成**扁平**的：不带 parent/children。任何布局方案只引用素材 `id`，不回头读 DOM。

`text_traps` 是合并前的必查项：列出「文字藏在容器内部更深层」的位置。实测 V1 稿 138 个文本里有 97 个属于这种情况——**合并任何容器前，先假设文本在深层，必须 `span.extract()` 再删子层**，否则文字会连带消失且像素校验才会发现。

### Step 2 · 从截图设计骨架

逐张看 `slices/`，把页面拆成语义区块，为每个区块写出结构描述（写入 `.relayout/layout.json`，格式见 `references/layout-schema.md`）：

```json
{
  "id": "prop-grid",
  "type": "grid",
  "columns": 4,
  "gap": [40, 119],
  "children": [
    {"type": "cell", "slots": {"icon": "img-042", "tag": "img-017", "label": "img-031"}}
  ]
}
```

要点：

- **只描述结构**：容器类型、方向、列数、间距、对齐；不写 `top: 792px` 这类旧世界坐标；
- **槽位绑定素材 id**：从 `assets.json` 按 bbox 归属挑选；
- **同一素材可复用**：实测 261 个图片元素只对应 106 张唯一文件（南瓜图标复用 15 次），骨架里重复引用同一 id 即可。

判断准则见 `references/visual-judgement.md`。

### Step 3 · 生成 HTML/CSS

按骨架产出新的 `index_ai.html` + `style_ai.css`：

- 容器用 flex / grid，间距用 `gap` 或 `margin`；
- 图片素材直接引用 `images/<file>`，**不重新切图**；
- 文字素材用原文本，保留字号 / 行高 / 颜色；
- 纯装饰（角标、阴影层）如无结构意义，可作为容器的 `background` 或独立绝对定位元素，但**不要为了"结构漂亮"牺牲位置精度**。

### Step 4 · 验收（不能用"零 diff"）

布局重建后，行高、盒模型、字体渲染都会变，逐像素零差异是不可能的。改用**分区验收**：

```bash
python scripts/verify_page.py index_optimized.html index_ai.html \
    --max-delta 1 --region 0,740,750,560     # 按区块逐个验
```

| 对象 | 标准 |
|---|---|
| 图片素材 | 位置、尺寸误差 ≤ 1px，内容逐像素一致 |
| 文本素材 | 落在原 bbox 内、内容完整、对齐方向一致 |
| 整体 | 差异像素占比与差异区域集中度，而不是零差异 |

不通过的区块：回退该区块，记录原因，修正重试（同一块最多 2 次）。

### Step 5 · 汇总

写 `<html_dir>/_relayout_report.md`：

| 区块 | 骨架 | 素材数 | 结果 | 差异 |
|---|---|---|---|---|

并说明：哪些区块重建成功、哪些保留原结构（及原因）、整体差异量级。

## 硬约束

1. **文字永远是 DOM 文本**，不转图；合并容器前先查 `text_traps`，把 `span` 摘出来再删子层。
2. 结构**只从截图得出**；`dom.json` 的父子关系不作为依据。
3. 素材只引用不重切；同一素材多次复用时共用同一文件。
4. 每次落地改动要跑 `verify_page.py`，并按区块记录差异。
5. **一次改一个区块**，改完立刻验收，便于归因。
6. class 必须全页唯一才能改/删对应 CSS 规则。

## 反模式

- 扫 DOM 找"可合并的图片组"然后批量拍平——这是把不可信结构当依据。
- 只为"结构更漂亮"而移动元素，导致位置漂移。
- 用整页 `maxΔ ≤ 1` 去卡一次布局重建（必然失败），或为了让 diff 变小而回退语义化结构。
- 把文字渲染成图片来"简化结构"。

## 收益在哪里：模板化，而不是继续拆布局

实测南瓜产物：`LayoutOptimizer` 已经把该 flex 的容器都 flex 化了（"父容器非 flex 且子元素单轴排列"的容器数为 **0**）。继续在布局上找空间没有收益。

真正的不规范是**重复单元被复制 N 份**：15 个道具格 = 15 份独立 DOM + 15 套 CSS。把它们收敛成一个模板 + CSS 变量，实测：

| 指标 | 改前 | 改后 |
|---|---:|---:|
| `div` 节点 | 521 | 461（−60） |
| CSS 规则 | 464 | 378（−86） |
| CSS 体积 | 73.8 KB | 60.3 KB（−18%） |
| 整页像素差 | — | **0** |

详见 `references/templatize.md`。这类改造几何不变、只换承载方式，所以能做到零像素差异。

## 参考

- `references/layout-schema.md` — layout.json 完整字段
- `references/visual-judgement.md` — 从截图设计骨架的准则
- `references/templatize.md` — 重复单元模板化（收益最高、零像素代价）
- `references/pitfalls.md` — 实测踩坑
