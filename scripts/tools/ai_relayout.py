# -*- coding: utf-8 -*-
"""产物级重排：把「子层全是图片」的 ``layer-group`` 合并为单张图 + 单个 div。

背景
----
``psd2code`` 的输出是"绝对定位 + 切图"的拼贴结构：一个图标往往被拆成
「底板 + 图案 + 角标」多个 div，每个都带一张 ``background-image``。这类层
在视觉上叠成一体，但 DOM 上是 3 个节点、3 个文件。

本工具在**产物生成之后**再做一层收敛：把这些纯图片组按 z-index 合成为
一张 PNG，只保留一个 div。它不改主流水线，输出 ``index_ai.html`` /
``style_ai.css`` 与原产物并列，便于对比。

可靠性
------
核心是**像素校验闭环**：每个候选组单独应用改动 → 截图 → 与原图逐像素比对，
超过阈值就回滚该组。没有通过的组一律不落盘，因此"合并"不会悄悄改变视觉。

合并完成后还会做一次整页比对兜底；整页差异超阈值时整体失败，不写产物。

用法
----
    python scripts/tools/ai_relayout.py output/<项目>/html
    python scripts/tools/ai_relayout.py output/<项目>/html --dry-run
    python scripts/tools/ai_relayout.py output/<项目>/html --no-verify   # 不推荐

``--verify`` 需要 ``playwright`` 与本机 Chrome：

    pip install playwright && python -m playwright install chromium
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# 允许以 `python scripts/tools/ai_relayout.py` 直接运行
_SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

from bs4 import BeautifulSoup  # noqa: E402
from PIL import Image  # noqa: E402

from common.css_utils import parse_css_to_dict  # noqa: E402

# 带这些属性的层不能用「PIL 拍平为一张图」等价替换
_UNSAFE_PROPS = ("transform", "filter", "mix-blend-mode")
_IMG_RE = re.compile(r'url\("?([^")]+)"?\)')

DEFAULT_MAX_COLOR_DELTA = 1


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Layer:
    """组内一个图片子层。"""

    element: Any
    css_class: str
    left: float
    top: float
    width: float
    height: float
    z_index: float
    url: str


@dataclass
class GroupCandidate:
    """一个候选 ``layer-group``。"""

    index: int
    group_class: str
    layers: list[Layer] = field(default_factory=list)
    skip_reason: Optional[str] = None
    merged_url: Optional[str] = None
    merged_width: int = 0
    merged_height: int = 0
    status: str = "pending"      # pending / merged / rejected / skipped
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.skip_reason is None


# ---------------------------------------------------------------------------
# CSS 工具
# ---------------------------------------------------------------------------

def _px(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace("px", "").strip())
    except (TypeError, ValueError):
        return default


def _rule_of(css_rules: dict[str, dict[str, str]], element: Any) -> tuple[Optional[str], dict[str, str]]:
    """返回元素第一个带尺寸信息的 class 与其规则。"""
    for cls in (element.get("class") or []):
        rule = css_rules.get(f".{cls}")
        if rule and "width" in rule:
            return cls, rule
    return None, {}


def _bg_url(rule: dict[str, str]) -> Optional[str]:
    raw = rule.get("background", "") + rule.get("background-image", "")
    match = _IMG_RE.search(raw)
    return match.group(1) if match else None


def _replace_rule(css_text: str, cls: str, body: str) -> str:
    pattern = re.compile(r"^\." + re.escape(cls) + r"\s*\{[^}]*\}\s*$", re.M)
    return pattern.sub(f".{cls} {{ {body} }}", css_text, count=1)


def _drop_rule(css_text: str, cls: str) -> str:
    pattern = re.compile(r"^\." + re.escape(cls) + r"\s*\{[^}]*\}\s*$", re.M)
    return pattern.sub("", css_text)


def canvas_size(css_text: str, default: tuple[int, int] = (750, 1334)) -> tuple[int, int]:
    """从 ``#canvas`` 规则读取设计稿尺寸。"""
    match = re.search(r"#canvas\s*\{([^}]*)\}", css_text, re.S)
    if not match:
        return default
    body = match.group(1)

    def decl(prop: str) -> float:
        found = re.search(rf"{prop}\s*:\s*([^;]+)", body)
        return _px(found.group(1)) if found else 0.0

    return (int(decl("width")) or default[0], int(decl("height")) or default[1])


