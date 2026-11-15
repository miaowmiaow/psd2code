"""背景层吸收（LayoutOptimizer Step 3.95）的单元测试。

这个 pass 历史上有一个"只做一半"的 bug：当底图的 `left/top` 为 0（也就是绝大多数
情况）时，它**只**把 background 写进容器 CSS，却没有删除底图子层、也没有累加统计，
结果是同一张图被绘制两遍，且 `_layout_stats.json` 谎报 0 吸收。

这里锁住四条不变式：
1. 吸收成功 ⇒ 元素删除 + 旧规则被清理 + 计数 +1；
2. 不满足条件 ⇒ **完全不改动** CSS（不能出现"容器有背景、子层还在"）；
3. 有子元素的层不能被吸收（会丢内容）；
4. z-index 无法区分前后景时（`CssDedup` 之后常见）不吸收。
"""

from __future__ import annotations

import copy

from targets.html.postprocess.layout_optimizer.transformers.background_layer_absorber_v2 import (
    absorb_background_layers_safe,
)

HTML = '''<div id="canvas">
<div class="card layer-group" data-name="卡片" data-type="group" id="group-2">
<div class="bgimg layer" data-name="底图" data-type="image" id="layer-1"></div>
<div class="title layer" data-name="标题" data-type="text" id="layer-3"></div>
</div>
</div>'''

HTML_BG_WITH_CHILD = '''<div id="canvas">
<div class="card layer-group" data-name="卡片" data-type="group" id="group-2">
<div class="bgimg layer" data-name="底图" data-type="image" id="layer-1">
<div class="inner layer" data-name="内层" data-type="image" id="layer-9"></div>
</div>
<div class="title layer" data-name="标题" data-type="text" id="layer-3"></div>
</div>
</div>'''


def _css(
    z_bg: int = 1,
    z_title: int = 2,
    bg_left: str = "0px",
    bg_top: str = "0px",
    bg_size: tuple[str, str] = ("300px", "200px"),
) -> dict[str, dict[str, str]]:
    return {
        ".card": {
            "position": "absolute", "left": "0px", "top": "0px",
            "width": "300px", "height": "200px",
        },
        ".bgimg": {
            "position": "absolute", "left": bg_left, "top": bg_top,
            "width": bg_size[0], "height": bg_size[1],
            "z-index": str(z_bg),
            "background-image": 'url("images/bg.png")',
        },
        ".title": {
            "position": "absolute", "left": "10px", "top": "10px",
            "width": "200px", "height": "30px",
            "z-index": str(z_title), "font-size": "14px",
        },
    }


class TestAbsorbHappyPath:
    def test_full_cover_background_is_absorbed(self):
        """核心回归：底图 left/top 为 0 时也必须删除子层并计数。"""
        css = _css()
        new_html, new_css, stats = absorb_background_layers_safe(HTML, css)

        assert stats["bg_layers_absorbed"] == 1
        assert stats["bg_elements_removed"] == 1
        # 容器拿到背景
        assert new_css[".card"]["background-image"] == 'url("images/bg.png")'
        # 子层 DOM 被删除、旧 class 规则被清理
        assert "bgimg" not in new_html
        assert ".bgimg" not in new_css
        # 文案层与容器定位不受影响
        assert "title" in new_html
        assert new_css[".card"]["width"] == "300px"

    def test_offset_background_writes_position(self):
        """带偏移的底图吸收后，偏移要落到 background-position 上。"""
        new_html, new_css, stats = absorb_background_layers_safe(
            HTML, _css(bg_left="5px", bg_top="5px")
        )
        assert stats["bg_layers_absorbed"] == 1
        assert "bgimg" not in new_html
        card = new_css[".card"]
        position = card.get("background-position") or card.get("background", "")
        assert "5px 5px" in position


class TestAbsorbSkips:
    def test_identical_z_index_skips_without_touching_css(self):
        """z-index 相同（CssDedup 后的常态）→ 不吸收，且不能"只改一半"。"""
        css = _css(z_bg=1, z_title=1)
        before = copy.deepcopy(css)
        new_html, new_css, stats = absorb_background_layers_safe(HTML, css)

        assert stats["bg_layers_absorbed"] == 0
        assert new_css == before, "跳过时不应修改任何 CSS"
        assert "bgimg" in new_html

    def test_background_with_children_is_not_absorbed(self):
        """有子元素的层不能被吸收（会丢内容），且不能留下半成品背景。"""
        css = _css()
        before = copy.deepcopy(css)
        new_html, new_css, stats = absorb_background_layers_safe(HTML_BG_WITH_CHILD, css)

        assert stats["bg_layers_absorbed"] == 0
        assert "background-image" not in new_css.get(".card", {}), "不能只写容器背景"
        assert new_css[".bgimg"] == before[".bgimg"]
        assert "inner" in new_html

    def test_partial_cover_is_not_absorbed(self):
        """覆盖率不足 95% 的前景图不能被当成背景层。"""
        css = _css(bg_size=("150px", "200px"))
        _, new_css, stats = absorb_background_layers_safe(HTML, css)
        assert stats["bg_layers_absorbed"] == 0
        assert ".bgimg" in new_css

    def test_negative_offset_is_not_absorbed(self):
        """出血背景（left<0）吸收后会被容器裁切，不能吸收。"""
        css = _css(bg_left="-5px")
        _, new_css, stats = absorb_background_layers_safe(HTML, css)
        assert stats["bg_layers_absorbed"] == 0
        assert ".bgimg" in new_css


class TestAbsorberInsideOptimizerChain:
    """集成位置回归：吸收必须跑在 CssDedup **之前**。

    CssDedup 会精简掉"与 DOM 顺序一致"的 z-index，而吸收用 z-index 判断前后景。
    一旦有人把 ``_absorb_background_layers`` 挪到 dedup 之后，这个 pass 会退化为
    min_z == max_z 的 early-return（即完全失效），本用例会立刻失败。
    """

    def test_absorbed_before_css_dedup_strips_z_index(self):
        from targets.html.postprocess.layout_optimizer import optimize_layout

        html = '''<!DOCTYPE html>
<html><body>
<div id="canvas">
<div class="card layer-group" data-name="卡片" data-type="group" id="group-2">
<div class="bgimg layer" data-name="底图" data-type="image" id="layer-1"></div>
<div class="title layer" data-name="标题" data-type="text" id="layer-3">标题</div>
</div>
</div>
</body></html>'''
        css = {
            "#canvas": {"position": "relative", "width": "300px", "height": "200px",
                        "overflow": "hidden"},
            ".card": {"position": "absolute", "left": "0px", "top": "0px",
                      "width": "300px", "height": "200px"},
            ".bgimg": {"position": "absolute", "left": "0px", "top": "0px",
                       "width": "300px", "height": "200px", "z-index": "1",
                       "background-image": 'url("images/bg.png")',
                       "background-size": "100% 100%"},
            ".title": {"position": "absolute", "left": "10px", "top": "10px",
                       "width": "200px", "height": "30px", "z-index": "2",
                       "font-size": "14px"},
        }
        new_html, new_css, stats = optimize_layout(html, css, global_header="")

        assert stats["bg_absorption"]["bg_layers_absorbed"] == 1
        assert "bgimg" not in new_html, "底图子层应被删除"
        assert ".bgimg" not in new_css, "底图规则应被清理"
        assert "url(\"images/bg.png\")" in new_css[".card"].get("background", "")
        # 文案层必须保留
        assert "title" in new_html
