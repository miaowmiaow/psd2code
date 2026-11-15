"""默认输出目录推导（`Config.OUTPUT_BASE_DIR`）。

历史问题：路径由 `config.py` 的 `__file__` 上溯三层得到。源码/可编辑安装下
得到 `<repo>/output`（符合预期），但 **wheel 安装** 下会得到
`<python>/lib/.../output`，于是产物被写进 Python 安装目录。

这里锁住两条：源码布局 → 仓库 output；site-packages 布局 → 当前工作目录。
"""

from __future__ import annotations

from pathlib import Path

from config.config import _resolve_output_base


class TestResolveOutputBase:
    def test_source_layout_uses_repo_output(self):
        repo = Path("/Users/me/projects/psd2code-github")
        assert _resolve_output_base(repo, Path("/tmp/elsewhere")) == str(repo / "output")

    def test_wheel_install_falls_back_to_cwd(self):
        venv = Path("/opt/venv/lib/python3.12/site-packages")
        assert _resolve_output_base(venv, Path("/tmp/work")) == str(
            Path("/tmp/work/output")
        )

    def test_dist_packages_is_also_detected(self):
        dist = Path("/usr/lib/python3/dist-packages")
        assert _resolve_output_base(dist, Path("/tmp/work")) == str(
            Path("/tmp/work/output")
        )

    def test_current_layout_is_repo_output(self):
        """仓库内运行时，默认输出必须还是 `<repo>/output`（不改历史行为）。"""
        from config.config import Config

        expected = Path(__file__).resolve().parent.parent / "output"
        assert Path(Config.OUTPUT_BASE_DIR) == expected
