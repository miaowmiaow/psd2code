"""端到端冒烟测试：真实 PSD 文件 → HTML / React 产物。

为什么需要这一层（其余测试都是模块级单测）：
- 单测覆盖不到 ``psd-tools → LayerExporter → IR → codegen → LayoutOptimizer``
  这条真正的主链路，渲染/导出/落盘任何一环坏掉，单测都还是绿的；
- CI 里没有 PSD 样本，历史上"改坏了渲染"只能靠人工跑设计稿发现。

做法：用 ``tests/psd_factory.py`` 现场生成一份最小合法 PSD（40×30，1 组 3 层），
跑完整 CLI，然后：
1. 断言产物清单与关键 sidecar（layer_map / class_alias_map / naming report）；
2. 用「归一化快照」锁住 ``index.html`` / ``index_optimized.html`` /
   ``style_optimized.css`` 的结构（图片名里的内容哈希被归一化，避免 PNG
   编码库版本差异导致快照漂移）；
3. 断言 ``style_optimized.css?v=<md5>`` 与最终 CSS 内容一致（缓存击穿回归）。

快照更新方式：

    P2C_UPDATE_SNAPSHOTS=1 pytest tests/test_e2e_smoke.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from .psd_factory import CANVAS_H, CANVAS_W, build_minimal_psd

SNAPSHOT_DIR = Path(__file__).parent / "snapshots" / "e2e_minimal"

# 归一化：图片内容哈希（Pillow / zlib 版本不同会让 PNG 字节不同）+ 缓存版本号
_IMG_HASH_RE = re.compile(r"-[0-9a-f]{6}\.png")
_VER_RE = re.compile(r"\?v=[0-9a-f]{6,}")


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = _IMG_HASH_RE.sub("-<hash>.png", text)
    text = _VER_RE.sub("?v=<ver>", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip() + "\n"


def _run_cli(psd_path: Path, out_dir: Path, *extra: str) -> int:
    import psd_to_code

    argv = [str(psd_path), "--output", str(out_dir), *extra]
    return psd_to_code.main(argv)


@pytest.fixture()
def minimal_psd(tmp_path: Path) -> Path:
    return build_minimal_psd(tmp_path / "mini.psd")


@pytest.fixture()
def html_out(minimal_psd: Path, tmp_path: Path) -> Path:
    """跑一次 --target html，返回 html/ 产物目录。"""
    out_base = tmp_path / "out"
    assert _run_cli(minimal_psd, out_base) == 0
    return out_base / "mini" / "html"


# ---------------------------------------------------------------------------
# 产物完整性与 sidecar
# ---------------------------------------------------------------------------

EXPECTED_FILES = (
    "index.html",
    "index_optimized.html",
    "style.css",
    "style_optimized.css",
    "main.js",
    "metadata.json",
    "layer_map.json",
    "class_alias_map.json",
    "_naming_report.md",
    "_mapping_report.md",
    "_image_index.md",
    "README.md",
    "preview.html",
    "_layout_stats.json",
)


class TestHtmlTargetEndToEnd:
    def test_produces_expected_files(self, html_out: Path):
        missing = [f for f in EXPECTED_FILES if not (html_out / f).exists()]
        assert missing == [], f"缺失产物: {missing}"

    def test_no_optimizer_step_silently_failed(self, html_out: Path):
        """容错模式会吞掉 transformer 异常，只留 warning —— 这里必须为 0。

        没有这条断言时，某个优化器步骤崩掉仍然可能产出"看起来正常"的
        页面，快照测试也会照常通过（回归被静默放行）。
        """
        stats = json.loads((html_out / "_layout_stats.json").read_text(encoding="utf-8"))
        assert stats["failures"] == [], f"优化器有步骤失败: {stats['failures']}"

    def test_version_sources_agree(self, html_out: Path):
        """产物格式版本只有一个真源：codegen.version。config 是只读镜像。"""
        from config import __version__ as config_version
        from targets.html.codegen.version import __version__ as codegen_version

        meta = json.loads((html_out / "metadata.json").read_text(encoding="utf-8"))
        assert meta["version"] == codegen_version
        assert config_version == codegen_version

    def test_responsive_scaling_is_valid_css_and_js_driven(self, html_out: Path):
        """回归：`scale(calc(100vw / W))` 的 calc 结果是长度，浏览器会丢弃整条声明。

        正确实现改为 main.js 里的 fitCanvas()（见 html_builder 注释）。
        """
        css = (html_out / "style_optimized.css").read_text(encoding="utf-8")
        assert "scale(calc(" not in css, "生成的 CSS 里不能出现无效的 scale(calc(...))"
        assert "scale(calc(" not in (html_out / "style.css").read_text(encoding="utf-8")

        js = (html_out / "main.js").read_text(encoding="utf-8")
        assert "fitCanvas" in js
        assert f"DESIGN_WIDTH = {CANVAS_W}" in js
        assert f"DESIGN_HEIGHT = {CANVAS_H}" in js

    def test_exports_one_png_per_visible_layer(self, html_out: Path):
        pngs = sorted((html_out / "images").glob("*.png"))
        assert len(pngs) == 3, [p.name for p in pngs]

    def test_semantic_class_names_from_chinese_layers(self, html_out: Path):
        """中文图层名 → 语义类名：背景→bg / 图标→icon / 卡片→card。"""
        layer_map = json.loads((html_out / "layer_map.json").read_text(encoding="utf-8"))
        by_class = layer_map["by_class"]
        assert {"bg", "icon", "card"} <= set(by_class)
        assert by_class["bg"]["name"] == "背景"
        assert by_class["icon"]["name"] == "图标"
        assert by_class["card"]["type"] == "group"

    def test_group_rendered_as_container(self, html_out: Path):
        html = (html_out / "index.html").read_text(encoding="utf-8")
        assert 'id="canvas"' in html
        assert "layer-group" in html  # 组没有被拍平
        assert f'width: {CANVAS_W}px' in (html_out / "style_optimized.css").read_text(encoding="utf-8")
        assert f"height: {CANVAS_H}px" in (html_out / "style_optimized.css").read_text(encoding="utf-8")

    def test_optimized_html_strips_dev_metadata(self, html_out: Path):
        """交付版不应残留调试属性 / PSD 图层 id。"""
        html = (html_out / "index_optimized.html").read_text(encoding="utf-8")
        assert "data-name=" not in html
        assert 'id="layer-' not in html
        assert 'id="group-' not in html

    def test_css_cache_bust_matches_final_css(self, html_out: Path):
        """回归：CSS 在写盘后又被改写时，?v= 版本号会与内容不一致。"""
        html = (html_out / "index_optimized.html").read_text(encoding="utf-8")
        css = (html_out / "style_optimized.css").read_text(encoding="utf-8")
        m = re.search(r"style_optimized\.css\?v=([0-9a-f]{6,})", html)
        assert m, "index_optimized.html 缺少 style_optimized.css?v=... 缓存版本号"
        assert m.group(1) == hashlib.md5(css.encode("utf-8")).hexdigest()[: len(m.group(1))]


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------

SNAPSHOT_FILES = ("index.html", "index_optimized.html", "style_optimized.css")


class TestOutputSnapshots:
    @pytest.mark.parametrize("filename", SNAPSHOT_FILES)
    def test_snapshot(self, html_out: Path, filename: str):
        actual = _normalize((html_out / filename).read_text(encoding="utf-8"))
        snapshot = SNAPSHOT_DIR / filename

        if os.environ.get("P2C_UPDATE_SNAPSHOTS") == "1":
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(actual, encoding="utf-8")
            pytest.skip(f"已更新快照: {snapshot}")

        assert snapshot.exists(), (
            f"快照缺失 {snapshot}；首次生成请执行 "
            f"P2C_UPDATE_SNAPSHOTS=1 pytest {Path(__file__).name}"
        )
        expected = snapshot.read_text(encoding="utf-8")
        if actual != expected:
            # 给出可读的差异，避免 CI 上只看到 "assert A == B"
            import difflib

            diff = "".join(
                difflib.unified_diff(
                    expected.splitlines(keepends=True),
                    actual.splitlines(keepends=True),
                    fromfile=f"snapshot/{filename}",
                    tofile=f"actual/{filename}",
                )
            )
            pytest.fail(f"产物与快照不一致：\n{diff}")


# ---------------------------------------------------------------------------
# CLI 变体：strict / CSS 风格 / 关闭 smart-merge
# ---------------------------------------------------------------------------

class TestCliVariants:
    def test_strict_mode_reports_zero_failures(self, minimal_psd: Path, tmp_path: Path):
        """`--strict` 必须能从 CLI 打开（此前只存在于 API，CI 无法启用）。"""
        out_base = tmp_path / "strict"
        assert _run_cli(minimal_psd, out_base, "--strict") == 0
        stats = json.loads(
            (out_base / "mini" / "html" / "_layout_stats.json").read_text(encoding="utf-8")
        )
        assert stats["failures"] == []

    @pytest.mark.parametrize(
        "extra",
        [("--css-style", "expanded"), ("--no-css-pretty",), ("--no-smart-merge",)],
        ids=["css-expanded", "no-css-pretty", "no-smart-merge"],
    )
    def test_output_variants_stay_healthy(
        self, minimal_psd: Path, tmp_path: Path, extra: tuple[str, ...]
    ):
        out_base = tmp_path / "variant"
        assert _run_cli(minimal_psd, out_base, *extra) == 0

        html_dir = out_base / "mini" / "html"
        css = (html_dir / "style_optimized.css").read_text(encoding="utf-8")
        assert "#canvas" in css and ".bg" in css
        stats = json.loads((html_dir / "_layout_stats.json").read_text(encoding="utf-8"))
        assert stats["failures"] == []


# ---------------------------------------------------------------------------
# 多 target：React / Vue 复用 HTML 前缀链
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("target", "entry"),
    [("react", "react/src/App.jsx"), ("vue", "vue/src/App.vue")],
)
def test_framework_target_reuses_html_prefix(minimal_psd: Path, tmp_path: Path, target: str, entry: str):
    """React / Vue 必须共享 HTML 前缀链（含 BgNesting / Prune / LayoutOptimize）。"""
    out_base = tmp_path / f"out_{target}"
    assert _run_cli(minimal_psd, out_base, "--target", target) == 0

    project = out_base / "mini"
    # 1) HTML 中间产物存在，且确实是「优化后」版本（证明前缀链完整跑过）
    assert (project / "html" / "index_optimized.html").exists()
    assert (project / "html" / "style_optimized.css").exists()
    # 2) 目标框架文件存在
    assert (project / entry).exists()
    assert (project / target / "package.json").exists()
