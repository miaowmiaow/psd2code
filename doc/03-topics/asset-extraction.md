# 主题：资源（图片）导出、命名与去重

> **本文解决什么**：回答"images/ 目录里的每张图是怎么来的、叫什么名字、为什么有些图被复用"这三个问题。
> **不讨论什么**：像素渲染算法。

---

## 写盘入口

统一入口：`core/extract/layer_exporter.LayerExporter._save_image_dedup(img, name, depth)`

```python
def _save_image_dedup(self, img, name, depth) -> str:  # 返回相对路径 "images/xxx.png"
    1. 把 img 序列化为 bytes（按 Config.IMAGE_FORMAT）
    2. md5(bytes)
    3. 如果 md5 已在 self._image_hash_map：复用旧路径，dedup_count++
    4. 否则：make_image_filename(name, ..., content_hash=md5, ltype="image")
       生成文件名并写盘
```

## 命名规则（2026-04 起）

`common.utils.make_image_filename(name, max_length, fmt, *, content_hash, ltype)`：

```
<semantic>-<hash6>.<format>
```

- `semantic`：由 `common.semantic.extract_semantic_token` 从图层名抽取的 kebab-case
  短语义词（如 `btn`、`btn-receive`、`candy-big`、`rounded`）。PS 默认名会被过滤、
  走 `ltype` 兜底（`image→img`、`shape→shape`）。
- `hash6`：**图片内容 md5 的前 6 位**。和外层 `_image_hash_map` 的口径一致 →
  PSD 没改、产物名字就不变，**git diff 和 CDN 缓存都友好**。
- 冲突兜底：同 `<semantic>-<hash6>.<ext>` 撞车时自动追加 `-2/-3` 后缀。

### 示例

| 原图层名 | 旧命名（1.0） | 新命名（1.1.0+） |
| -------- | ------------- | ---------------- |
| "圆角矩形 3" | `yuanjiaojuxing_3_7.png` | `rounded-a3f012.png` |
| "立即领取" | `lijilingqu_13.png` | `btn-receive-279914.png` |
| "bg_main" | `bg_main_1.png` | `bg-main-4e8c1d.png` |
| "矢量智能对象"（PS 默认名） | `shiliangzhinengduixiang_14.png` | `img-7b0a12.png` |
| "image (1).png" | `image_(1)_24.png`（含括号 ⚠️） | `img-4e8c1d.png` |
| 空 | `layer_42.png` | `img-abc123.png` |

## 去重原理

- 依据 **图片字节内容** 的 md5，而不是图层名。
- 同一次运行里，多处引用相同内容（如重复 icon）只存一份。
- 不跨运行去重（每次运行 `_image_hash_map` 重新开始）——但由于新命名把 md5 编进
  文件名本身，**重复运行同一 PSD 的产物文件名是完全一致的**（不依赖内存 dedup map）。

## 命名与 IR 的关系

`ImageNode.asset` / `GroupNode.merged_asset` 指向 `AssetRef`：

```
AssetRef.src = "images/btn-receive-279914.png"    # 相对输出根
AssetRef.absolute_path = output_dir / "images/btn-receive-279914.png"
AssetRef.format = "png"
```

codegen 只读 `AssetRef.src`，不关心它怎么来的。

## 可配置项

在 `config/config.py`：

```python
Config.IMAGE_FORMAT = 'png'         # 'png' | 'jpg' | 'webp'
Config.MAX_FILENAME_LENGTH = 50     # 实际产物通常 ≤ 20 字符
Config.CROP_OVERFLOW_IMAGES = True  # 是否裁掉超出画布的像素
```

切换到 `jpg`/`webp` 需要重跑 baseline diff（文件名后缀会变，HTML 中引用也会变）。

## 常见问题

- **"为什么我看到 `♻️ xxx (复用 images/yyy.png)`？"**  —— 去重命中。
- **"我修改了图层但文件名没变"** —— 正常：如果你的修改没影响像素（纯文本改名），
  md5 相同 → 命中缓存，且新命名格式里的 hash6 本身就是内容 md5 → 文件名自然不变。
- **"我希望用 sha1 / 更短的 hash"** —— 改 `_save_image_dedup` 里 `md5 = hashlib.md5(...)`
  那一行；`make_image_filename` 取前 6 位 hex 即可。
- **"业务里给图层起的新名字（比如'活动封面'）没被识别"** —— 按需要加到
  `common/semantic.py::_KEYWORDS` 列表（位置按优先级，更具体的放前面）。