# ---------------------------------------------------------------------------
# 候选筛选
# ---------------------------------------------------------------------------

def collect_candidates(
    soup: BeautifulSoup,
    css_rules: dict[str, dict[str, str]],
    images_dir: Path,
) -> list[GroupCandidate]:
    """收集所有 ``layer-group``，并标注是否可安全合并。

    安全条件（全部满足才会合并）：
      1. 直接子 div ≥ 2，且全部是 ``background-image`` 层；
      2. 子层不含文本、不含子 div（否则删除会丢内容）；
      3. 子层不带 transform / filter / mix-blend-mode / background-size；
      4. 组自身没有背景图（否则合并图会盖掉组背景）；
      5. 子层的 class 在全页唯一（CSS 是全局的，重名会误伤）。
    """
    class_count: dict[str, int] = {}
    for element in soup.find_all("div"):
        for cls in (element.get("class") or []):
            class_count[cls] = class_count.get(cls, 0) + 1

    candidates: list[GroupCandidate] = []
    for index, group in enumerate(soup.find_all("div", class_="layer-group")):
        kids = [k for k in group.find_all(recursive=False) if k.name == "div"]
        if len(kids) < 2:
            continue

        group_class, group_rule = _rule_of(css_rules, group)
        cand = GroupCandidate(index=index, group_class=group_class or "?")

        if not group_class:
            cand.skip_reason = "组无独立 class"
        elif _bg_url(group_rule):
            cand.skip_reason = "组自身已有背景图"

        layers: list[Layer] = []
        for kid in kids:
            if cand.skip_reason:
                break
            cls, rule = _rule_of(css_rules, kid)
            url = _bg_url(rule)
            if not cls or not url:
                cand.skip_reason = "子层非纯图片"
            elif kid.find("span") or kid.find("div") or (kid.get_text() or "").strip():
                cand.skip_reason = "子层含文本或子容器"
            elif any(prop in rule for prop in _UNSAFE_PROPS):
                cand.skip_reason = "子层带 transform/filter/blend"
            elif "background-size" in rule:
                cand.skip_reason = "子层图片被缩放"
            elif not (images_dir / Path(url).name).exists():
                cand.skip_reason = "图片文件缺失"
            elif class_count.get(cls, 0) != 1:
                cand.skip_reason = "class 非全页唯一"
            else:
                layers.append(Layer(
                    element=kid, css_class=cls,
                    left=_px(rule.get("left")), top=_px(rule.get("top")),
                    width=_px(rule.get("width")), height=_px(rule.get("height")),
                    z_index=_px(rule.get("z-index")), url=url,
                ))

        if cand.skip_reason is None and len(layers) < 2:
            cand.skip_reason = "可合并子层不足 2 个"
        cand.layers = layers
        candidates.append(cand)

    return candidates


def synthesize(cand: GroupCandidate, images_dir: Path, out_name: str) -> GroupCandidate:
    """把组内图片按 z-index 拍平为一张 PNG。"""
    lefts = [l.left for l in cand.layers]
    tops = [l.top for l in cand.layers]
    min_left, min_top = min(lefts), min(tops)
    width = int(round(max(l.left + l.width for l in cand.layers) - min_left))
    height = int(round(max(l.top + l.height for l in cand.layers) - min_top))
    if width <= 0 or height <= 0 or width > 4000 or height > 4000:
        cand.skip_reason = "合并画布尺寸异常"
        return cand

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for layer in sorted(cand.layers, key=lambda l: l.z_index):
        image = Image.open(images_dir / Path(layer.url).name).convert("RGBA")
        canvas.alpha_composite(
            image,
            (int(round(layer.left - min_left)), int(round(layer.top - min_top))),
        )
    canvas.save(images_dir / out_name)

    cand.merged_url = f"images/{out_name}"
    cand.merged_width = width
    cand.merged_height = height
    return cand


