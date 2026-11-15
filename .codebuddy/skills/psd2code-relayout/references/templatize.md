# 重复单元模板化

产物里最普遍的"不规范"不是布局，而是**重复单元被复制成 N 份独立 DOM + N 套 CSS**。这是最值得做、也最容易做到零像素代价的改造。

## 先确认同构

把每个候选实例的 CSS 规则做归一化（去掉 `z-index` 与 `background`），比较几何签名：

```python
def norm(body):
    body = re.sub(r'z-index:\s*-?\d+;?', '', body)
    body = re.sub(r'background:[^;]+;?', '', body)
    return ' '.join(body.split()).strip('; ')
```

签名相同 = 位置、尺寸、层级结构都一致，只有图片/文案/层级号不同 → 可合并。

实测（南瓜道具格）：

```
15 格 → position: absolute; left: 0px; top: 114px; width: 148px; height: 88px
 1 格 → position: absolute; left: -9px; top: 27px; width: 52px; height: 52px
 1 格 → position: absolute; left: 67px; top: 19px; width: 60px; height: 60px
```

15 : 2，主模板覆盖绝大多数，另两个单独处理。

## 结构收敛

原结构（每格 7 个节点 / 6 条规则）：

```html
<div class="prop-bg-2-k">
  <div class="rounded-N"></div>       <!-- 底板 -->
  <div class="btn-receive-N"></div>   <!-- 领取条 -->
  <div class="group-N">               <!-- 标签组 -->
    <div class="rect-N"></div>        <!-- 标签条 -->
    <div class="text-N"><span>10</span></div>
    <div class="candy-N"></div>       <!-- 图标 -->
  </div>
</div>
```

模板化后（每格 3 个节点 / 1 套公共规则）：

```html
<div class="prop-card"
     style="--z:73; --card-bg:url(...); --btn-bg:url(...);
            --badge-bg:url(...); --candy-bg:url(...)">
  <div class="prop-card__btn"></div>
  <div class="prop-card__badge"><span class="prop-card__count">10</span></div>
</div>
```

```css
.prop-card            { position:absolute; left:0; top:114px; width:148px; height:88px;
                        z-index: var(--z); isolation: isolate; }
.prop-card::before    { /* 底板 */ content:''; position:absolute; left:-8px; top:-8px;
                        width:146px; height:86px; z-index:1;
                        background: var(--card-bg) no-repeat; }
.prop-card__btn       { /* 领取条 */ position:absolute; left:37px; top:34px;
                        width:55px; height:30px; background: var(--btn-bg) no-repeat; }
.prop-card__badge     { /* 标签条 */ position:absolute; left:33px; top:5px;
                        width:68px; height:30px;
                        background: var(--badge-bg) no-repeat 0px 4px; }
.prop-card__badge::before { /* 图标 */ content:''; width:28px; height:28px;
                        background: var(--candy-bg) no-repeat; }
.prop-card__count     { position:absolute; left:29px; top:10px; }
```

要点：

- 纯装饰层用 `::before` 承担，省掉一个节点；
- 图片走 CSS 变量，同一套规则服务 N 个实例；
- `z-index` 用 `--z` 传入（原实例的层号各不相同）；
- `isolation: isolate` 把内部层级关起来，避免与外层其它元素互相干扰。

## 三个必踩的坑

### 1. 子元素坐标是相对各自的父级

`text` 在 `group` 里是 `left:29px`，`group` 在卡片里是 `left:33px`。把 `text` 提到卡片下时，必须写成 `33 + 29 = 62px`；否则数字会跑到图标左边。

模板里更稳的做法：**保持原层级**，把 `text` 放进 `badge` 内部，坐标就还是 `29px`。

### 2. 背景图偏移要跟着搬

原 `rect` 在 `group` 内的位置是 `(0, 4)`。若把 `rect` 的图直接作为 `badge` 的 `background`，图像会从 `(0,0)` 开始画 → 整体上移 4px，实测 `maxΔ=70`。

必须写 `background: var(--badge-bg) no-repeat 0px 4px;`。

### 3. 文本可能在深层 div 里，删除前必须先摘出来

V1 稿的奖励标签是 4 层结构：

```
.points ▸ rect-N ▸ points-N ▸ <span>+10积分</span>
```

按"清空所有子 div"的方式合并，`<span>` 会随它的父 div 一起被删掉——实测 22 个标签的文字全部消失，整页 0.26% 差异。

正确顺序：

```python
span = el.find("span")
if span is not None:
    span.extract()          # 先摘出来
for d in list(el.find_all("div")):
    d.decompose()
if span is not None:
    span["class"] = ["reward-tag__text"]
    el.append(span)
```

### 4. 背景条能否并入容器：先比 z-index

两种写法都可能，但前提是**子层的 z-index 不高于文本**：

| 场景 | 结论 |
|---|---|
| 文本容器也带 z-index（如 `points-N z=306` > `rect z=209`） | 改成容器 `background` 安全，实测 `maxΔ=0` |
| 文本是静态节点（`z-index: auto`），背景条带 z（如 `btn rect z=285`） | **不要合并**，实测 0.14% 差异 |

第二种情况里，背景条原本绘制在文字之上（把字压住），改成容器背景会让文字浮上来。这是真实视觉差异，不该"顺手修正"。

写法选择：子层有非零 `left/top` 偏移时用 `::before`（几何能原样搬过去）；在 `(0,0)` 且尺寸一致时直接用 `background` 更省。

### 5. 伪元素的 z-index 出不了父元素

`::before` 的 `z-index` 只在所属元素的层叠上下文内比较。如果某个装饰需要压到**父元素之外**的兄弟上，就不能用伪元素，得留成独立节点。

## 验收

先按单个区域验，再整页：

```bash
python scripts/verify_page.py index_optimized.html index_ai.html \
    --max-delta 1 --region 20,760,520,980
python scripts/verify_page.py index_optimized.html index_ai.html --max-delta 1
```

模板化是**只改承载方式、不改几何**，所以 `maxΔ=0` 是可达的——如果出现非零差异，说明坐标或背景偏移没搬对，去查而不是放宽阈值。

## 实测收益（南瓜道具格，15 个实例）

| 指标 | 改前 | 改后 | 变化 |
|---|---:|---:|---:|
| `div` 节点 | 521 | 461 | **−60** |
| CSS 规则 | 464 | 378 | **−86** |
| CSS 体积 | 73.8 KB | 60.3 KB | **−18%** |
| 整页像素差 | — | 0 | 零差异 |

## 适用范围

适合：格子墙、列表项、按钮组、标签组——凡是"同一套样式复制 N 次"的都适用。

不适合：几何不一致（每项位置/尺寸都不同）、或每项内部结构差异大的复用。硬合会让模板里塞满 `if`，反而不如分开。
