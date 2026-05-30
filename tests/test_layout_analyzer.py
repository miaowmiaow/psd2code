"""Tests for LayoutAnalyzer — 布局特征分析器单元测试

覆盖范围：
- _parse_opacity: 各种 CSS opacity 解析
- _axis_overlap_ratio: 轴向投影重叠率计算
- _bbox_overlap_area: bbox 交叉面积计算
- _classify_children: V10 装饰剥离（bg / decor / content）
- _detect_trend_layout: V13 趋势检测算法
- _is_stacked_cluster: V8 堆叠装饰组判定
- _has_dominant_background_overlay: V9 支配背景层判定
- analyze_children_layout: 端到端集成测试
- calculate_signature: 结构签名
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from targets.html.postprocess.layout_optimizer.analyzers.layout_analyzer import (
    LayoutAnalyzer,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


def _make_analyzer(css_rules: dict | None = None) -> LayoutAnalyzer:
    """便捷创建 LayoutAnalyzer 实例"""
    return LayoutAnalyzer(css_rules or {})


def _make_child_info(
    class_name: str,
    left: float,
    top: float,
    width: float,
    height: float,
    data_type: str = "group",
    opacity: float = 1.0,
) -> dict:
    """便捷构建 children_info 字典"""
    return {
        "class": class_name,
        "classes": [class_name],
        "left": left,
        "top": top,
        "width": width,
        "height": height,
        "data_type": data_type,
        "opacity": opacity,
        "is_trend": False,
    }


def _make_bs4_child(class_name: str, data_type: str = "group") -> dict:
    """模拟 BeautifulSoup child 的 dict-like 接口（供 analyze_children_layout 使用）"""
    return {"class": [class_name], "data-type": data_type}


# ═══════════════════════════════════════════════════════════════════════════════
# TestParseOpacity
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseOpacity:
    """_parse_opacity 测试"""

    def test_normal_float(self):
        assert LayoutAnalyzer._parse_opacity("0.5") == 0.5

    def test_integer_one(self):
        assert LayoutAnalyzer._parse_opacity("1") == 1.0

    def test_zero(self):
        assert LayoutAnalyzer._parse_opacity("0") == 0.0

    def test_empty_string(self):
        assert LayoutAnalyzer._parse_opacity("") == 1.0

    def test_invalid_string(self):
        assert LayoutAnalyzer._parse_opacity("auto") == 1.0

    def test_whitespace(self):
        assert LayoutAnalyzer._parse_opacity("  0.8  ") == 0.8

    def test_none_returns_default(self):
        assert LayoutAnalyzer._parse_opacity(None) == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# TestAxisOverlapRatio
# ═══════════════════════════════════════════════════════════════════════════════


class TestAxisOverlapRatio:
    """_axis_overlap_ratio 测试"""

    def test_full_overlap_x(self):
        """两个完全相同的元素 X 投影重叠率 = 1.0"""
        a = _make_child_info("a", 100, 0, 200, 50)
        b = _make_child_info("b", 100, 60, 200, 50)
        assert LayoutAnalyzer._axis_overlap_ratio(a, b, "x") == 1.0

    def test_no_overlap_x(self):
        """X 方向完全不重叠"""
        a = _make_child_info("a", 0, 0, 100, 50)
        b = _make_child_info("b", 200, 0, 100, 50)
        assert LayoutAnalyzer._axis_overlap_ratio(a, b, "x") == 0.0

    def test_partial_overlap_x(self):
        """X 方向部分重叠"""
        a = _make_child_info("a", 0, 0, 100, 50)
        b = _make_child_info("b", 50, 0, 100, 50)
        # overlap = 50, max(100, 100) = 100 → 0.5
        assert LayoutAnalyzer._axis_overlap_ratio(a, b, "x") == 0.5

    def test_narrow_inside_wide_x(self):
        """窄元素在宽元素内部，用 max(w_a, w_b) 归一化 → 比率较低"""
        a = _make_child_info("a", 0, 0, 400, 50)
        b = _make_child_info("b", 100, 0, 50, 50)
        # overlap = 50, max(400, 50) = 400 → 0.125
        assert LayoutAnalyzer._axis_overlap_ratio(a, b, "x") == pytest.approx(
            0.125
        )

    def test_full_overlap_y(self):
        """Y 方向完全对齐"""
        a = _make_child_info("a", 0, 100, 50, 200)
        b = _make_child_info("b", 60, 100, 50, 200)
        assert LayoutAnalyzer._axis_overlap_ratio(a, b, "y") == 1.0

    def test_no_overlap_y(self):
        a = _make_child_info("a", 0, 0, 50, 100)
        b = _make_child_info("b", 0, 200, 50, 100)
        assert LayoutAnalyzer._axis_overlap_ratio(a, b, "y") == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# TestBboxOverlapArea
# ═══════════════════════════════════════════════════════════════════════════════


class TestBboxOverlapArea:
    """_bbox_overlap_area 测试"""

    def test_no_overlap(self):
        a = _make_child_info("a", 0, 0, 50, 50)
        b = _make_child_info("b", 100, 100, 50, 50)
        assert LayoutAnalyzer._bbox_overlap_area(a, b) == 0.0

    def test_full_overlap(self):
        a = _make_child_info("a", 10, 10, 100, 100)
        b = _make_child_info("b", 10, 10, 100, 100)
        assert LayoutAnalyzer._bbox_overlap_area(a, b) == 10000.0

    def test_partial_overlap(self):
        a = _make_child_info("a", 0, 0, 100, 100)
        b = _make_child_info("b", 50, 50, 100, 100)
        # overlap: x=[50,100]=50, y=[50,100]=50 → 2500
        assert LayoutAnalyzer._bbox_overlap_area(a, b) == 2500.0

    def test_contained(self):
        """b 完全在 a 内"""
        a = _make_child_info("a", 0, 0, 200, 200)
        b = _make_child_info("b", 50, 50, 50, 50)
        assert LayoutAnalyzer._bbox_overlap_area(a, b) == 2500.0

    def test_edge_touching(self):
        """边缘接触 → 0 面积重叠"""
        a = _make_child_info("a", 0, 0, 100, 100)
        b = _make_child_info("b", 100, 0, 100, 100)
        assert LayoutAnalyzer._bbox_overlap_area(a, b) == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# TestClassifyChildren
# ═══════════════════════════════════════════════════════════════════════════════


class TestClassifyChildren:
    """_classify_children 装饰剥离逻辑测试"""

    def test_empty_list(self):
        analyzer = _make_analyzer()
        bg, decor, content = analyzer._classify_children([])
        assert bg == set()
        assert decor == set()
        assert content == []

    def test_no_images_all_content(self):
        """全部是 group/text 类型 → 全部归 content"""
        children = [
            _make_child_info("g1", 0, 0, 100, 50, data_type="group"),
            _make_child_info("t1", 0, 60, 100, 20, data_type="text"),
        ]
        analyzer = _make_analyzer()
        bg, decor, content = analyzer._classify_children(children)
        assert bg == set()
        assert decor == set()
        assert len(content) == 2

    def test_large_image_is_bg(self):
        """面积 >= 85% envelope → bg"""
        # envelope = 300x200 = 60000
        children = [
            _make_child_info("bg-img", 0, 0, 290, 190, data_type="image", opacity=1.0),
            _make_child_info("txt", 50, 50, 100, 30, data_type="text"),
        ]
        analyzer = _make_analyzer()
        bg, decor, content = analyzer._classify_children(children)
        assert "bg-img" in bg
        assert "txt" not in bg
        assert len(content) == 1
        assert content[0]["class"] == "txt"

    def test_dual_axis_cover_is_bg(self):
        """双轴覆盖 >= 80% → bg（即使面积占比稍小）"""
        # envelope: left=0, top=0, right=300, bottom=200 → 300x200
        # bg-img: left=5, top=5, w=280, h=180 → area=50400 / 60000=0.84 ≥ 0.85? 是的
        # 但如果面积稍微不够，走双轴覆盖
        children = [
            # 面积=240*160=38400/60000=0.64 < 0.85，但是双轴覆盖...
            # cover_w = min(240,300)-max(0,0)=240 / 300=0.8
            # cover_h = min(160,200)-max(0,0)=160 / 200=0.8
            _make_child_info("bg-img", 0, 0, 240, 160, data_type="image", opacity=1.0),
            _make_child_info("txt", 0, 0, 300, 200, data_type="text"),
        ]
        analyzer = _make_analyzer()
        bg, decor, content = analyzer._classify_children(children)
        assert "bg-img" in bg

    def test_small_lowopacity_image_is_decor(self):
        """小面积 + 低透明度 + 不重叠内容 → decor"""
        # envelope: x=[0,300], y=[0,200] → 300x200=60000
        # deco 在右上角，与 txt（在左侧）不重叠
        children = [
            # decor: image, opacity=0.5, area=20*20=400/60000=0.006 < 0.3
            # 且不与非 image 子重叠（txt 在左侧，grp 也是 image 所以不参与重叠检查）
            _make_child_info("deco", 280, 0, 20, 20, data_type="image", opacity=0.5),
            _make_child_info("txt", 50, 50, 100, 30, data_type="text"),
            _make_child_info("anchor", 0, 180, 20, 20, data_type="image", opacity=1.0),
        ]
        analyzer = _make_analyzer()
        bg, decor, content = analyzer._classify_children(children)
        assert "deco" in decor
        assert "txt" not in decor
        assert "anchor" not in decor

    def test_decor_overlapping_content_stays_content(self):
        """小 image 但与 text 重叠显著 → 不归 decor，留作 content"""
        children = [
            # 小 image 盖在 text 上面
            _make_child_info("icon", 50, 50, 30, 30, data_type="image", opacity=0.5),
            _make_child_info("txt", 40, 40, 100, 50, data_type="text"),
        ]
        analyzer = _make_analyzer()
        bg, decor, content = analyzer._classify_children(children)
        # icon overlap with txt: x=[50,80]∩[40,140]=30, y=[50,80]∩[40,90]=30 → 900
        # icon area=900, overlap/self_area=900/900=1.0 >= 0.3 → 不算 decor
        assert "icon" not in decor
        assert len(content) == 2

    def test_high_opacity_image_not_decor(self):
        """高透明度 image（≥0.95）不算 decor（即使面积小）"""
        children = [
            _make_child_info("icon", 280, 180, 20, 20, data_type="image", opacity=0.97),
            _make_child_info("main", 0, 0, 300, 200, data_type="group"),
        ]
        analyzer = _make_analyzer()
        bg, decor, content = analyzer._classify_children(children)
        assert "icon" not in decor
        assert len(content) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# TestDetectTrendLayout
# ═══════════════════════════════════════════════════════════════════════════════


class TestDetectTrendLayout:
    """_detect_trend_layout V13 趋势检测"""

    def test_clear_vertical_3_items(self):
        """3 个垂直对齐元素 → vertical"""
        items = [
            _make_child_info("a", 100, 0, 200, 50),
            _make_child_info("b", 100, 60, 200, 50),
            _make_child_info("c", 100, 120, 200, 50),
        ]
        analyzer = _make_analyzer()
        layout, v, h = analyzer._detect_trend_layout(items)
        assert layout == "vertical"
        assert v >= 2
        assert h == 0

    def test_clear_horizontal_3_items(self):
        """3 个水平对齐元素 → horizontal"""
        items = [
            _make_child_info("a", 0, 100, 100, 200),
            _make_child_info("b", 110, 100, 100, 200),
            _make_child_info("c", 220, 100, 100, 200),
        ]
        # 注意：列表需按 (top, left) 排序
        items.sort(key=lambda x: (x["top"], x["left"]))
        analyzer = _make_analyzer()
        layout, v, h = analyzer._detect_trend_layout(items)
        assert layout == "horizontal"
        assert h >= 2

    def test_scattered_no_layout(self):
        """散落元素（X 错位） → none"""
        items = [
            _make_child_info("a", 34, 31, 100, 40),
            _make_child_info("b", 541, 26, 154, 30),
            _make_child_info("c", 227, 124, 200, 100),
            _make_child_info("d", 227, 237, 200, 100),
        ]
        items.sort(key=lambda x: (x["top"], x["left"]))
        analyzer = _make_analyzer()
        layout, v, h = analyzer._detect_trend_layout(items)
        # V13 应该因为 X 投影不对齐而拦截
        # b@(541,154) 与 c@(227,200) → X overlap = max(0, min(695,427)-max(541,227))
        # = max(0, 427-541) = 0 → overlap_ratio=0 < 0.5 → 不串链
        assert layout == "none"

    def test_two_items_vertical(self):
        """content 只有 2 个（V10 特例）→ 1 次变化也可 flex"""
        items = [
            _make_child_info("a", 100, 0, 200, 50),
            _make_child_info("b", 100, 60, 200, 50),
        ]
        analyzer = _make_analyzer()
        layout, v, h = analyzer._detect_trend_layout(items)
        assert layout == "vertical"
        assert v == 1

    def test_two_items_horizontal(self):
        items = [
            _make_child_info("a", 0, 100, 100, 200),
            _make_child_info("b", 110, 100, 100, 200),
        ]
        items.sort(key=lambda x: (x["top"], x["left"]))
        analyzer = _make_analyzer()
        layout, v, h = analyzer._detect_trend_layout(items)
        assert layout == "horizontal"
        assert h == 1

    def test_single_item_none(self):
        """单个元素不检测趋势"""
        items = [_make_child_info("a", 0, 0, 100, 50)]
        analyzer = _make_analyzer()
        layout, v, h = analyzer._detect_trend_layout(items)
        assert layout == "none"
        assert v == 0
        assert h == 0


# ═══════════════════════════════════════════════════════════════════════════════
# TestIsStackedCluster
# ═══════════════════════════════════════════════════════════════════════════════


class TestIsStackedCluster:
    """_is_stacked_cluster V8 堆叠装饰组"""

    def test_no_overlap_not_stacked(self):
        """无重叠的竖排列 → 非堆叠"""
        items = [
            _make_child_info("a", 0, 0, 100, 50),
            _make_child_info("b", 0, 60, 100, 50),
            _make_child_info("c", 0, 120, 100, 50),
        ]
        analyzer = _make_analyzer()
        assert analyzer._is_stacked_cluster(items) is False

    def test_all_overlapping_is_stacked(self):
        """所有元素高度重叠 → 堆叠"""
        items = [
            _make_child_info("a", 10, 10, 200, 200),
            _make_child_info("b", 20, 20, 200, 200),
            _make_child_info("c", 30, 30, 200, 200),
            _make_child_info("d", 40, 40, 200, 200),
        ]
        analyzer = _make_analyzer()
        # 每对都有大面积重叠，pair 数 = C(4,2) = 6 >= n=4
        assert analyzer._is_stacked_cluster(items) is True

    def test_two_items_never_stacked(self):
        """只有 2 个元素 → 直接返回 False（< 3）"""
        items = [
            _make_child_info("a", 0, 0, 200, 200),
            _make_child_info("b", 10, 10, 200, 200),
        ]
        analyzer = _make_analyzer()
        assert analyzer._is_stacked_cluster(items) is False

    def test_borderline_overlap(self):
        """部分重叠但不够 → 非堆叠"""
        # 3 个元素排成半重叠序列
        items = [
            _make_child_info("a", 0, 0, 100, 100),    # area=10000
            _make_child_info("b", 80, 0, 100, 100),   # overlap a-b: x=20, y=100 → 2000/10000=0.2 < 0.3
            _make_child_info("c", 160, 0, 100, 100),  # overlap a-c: 0, b-c: x=20, y=100 → 2000/10000=0.2
        ]
        analyzer = _make_analyzer()
        assert analyzer._is_stacked_cluster(items) is False


# ═══════════════════════════════════════════════════════════════════════════════
# TestHasDominantBackgroundOverlay
# ═══════════════════════════════════════════════════════════════════════════════


class TestHasDominantBackgroundOverlay:
    """_has_dominant_background_overlay V9 支配背景"""

    def test_one_large_bg_with_overlaid_children(self):
        """一个大 image 底板 + 多个小元素落在其上 → True"""
        # envelope = 300x300 = 90000
        # bg: 290x290 = 84100 / 90000 = 0.93 ≥ 0.8
        items = [
            _make_child_info("bg", 5, 5, 290, 290, data_type="image"),
            _make_child_info("txt1", 50, 50, 80, 20, data_type="text"),
            _make_child_info("txt2", 50, 100, 80, 20, data_type="text"),
            _make_child_info("icon", 200, 200, 40, 40, data_type="image"),
        ]
        analyzer = _make_analyzer()
        assert analyzer._has_dominant_background_overlay(items) is True

    def test_large_group_not_candidate(self):
        """大 group 不算候选（V10 修正：必须是 image）"""
        items = [
            _make_child_info("grp", 0, 0, 300, 300, data_type="group"),
            _make_child_info("btn", 100, 100, 50, 30, data_type="text"),
        ]
        analyzer = _make_analyzer()
        assert analyzer._has_dominant_background_overlay(items) is False

    def test_no_dominant_bg(self):
        """所有元素差不多大 → 无支配背景"""
        items = [
            _make_child_info("a", 0, 0, 100, 100, data_type="image"),
            _make_child_info("b", 110, 0, 100, 100, data_type="image"),
            _make_child_info("c", 220, 0, 100, 100, data_type="image"),
        ]
        analyzer = _make_analyzer()
        # envelope = 320x100 = 32000, each area=10000/32000=0.3125 < 0.8
        assert analyzer._has_dominant_background_overlay(items) is False

    def test_single_item_returns_false(self):
        """只有 1 个子元素 → n < 2 → False"""
        items = [_make_child_info("bg", 0, 0, 500, 500, data_type="image")]
        analyzer = _make_analyzer()
        assert analyzer._has_dominant_background_overlay(items) is False

    def test_children_not_inside_bg(self):
        """大 image 存在但其余子元素没落在它上面"""
        # bg 在左上角，子元素在右下角（不重叠）
        items = [
            _make_child_info("bg", 0, 0, 200, 200, data_type="image"),
            _make_child_info("a", 250, 250, 50, 50, data_type="text"),
            _make_child_info("b", 260, 310, 40, 40, data_type="text"),
        ]
        analyzer = _make_analyzer()
        # envelope = 300x350 = 105000, bg area=40000/105000=0.38 < 0.8
        assert analyzer._has_dominant_background_overlay(items) is False


# ═══════════════════════════════════════════════════════════════════════════════
# TestAnalyzeChildrenLayout — 端到端集成
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnalyzeChildrenLayout:
    """analyze_children_layout 端到端测试"""

    def _build_css_rules(self, items: list[dict]) -> dict:
        """从 item 信息构建 css_rules"""
        rules = {}
        for item in items:
            rules[f".{item['name']}"] = {
                "left": f"{item['left']}px",
                "top": f"{item['top']}px",
                "width": f"{item['width']}px",
                "height": f"{item['height']}px",
                "opacity": str(item.get("opacity", 1.0)),
            }
        return rules

    def test_vertical_layout(self):
        """3 个垂直排列的 group → vertical"""
        items = [
            {"name": "a", "left": 50, "top": 0, "width": 200, "height": 50},
            {"name": "b", "left": 50, "top": 60, "width": 200, "height": 50},
            {"name": "c", "left": 50, "top": 120, "width": 200, "height": 50},
        ]
        css = self._build_css_rules(items)
        children = [_make_bs4_child(i["name"]) for i in items]

        analyzer = LayoutAnalyzer(css)
        result = analyzer.analyze_children_layout(children)
        assert result["layout_type"] == "vertical"
        assert result["vertical_changes"] >= 2

    def test_horizontal_layout(self):
        """3 个水平排列的 group → horizontal"""
        items = [
            {"name": "a", "left": 0, "top": 50, "width": 100, "height": 200},
            {"name": "b", "left": 110, "top": 50, "width": 100, "height": 200},
            {"name": "c", "left": 220, "top": 50, "width": 100, "height": 200},
        ]
        css = self._build_css_rules(items)
        children = [_make_bs4_child(i["name"]) for i in items]

        analyzer = LayoutAnalyzer(css)
        result = analyzer.analyze_children_layout(children)
        assert result["layout_type"] == "horizontal"
        assert result["horizontal_changes"] >= 2

    def test_single_child_returns_none(self):
        """只有 1 个子 → none"""
        items = [{"name": "a", "left": 0, "top": 0, "width": 100, "height": 50}]
        css = self._build_css_rules(items)
        children = [_make_bs4_child(i["name"]) for i in items]

        analyzer = LayoutAnalyzer(css)
        result = analyzer.analyze_children_layout(children)
        assert result["layout_type"] == "none"

    def test_decor_classes_populated(self):
        """装饰层被正确识别到 decor_classes"""
        items = [
            {"name": "bg", "left": 0, "top": 0, "width": 290, "height": 190,
             "opacity": 1.0},
            {"name": "a", "left": 50, "top": 50, "width": 100, "height": 30},
            {"name": "b", "left": 50, "top": 100, "width": 100, "height": 30},
        ]
        css = self._build_css_rules(items)
        # bg 是 image 类型
        children = [
            {"class": ["bg"], "data-type": "image"},
            {"class": ["a"], "data-type": "text"},
            {"class": ["b"], "data-type": "text"},
        ]
        analyzer = LayoutAnalyzer(css)
        result = analyzer.analyze_children_layout(children)
        # bg 应被剥离为 bg 类装饰
        assert "bg" in result["decor_classes"]

    def test_stacked_cluster_returns_none(self):
        """堆叠装饰组 → 即使 trend 通过也被 V8 拦截"""
        # 4 个 group 互相高度重叠
        items = [
            {"name": "a", "left": 10, "top": 10, "width": 200, "height": 200},
            {"name": "b", "left": 20, "top": 20, "width": 200, "height": 200},
            {"name": "c", "left": 30, "top": 30, "width": 200, "height": 200},
            {"name": "d", "left": 40, "top": 40, "width": 200, "height": 200},
        ]
        css = self._build_css_rules(items)
        children = [_make_bs4_child(i["name"]) for i in items]

        analyzer = LayoutAnalyzer(css)
        result = analyzer.analyze_children_layout(children)
        assert result["layout_type"] == "none"

    def test_child_without_class_skipped(self):
        """没有 class 的子元素被跳过"""
        css = {
            ".a": {"left": "0", "top": "0", "width": "100px", "height": "50px"},
        }
        children = [
            {"class": ["a"], "data-type": "group"},
            {"class": [None], "data-type": "group"},  # 无效 class
        ]
        analyzer = LayoutAnalyzer(css)
        result = analyzer.analyze_children_layout(children)
        # 只有 1 个有效子 → none
        assert result["layout_type"] == "none"

    def test_no_css_defaults_to_zero(self):
        """CSS 缺失时位置/尺寸默认 0"""
        css = {}  # 完全空 CSS
        children = [
            {"class": ["x"], "data-type": "group"},
            {"class": ["y"], "data-type": "group"},
        ]
        analyzer = LayoutAnalyzer(css)
        result = analyzer.analyze_children_layout(children)
        # 所有子在 (0,0) size=0 → 无法产生趋势
        assert result["layout_type"] == "none"


# ═══════════════════════════════════════════════════════════════════════════════
# TestCalculateSignature
# ═══════════════════════════════════════════════════════════════════════════════


class TestCalculateSignature:
    """calculate_signature 结构签名测试"""

    def _make_soup_element(self, html_str: str):
        """创建 BeautifulSoup 元素"""
        soup = BeautifulSoup(html_str, "html.parser")
        return soup.find("div")

    def test_basic_signature(self):
        html = """
        <div class="container" data-type="group">
            <div data-type="text">Hello</div>
            <div data-type="image"></div>
        </div>
        """
        css = {
            ".container": {
                "width": "200px",
                "height": "100px",
                "background-image": "url(images/bg-card.png)",
            }
        }
        analyzer = LayoutAnalyzer(css)
        el = self._make_soup_element(html)
        sig = analyzer.calculate_signature(el)
        # 格式: child_count|types|bg_name|widthxheight（无空格）
        assert sig == "2|text,image|bg-card|200pxx100px"

    def test_no_background(self):
        html = '<div class="box"><span data-type="text">Hi</span></div>'
        css = {".box": {"width": "50px", "height": "30px"}}
        analyzer = LayoutAnalyzer(css)
        el = self._make_soup_element(html)
        sig = analyzer.calculate_signature(el)
        assert "|50pxx30px" in sig
        # 无背景图
        assert "||" in sig

    def test_element_without_css(self):
        html = '<div class="unknown"><div data-type="group"></div></div>'
        analyzer = LayoutAnalyzer({})
        el = self._make_soup_element(html)
        sig = analyzer.calculate_signature(el)
        assert sig.startswith("1|group|")