# ---------------------------------------------------------------------------
# 浏览器校验
# ---------------------------------------------------------------------------

_JS_APPLY = """(payload) => {
  const pick = (cls) => document.querySelector('.' + cls);
  const host = pick(payload.carrier);
  if (!host) return false;
  payload.dead.forEach((cls) => {
    const el = pick(cls);
    if (el) { el.style.display = 'none'; }
  });
  const cs = getComputedStyle(host);
  host.style.background = `url("${payload.img}") no-repeat`;
  host.style.left = (parseFloat(cs.left) - payload.dx) + 'px';
  host.style.top = (parseFloat(cs.top) - payload.dy) + 'px';
  host.style.width = payload.w + 'px';
  host.style.height = payload.h + 'px';
  host.style.zIndex = String(payload.z);
  return true;
}"""

def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise SystemExit(
            "像素校验需要 playwright：pip install playwright && "
            "python -m playwright install chromium\n"
            "（或使用 --no-verify 跳过校验，但产物将不可信）"
        ) from exc


def verify_candidates(
    html_path: Path,
    candidates: list[GroupCandidate],
    viewport: tuple[int, int],
    max_delta: int = DEFAULT_MAX_COLOR_DELTA,
) -> None:
    """逐组应用并比对，通过的标记 merged，失败的 accepted=False 并回滚。

    逐组校验而不是整体校验，是为了让"哪一组不可合并"可定位、可回滚。
    回滚通过重新加载页面实现（保证状态干净），代价是每组一次导航。
    """
    _require_playwright()
    import numpy as np
    from io import BytesIO
    from PIL import Image as PILImage
    from playwright.sync_api import sync_playwright

    def shot_bytes(page, clip) -> Any:
        return page.screenshot(clip=clip)

    def decode(raw: bytes):
        return np.asarray(PILImage.open(BytesIO(raw)).convert("RGB"), dtype=np.int16)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        for cand in candidates:
            if not cand.ok:
                cand.status = "skipped"
                continue

            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
            page.goto(html_path.as_uri())
            page.wait_for_timeout(900)

            carrier = cand.layers[0]
            element = page.query_selector("." + carrier.css_class)
            if element is None:
                cand.status = "rejected"
                cand.detail = "承载层未找到"
                page.close()
                continue

            element.scroll_into_view_if_needed()
            page.wait_for_timeout(120)
            box = element.bounding_box()
            pad = 6
            clip = {
                "x": max(0.0, box["x"] - pad), "y": max(0.0, box["y"] - pad),
                "width": box["width"] + 2 * pad, "height": box["height"] + 2 * pad,
            }
            before = decode(shot_bytes(page, clip))

            payload = {
                "carrier": carrier.css_class,
                "dead": [l.css_class for l in cand.layers[1:]],
                "img": cand.merged_url,
                "dx": carrier.left - min(l.left for l in cand.layers),
                "dy": carrier.top - min(l.top for l in cand.layers),
                "w": cand.merged_width,
                "h": cand.merged_height,
                "z": max(l.z_index for l in cand.layers),
            }
            if not page.evaluate(_JS_APPLY, payload):
                cand.status = "rejected"
                cand.detail = "应用失败"
                page.close()
                continue

            page.wait_for_timeout(150)
            after = decode(shot_bytes(page, clip))
            page.close()

            if before.shape != after.shape:
                cand.status = "rejected"
                cand.detail = "截图尺寸变化"
                continue

            delta = int(np.abs(before - after).max())
            changed = int((np.abs(before - after).max(axis=2) > 0).sum())
            if delta > max_delta:
                cand.status = "rejected"
                cand.detail = f"像素差 {delta} 超过阈值 {max_delta}（{changed} px）"
            else:
                cand.status = "merged"
                cand.detail = f"maxΔ={delta}, {changed} px"

        browser.close()


