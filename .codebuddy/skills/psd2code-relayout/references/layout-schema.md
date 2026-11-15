# layout.json 结构

`layout.json` 是**你从截图得出的结构方案**，与产物 DOM 无关。它只描述"版面长什么样"，并用素材 id 指向 `assets.json` 里的具体图片/文本。

落盘的意义：结构决策可复核、可重跑、可对照验收结果，而不是只存在于对话里。

## 顶层

```json
{
  "source": "index_optimized.html",
  "canvas": [750, 6778],
  "sections": []
}
```

## block

所有节点共用一个 `block` 结构，靠 `type` 区分：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 唯一，语义命名（`prop-grid` / `comment-card`） |
| `type` | string | 见下表 |
| `bbox` | [x, y, w, h] | 该区块在基准截图上的位置，**仅供参考与验收**，不写进 CSS |
| `children` | block[] | 子节点 |
| `slots` | object | 素材绑定，`{槽位名: 素材 id}` |
| `gap` | [row, col] | 间距，来自截图测量（用 `assets.json` 的 bbox 印证） |
| `reason` | string | 一句话说明为什么这么分（写给自己） |

### type 取值

| type | 对应 CSS | 用途 |
|---|---|---|
| `section` | 普通块级容器 | 页面大分区 |
| `row` | `display:flex; flex-direction:row` | 横向排列 |
| `column` | `display:flex; flex-direction:column` | 纵向排列 |
| `grid` | `display:grid` | 等宽重复单元（格子墙、列表） |
| `cell` | grid/flex 的子项 | 单元容器，内部通常再套 column |
| `absolute` | `position:absolute` | **必须**脱离流的元素（出血、重叠、旋转、角标） |
| `background` | 容器 `background-image` | 底图，通常对应 `role: "background"` 的素材 |

### grid 额外字段

| 字段 | 说明 |
|---|---|
| `columns` | 列数 |
| `column_width` | 列宽（px）；等宽时写一次即可 |
| `rows` | 行数（可选，用于校验） |

### slots

```json
{"slots": {"icon": "img-042", "count_tag": "img-017", "label": "txt-008"}}
```

- key 由你定义（`icon` / `title` / `avatar` …），只要同一类单元保持一致；
- value 必须是 `assets.json` 里存在的 `id`；
- 同一素材可以在多处复用——实测 261 个图片元素只对应 106 张唯一文件，重复引用是常态，不是错误。

## 完整示例（道具格子墙）

```json
{
  "source": "index_optimized.html",
  "canvas": [750, 6778],
  "sections": [
    {
      "id": "prop-wall",
      "type": "section",
      "bbox": [24, 760, 702, 1030],
      "reason": "截图上是 4 列格子墙，第 4 列为宝箱（尺寸与其他列不同）",
      "children": [
        {
          "id": "prop-grid-main",
          "type": "grid",
          "columns": 3,
          "column_width": 135,
          "gap": [119, 40],
          "bbox": [47, 792, 486, 1030],
          "children": [
            {
              "id": "cell-1-1",
              "type": "cell",
              "bbox": [47, 792, 135, 207],
              "slots": {"icon": "img-042", "count": "txt-018", "label": "img-031"},
              "reason": "每格纵向 3 段：南瓜图标 / 糖果数量标签 / 领取条"
            }
          ]
        },
        {
          "id": "prop-column-chest",
          "type": "column",
          "gap": [119, 0],
          "bbox": [573, 760, 152, 1030],
          "reason": "宝箱列高 52~60，与主格 88 不同构，单独一列",
          "children": [
            {"id": "chest-1", "type": "cell", "bbox": [573, 760, 152, 60], "slots": {"icon": "img-088"}}
          ]
        }
      ]
    },
    {
      "id": "decor-spark",
      "type": "absolute",
      "bbox": [612, 1930, 96, 96],
      "slots": {"image": "img-150"},
      "reason": "爆炸贴纸压在格子之上，属于出血装饰，必须绝对定位"
    }
  ]
}
```

## 什么时候允许 `absolute`

只有三种情况：

1. 元素**确实与其他元素重叠**（角标、贴纸、光效）；
2. 元素**越过父容器边界**（出血、负偏移）；
3. 元素带旋转/倾斜，流式布局无法还原。

其余一律走 flex/grid。判断"看起来能对齐"就写进流里，不要图省事全用 absolute——那就退化成原产物了。

## 与验收的对应关系

`layout.json` 里的每个 block 都要能被单独验收：

```bash
python scripts/verify_page.py index_optimized.html index_ai.html \
    --max-delta 1 --region <bbox>
```

`bbox` 就是为此准备的：验收时按区块裁区域比对，而不是整页一把梭。整页 diff 只作为兜底，不用于判定单个区块成败。
