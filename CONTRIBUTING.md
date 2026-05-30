# Contributing to psd2code

感谢你愿意为 psd2code 做贡献！🎉  Thanks for considering contributing to psd2code!

本文档说明如何在本仓库中开展开发工作、提交 Issue 和 PR。

---

## 开发环境 / Dev setup

```bash
git clone https://github.com/miaowmiaow/psd2code.git
cd psd2code

python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 跑测试
pytest -q

# 跑一个端到端冒烟（替换为你本地的 psd 文件）
python3 psd_to_code.py /path/to/your.psd --target html
```

要求 Python ≥ 3.10。

---

## 代码风格 / Coding style

- 遵循 [doc/05-conventions/coding-style.md](./doc/05-conventions/coding-style.md)
- 公共 API 必须有 docstring；非显而易见的算法/边界情况必须有注释
- 优先类型注解（`from __future__ import annotations` + PEP 604 联合类型）
- 注释和提交信息可中英任选，但 **代码标识符必须使用英文**

---

## 提交流程 / Workflow

1. **Fork → 新建分支**：`git checkout -b feat/<short-desc>` 或 `fix/<short-desc>`
2. **小步提交**：每个 commit 解决一件事；commit message 建议 [Conventional Commits](https://www.conventionalcommits.org/)：
   - `feat(layout-optimizer): support 2-col grid detection`
   - `fix(html-target): correct font-size when transform.scale != 1`
   - `docs(readme): add english version`
3. **本地自检**：
   ```bash
   pytest -q
   python -m compileall -q scripts psd_to_code.py
   ```
4. **发起 PR**：填写 PR 模板，关联对应 Issue。

---

## 改动 IR / 公共契约要谨慎

`scripts/core/ir/` 下的 pydantic 模型是 `core` 与所有 `targets` 之间的契约。
如果你改了字段，**必须**：

- 更新 [doc/03-topics/ir-contract.md](./doc/03-topics/ir-contract.md)
- 更新所有 target（html / react / vue）的相关代码
- 添加迁移说明到 PR 描述

---

## 我能从哪里入手？/ Where to start?

- 标签 [`good first issue`](https://github.com/miaowmiaow/psd2code/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)：适合新人
- 标签 [`help wanted`](https://github.com/miaowmiaow/psd2code/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22)：希望社区帮忙
- 高价值方向（按贡献回报排序）：
  1. 新 target：Tailwind / 微信小程序 / 抖音小程序
  2. 新输入：Figma / Sketch → 同一个 IR
  3. layout-optimizer 增强：2D Grid 识别、equal-spacing 自动 `gap`
  4. effects 渲染补全（`gradient overlay` 等）
  5. 性能优化（大型 PSD 的并行化）

---

## 行为准则 / Code of Conduct

请保持友善与尊重。任何形式的骚扰、歧视或人身攻击都不被允许。
Be kind and respectful. No harassment, discrimination or personal attacks of any kind.

---

## License

Contributions you make are licensed under the [MIT License](./LICENSE) of this project.