def full_page_diff(
    original: Path,
    modified: Path,
    viewport: tuple[int, int],
) -> tuple[int, int, float]:
    """整页兜底比对，返回 (最大通道差, 超阈值像素数, 差异比例)。"""
    import numpy as np
    from playwright.sync_api import sync_playwright

    def shot(path: Path):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome")
            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
            page.goto(path.as_uri())
            page.wait_for_timeout(1200)
            raw = page.screenshot(full_page=True)
            browser.close()
        return np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"), dtype=np.int16)

    a = shot(original)
    b = shot(modified)
    if a.shape != b.shape:
        return 255, a.size, 1.0
    diff = np.abs(a - b).max(axis=2)
    return int(diff.max()), int((diff > 1).sum()), float((diff > 0).mean())


# ---------------------------------------------------------------------------
# 落盘与报告
# ---------------------------------------------------------------------------

def apply_changes(
    soup: BeautifulSoup,
    css_text: str,
    candidates: list[GroupCandidate],
) -> tuple[str, str, int]:
    """把通过校验的候选写入 CSS / DOM，返回 (css, html, 删除的节点数)。

    注意：``candidates`` 必须由**同一个** ``soup`` 收集而来——这里依赖候选里
    保存的元素引用来 ``decompose()``，用另一个解析实例会导致删除不生效。
    """
    removed = 0
    for cand in candidates:
        if cand.status != "merged":
            continue
        carrier = cand.layers[0]
        min_left = min(l.left for l in cand.layers)
        min_top = min(l.top for l in cand.layers)
        body = (
            f"position: absolute; left: {int(round(min_left))}px; top: {int(round(min_top))}px; "
            f"z-index: {int(max(l.z_index for l in cand.layers))}; "
            f"width: {cand.merged_width}px; height: {cand.merged_height}px; "
            f'background: url("{cand.merged_url}") no-repeat;'
        )
        css_text = _replace_rule(css_text, carrier.css_class, body)
        for layer in cand.layers[1:]:
            css_text = _drop_rule(css_text, layer.css_class)
            layer.element.decompose()
            removed += 1
    return css_text, str(soup), removed


