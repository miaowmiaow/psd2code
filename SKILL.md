---
name: psd2code-github
description: 将 PSD 设计稿转换为前端代码（HTML / React / Vue）。采用 core(PSD->IR) + targets(多产物) 架构，支持像素级效果还原、布局优化、语义命名与多 target 输出。触发词：psd 转 html、psd 转 react、psd 转 vue、psd to code、设计稿转代码。
---

# psd2code Skill

## 概述
`psd2code` 是一个 PSD -> 前端代码的编译器式工具链。

- `target=html`：生成 HTML/CSS/JS（absolute 原版 + optimized 优化版）
- `target=react`：基于 html 产物二次转换为 Vite + React 18
- `target=vue`：基于 html 产物二次转换为 Vite + Vue 3

详细架构与实现请看 [doc/README.md](./doc/README.md)。

## 何时使用
- 用户提供 `.psd` 并希望快速得到可运行页面代码
- 需要 HTML / React / Vue 任一产物
- 需要保留图层视觉效果并尽量优化结构

## 快速开始

```bash
# 默认 target=html
python3 psd_to_code.py /path/to/file.psd

# 指定 target
python3 psd_to_code.py /path/to/file.psd --target html
python3 psd_to_code.py /path/to/file.psd --target react
python3 psd_to_code.py /path/to/file.psd --target vue
```

### 预览（已自动集成）
从当前版本开始，`--target html` 转换完成后会自动安装预览页（无需手工第二步）：

- 自动复制 `preview.html / preview.css / preview.js`
- 自动更新 `preview.js?v=...`（缓存刷新）
- 自动将 `index.html` 内嵌到 `preview.html`（本地 `file://` 可用）

## 常用参数

- `--target {html,react,vue}`：产物类型（默认 `html`）
- `--css-style {compact,expanded}`：优化版 CSS 风格（默认 `compact`）
- `--no-css-pretty`：关闭 CSS 美化，使用机械渲染
- `--no-smart-merge`：关闭多 url 背景内联合成（便于 1:1 排查）
- `--enable-image-layer-flatten`：启用 ImageLayerFlatten（默认关闭）

## 产物目录

```text
output/<psd_stem>/
├── html/
│   ├── index.html
│   ├── index_optimized.html
│   ├── style.css / style_optimized.css
│   ├── main.js
│   ├── metadata.json
│   ├── layer_map.json
│   ├── _naming_report.md
│   ├── preview.html        # 自动安装
│   ├── preview.css         # 自动安装
│   ├── preview.js          # 自动安装
│   └── images/
├── react/                  # --target react
└── vue/                    # --target vue
```

## 快速排查
优先看这三项：

1. `_naming_report.md`：命名来源与规则命中
2. `layer_map.json`：类名/图层反查
3. `index.html` vs `index_optimized.html`：定位是解析问题还是优化问题

## 开发者指引

- 架构/模块文档： [doc/README.md](./doc/README.md)
- 已知坑位与硬约束： [doc/05-conventions/known-pitfalls.md](./doc/05-conventions/known-pitfalls.md)
- 语义命名模块： [doc/02-modules/semantic.md](./doc/02-modules/semantic.md)

## 依赖

- Python 3.10+
- psd-tools >= 1.17.1
- Pillow >= 10
- numpy
- beautifulsoup4
- pydantic >= 2.0
- pypinyin