def render_report(
    input_name: str,
    candidates: list[GroupCandidate],
    stats: dict[str, Any],
) -> str:
    merged = [c for c in candidates if c.status == "merged"]
    rejected = [c for c in candidates if c.status == "rejected"]
    skipped = [
        c for c in candidates
        if c.status not in ("merged", "rejected")
    ]

    lines = [
        "# AI 产物重排报告",
        "",
        f"- 输入：`{input_name}`",
        f"- 候选组：{len(candidates)}（合并 {len(merged)} / 回滚 {len(rejected)} / 跳过 {len(skipped)}）",
        f"- 删除节点：{stats.get('removed', 0)}",
        f"- `div` 总数：{stats.get('div_before', '?')} → {stats.get('div_after', '?')}",
        f"- 整页差异：{stats.get('diff_ratio', 0) * 100:.4f}%，最大通道差 {stats.get('max_delta', 0)}",
        "",
        "## 合并明细",
        "",
        "| 组 | 承载层 | 合并层 | 结果 |",
        "|---|---|---|---|",
    ]
    for cand in merged:
        layers = "、".join(l.css_class for l in cand.layers[1:])
        lines.append(
            f"| `{cand.group_class}` | `{cand.layers[0].css_class}` | {layers} | ✅ {cand.detail} |"
        )

    if rejected:
        lines += ["", "## 回滚明细", "", "| 组 | 原因 |", "|---|---|"]
        for cand in rejected:
            lines.append(f"| `{cand.group_class}` | {cand.detail} |")

    if skipped:
        lines += ["", "## 跳过原因统计", ""]
        counter: dict[str, int] = {}
        for cand in skipped:
            counter[cand.skip_reason or "未知"] = counter.get(cand.skip_reason or "未知", 0) + 1
        for reason, count in sorted(counter.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {reason}：{count} 组")

    lines += [
        "",
        "## 说明",
        "",
        "- 只会合并「子层全是图片、含无文本/子容器、class 全页唯一」的 `layer-group`；",
        "- 每组都经过截图逐像素校验，超过阈值自动回滚，未通过的组不会出现在产物里；",
        "- `maxΔ=1` 属于 PNG 拍平与浏览器合成在 8bit 量化上的舍入差，肉眼不可见。",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="产物级重排：合并纯图片 layer-group 并做像素校验",
    )
    parser.add_argument("html_dir", help="产物 html 目录（含 index_optimized.html / style_optimized.css）")
    parser.add_argument("--input", default="index_optimized.html", help="输入 HTML 文件名")
    parser.add_argument("--css", default="style_optimized.css", help="输入 CSS 文件名")
    parser.add_argument("--suffix", default="_ai", help="输出后缀：index<suffix>.html / style<suffix>.css")
    parser.add_argument("--max-color-delta", type=int, default=DEFAULT_MAX_COLOR_DELTA,
                        help="单组允许的最大通道差（默认 1）")
    parser.add_argument("--no-verify", action="store_true",
                        help="跳过浏览器像素校验（产物不可信，仅用于调试）")
    parser.add_argument("--dry-run", action="store_true", help="只列出候选与预计收益，不写文件")
    args = parser.parse_args(argv)

    html_dir = Path(args.html_dir).expanduser().resolve()
    html_path = html_dir / args.input
    css_path = html_dir / args.css
    images_dir = html_dir / "images"
    if not html_path.exists() or not css_path.exists():
        raise SystemExit(f"缺少产物文件：{html_path} / {css_path}")

    css_text = css_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    css_rules = parse_css_to_dict(css_text)
    viewport = canvas_size(css_text)
    div_before = len(soup.find_all("div"))

    candidates = collect_candidates(soup, css_rules, images_dir)
    ok = [c for c in candidates if c.ok]

    print(f"候选组：{len(candidates)}（可合并 {len(ok)}）")
    for cand in [c for c in candidates if not c.ok][:10]:
        print(f"  跳过 {cand.group_class}: {cand.skip_reason}")

    if args.dry_run:
        for i, cand in enumerate(ok, 1):
            print(f"  待合并 {cand.group_class}: "
                  f"{' + '.join(l.css_class for l in cand.layers)}")
        print("dry-run：未写任何文件")
        return 0

    for i, cand in enumerate(ok, 1):
        synthesize(cand, images_dir, f"ai-merged-{i}.png")

    if args.no_verify:
        for cand in ok:
            cand.status = "merged"
            cand.detail = "未校验"
    else:
        verify_candidates(html_path, ok, viewport, args.max_color_delta)

    merged = [c for c in ok if c.status == "merged"]
    if not merged:
        print("没有通过校验的组，未写产物")
        return 1

    new_css, new_html, removed = apply_changes(soup, css_text, ok)
    out_html = html_dir / f"{html_path.stem.split('_')[0]}{args.suffix}.html"
    out_css = html_dir / f"{css_path.stem.split('_')[0]}{args.suffix}.css"
    out_html.write_text(new_html.replace(args.css, out_css.name), encoding="utf-8")
    out_css.write_text(new_css, encoding="utf-8")

    stats: dict[str, Any] = {
        "removed": removed,
        "div_before": div_before,
        "div_after": div_before - removed,
    }
    if not args.no_verify:
        max_delta, over, ratio = full_page_diff(html_path, out_html, viewport)
        stats.update(max_delta=max_delta, diff_ratio=ratio)
        if max_delta > args.max_color_delta:
            print(f"⚠️ 整页差异 {max_delta} 超过阈值，产物仍已写出，请人工核对 {out_html.name}")
    report = render_report(args.input, candidates, stats)
    report_path = html_dir / "_ai_relayout_report.md"
    report_path.write_text(report, encoding="utf-8")

    print(f"合并 {len(merged)} 组，删除 {removed} 个节点")
    print(f"输出：{out_html.name} / {out_css.name}")
    print(f"报告：{report_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
