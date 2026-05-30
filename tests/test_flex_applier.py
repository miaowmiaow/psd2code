"""FlexApplier transformer 单测

覆盖核心布局应用逻辑：
  - 垂直布局（flex-col）：趋势元素 margin 转换 + 非趋势保留 absolute
  - 横向布局（flex-row）：趋势元素 margin 转换
  - 非 flex 容器：absolute 子 → 父添 relative
  - v-* 容器跳过
  - v-stack wrapper 子保留 relative
  - 带 z-index 子保留 relative
"""

import pytest
from bs4 import BeautifulSoup
from unittest.mock import MagicMock, patch

from targets.html.postprocess.layout_optimizer.transformers.flex_applier import (
    FlexApplier,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _make_applier(html: str, css_rules: dict):
    """创建 FlexApplier 实例，mock LayoutAnalyzer"""
    soup = _make_soup(html)
    stats = {
        "flex_applied": 0,
        "positions_removed": 0,
    }
    applier = FlexApplier(soup, css_rules, stats)
    return applier, soup, stats


# ===========================================================================
# _apply_vertical_layout 单元测试
# ===========================================================================


class TestApplyVerticalLayout:
    """垂直布局应用"""

    def test_basic_vertical_flex(self):
        """趋势子元素应获得 margin-top 并删除 position/top/left"""
        html = """
        <div class="container layer-group">
          <div class="child-a"></div>
          <div class="child-b"></div>
        </div>
        """
        css = {
            ".container": {"position": "absolute", "width": "300px", "height": "500px"},
            ".child-a": {"position": "absolute", "left": "10px", "top": "20px", "width": "100px", "height": "50px"},
            ".child-b": {"position": "absolute", "left": "10px", "top": "90px", "width": "100px", "height": "50px"},
        }
        applier, soup, stats = _make_applier(html, css)
        elem = soup.find("div", class_="container")
        all_children = [
            {"class": "child-a", "left": 10, "top": 20, "width": 100, "height": 50, "is_trend": True, "classes": ["child-a"]},
            {"class": "child-b", "left": 10, "top": 90, "width": 100, "height": 50, "is_trend": True, "classes": ["child-b"]},
        ]

        applier._apply_vertical_layout(
            elem, css[".container"], all_children, "container",
            vertical_changes=1, horizontal_changes=0
        )

        # 父容器应设为 flex column
        assert css[".container"]["display"] == "flex"
        assert css[".container"]["flex-direction"] == "column"
        # 第一个子：margin-top = 原始 top
        assert css[".child-a"]["margin-top"] == "20px"
        assert css[".child-a"]["margin-left"] == "10px"
        # 第二个子：margin-top = gap = 90 - (20+50) = 20
        assert css[".child-b"]["margin-top"] == "20px"
        # position/top/left 应被删除
        assert "position" not in css[".child-a"]
        assert "top" not in css[".child-a"]
        assert "left" not in css[".child-a"]

    def test_non_trend_stays_absolute(self):
        """非趋势元素应保留 absolute 定位"""
        html = """
        <div class="container layer-group">
          <div class="trend-a"></div>
          <div class="decor-b"></div>
        </div>
        """
        css = {
            ".container": {"position": "absolute", "width": "300px"},
            ".trend-a": {"position": "absolute", "left": "10px", "top": "20px", "width": "100px", "height": "50px"},
            ".decor-b": {"position": "absolute", "left": "50px", "top": "200px", "width": "30px", "height": "30px"},
        }
        applier, soup, stats = _make_applier(html, css)
        elem = soup.find("div", class_="container")
        all_children = [
            {"class": "trend-a", "left": 10, "top": 20, "width": 100, "height": 50, "is_trend": True, "classes": ["trend-a"]},
            {"class": "decor-b", "left": 50, "top": 200, "width": 30, "height": 30, "is_trend": False, "classes": ["decor-b"]},
        ]

        applier._apply_vertical_layout(
            elem, css[".container"], all_children, "container",
            vertical_changes=1, horizontal_changes=0
        )

        # 非趋势元素保持 absolute
        assert css[".decor-b"]["position"] == "absolute"
        # 父容器应有 position（作为定位上下文）
        assert "position" in css[".container"]

    def test_vstack_child_keeps_relative(self):
        """v-stack wrapper 子元素保留 position:relative"""
        html = """
        <div class="container layer-group">
          <div class="v-stack child-a"></div>
        </div>
        """
        css = {
            ".container": {"position": "absolute", "width": "300px"},
            ".child-a": {"position": "absolute", "left": "0px", "top": "10px", "width": "100px", "height": "50px"},
        }
        applier, soup, stats = _make_applier(html, css)
        elem = soup.find("div", class_="container")
        all_children = [
            {"class": "child-a", "left": 0, "top": 10, "width": 100, "height": 50, "is_trend": True, "classes": ["v-stack", "child-a"]},
        ]

        applier._apply_vertical_layout(
            elem, css[".container"], all_children, "container",
            vertical_changes=0, horizontal_changes=0
        )

        assert css[".child-a"]["position"] == "relative"

    def test_zindex_child_keeps_relative(self):
        """带 z-index 的子元素保留 position:relative"""
        html = """
        <div class="container layer-group">
          <div class="child-a"></div>
        </div>
        """
        css = {
            ".container": {"position": "absolute", "width": "300px"},
            ".child-a": {"position": "absolute", "left": "0px", "top": "10px", "width": "100px", "height": "50px", "z-index": "5"},
        }
        applier, soup, stats = _make_applier(html, css)
        elem = soup.find("div", class_="container")
        all_children = [
            {"class": "child-a", "left": 0, "top": 10, "width": 100, "height": 50, "is_trend": True, "classes": ["child-a"]},
        ]

        applier._apply_vertical_layout(
            elem, css[".container"], all_children, "container",
            vertical_changes=0, horizontal_changes=0
        )

        assert css[".child-a"]["position"] == "relative"

    def test_flex_shrink_added(self):
        """趋势子元素应获得 flex-shrink: 0"""
        html = """
        <div class="container layer-group">
          <div class="child-a"></div>
        </div>
        """
        css = {
            ".container": {"position": "absolute", "width": "300px"},
            ".child-a": {"position": "absolute", "left": "0px", "top": "0px", "width": "100px", "height": "50px"},
        }
        applier, soup, stats = _make_applier(html, css)
        elem = soup.find("div", class_="container")
        all_children = [
            {"class": "child-a", "left": 0, "top": 0, "width": 100, "height": 50, "is_trend": True, "classes": ["child-a"]},
        ]

        applier._apply_vertical_layout(
            elem, css[".container"], all_children, "container",
            vertical_changes=0, horizontal_changes=0
        )

        assert css[".child-a"]["flex-shrink"] == "0"


# ===========================================================================
# _apply_horizontal_layout 单元测试
# ===========================================================================


class TestApplyHorizontalLayout:
    """横向布局应用"""

    def test_basic_horizontal_flex(self):
        """趋势子元素应获得 margin-left 和 margin-top"""
        html = """
        <div class="container layer-group">
          <div class="child-a"></div>
          <div class="child-b"></div>
        </div>
        """
        css = {
            ".container": {"position": "absolute", "width": "600px", "height": "100px"},
            ".child-a": {"position": "absolute", "left": "10px", "top": "5px", "width": "100px", "height": "50px"},
            ".child-b": {"position": "absolute", "left": "130px", "top": "5px", "width": "100px", "height": "50px"},
        }
        applier, soup, stats = _make_applier(html, css)
        elem = soup.find("div", class_="container")
        all_children = [
            {"class": "child-a", "left": 10, "top": 5, "width": 100, "height": 50, "is_trend": True, "classes": ["child-a"]},
            {"class": "child-b", "left": 130, "top": 5, "width": 100, "height": 50, "is_trend": True, "classes": ["child-b"]},
        ]

        applier._apply_horizontal_layout(
            elem, css[".container"], all_children, "container",
            horizontal_changes=1, vertical_changes=0
        )

        # 父容器应设为 flex row
        assert css[".container"]["display"] == "flex"
        assert css[".container"]["flex-direction"] == "row"
        # 第一个子：margin-left = 10
        assert css[".child-a"]["margin-left"] == "10px"
        assert css[".child-a"]["margin-top"] == "5px"
        # 第二个子：margin-left = 130 - (10+100) = 20
        assert css[".child-b"]["margin-left"] == "20px"
        # position/top/left 应被删除
        assert "position" not in css[".child-a"]
        assert "top" not in css[".child-a"]
        assert "left" not in css[".child-a"]


# ===========================================================================
# _handle_non_flex_container 单元测试
# ===========================================================================


class TestHandleNonFlexContainer:
    """非 flex 容器处理"""

    def test_adds_relative_when_children_absolute(self):
        """有 absolute 子元素时父容器应添加 position: relative"""
        html = """
        <div class="container layer-group">
          <div class="child-a"></div>
        </div>
        """
        css = {
            ".container": {"width": "300px"},
            ".child-a": {"position": "absolute", "left": "10px", "top": "20px"},
        }
        applier, soup, stats = _make_applier(html, css)
        elem = soup.find("div", class_="container")
        all_children = [
            {"class": "child-a", "left": 10, "top": 20, "width": 100, "height": 50, "is_trend": False, "classes": ["child-a"]},
        ]

        applier._handle_non_flex_container(elem, css[".container"], all_children, "container")

        assert css[".container"]["position"] == "relative"

    def test_no_relative_when_no_absolute_children(self):
        """没有 absolute 子元素时不添加 position"""
        html = """
        <div class="container layer-group">
          <div class="child-a"></div>
        </div>
        """
        css = {
            ".container": {"width": "300px"},
            ".child-a": {"position": "relative"},
        }
        applier, soup, stats = _make_applier(html, css)
        elem = soup.find("div", class_="container")
        all_children = [
            {"class": "child-a", "left": 10, "top": 20, "width": 100, "height": 50, "is_trend": False, "classes": ["child-a"]},
        ]

        applier._handle_non_flex_container(elem, css[".container"], all_children, "container")

        assert "position" not in css[".container"]


# ===========================================================================
# apply_flex_layouts 集成跳过逻辑
# ===========================================================================


class TestApplyFlexLayoutsSkip:
    """apply_flex_layouts 中的跳过条件"""

    def test_skip_v_row(self):
        """v-row 容器应被跳过"""
        html = """
        <div class="v-row container">
          <div class="child-a"></div>
          <div class="child-b"></div>
        </div>
        """
        css = {
            ".v-row": {},
            ".child-a": {"position": "absolute", "left": "0px", "top": "0px", "width": "50px", "height": "50px"},
            ".child-b": {"position": "absolute", "left": "60px", "top": "0px", "width": "50px", "height": "50px"},
        }
        applier, soup, stats = _make_applier(html, css)
        applier.apply_flex_layouts()
        # 不应有任何 flex 应用
        assert stats["flex_applied"] == 0

    def test_skip_v_col(self):
        """v-col 容器应被跳过"""
        html = """
        <div class="v-col container">
          <div class="child-a"></div>
        </div>
        """
        css = {
            ".v-col": {},
            ".child-a": {"position": "absolute"},
        }
        applier, soup, stats = _make_applier(html, css)
        applier.apply_flex_layouts()
        assert stats["flex_applied"] == 0

    def test_skip_v_stack(self):
        """v-stack 容器应被跳过"""
        html = """
        <div class="v-stack container">
          <div class="child-a"></div>
        </div>
        """
        css = {
            ".v-stack": {},
            ".child-a": {"position": "absolute"},
        }
        applier, soup, stats = _make_applier(html, css)
        applier.apply_flex_layouts()
        assert stats["flex_applied"] == 0

    def test_skip_v_list(self):
        """v-list 容器应被跳过"""
        html = """
        <div class="v-list container">
          <div class="child-a"></div>
        </div>
        """
        css = {
            ".v-list": {},
            ".child-a": {"position": "absolute"},
        }
        applier, soup, stats = _make_applier(html, css)
        applier.apply_flex_layouts()
        assert stats["flex_applied"] == 0

    def test_skip_already_flex(self):
        """已经是 display:flex 的容器应被跳过"""
        html = """
        <div class="container layer-group">
          <div class="child-a"></div>
        </div>
        """
        css = {
            ".container": {"display": "flex", "width": "300px"},
            ".child-a": {"position": "absolute", "left": "0px", "top": "0px", "width": "50px", "height": "50px"},
        }
        applier, soup, stats = _make_applier(html, css)
        applier.apply_flex_layouts()
        assert stats["flex_applied"] == 0

    def test_decor_classes_excluded_from_trend(self):
        """decor_classes 中的子元素不参与 flex 趋势"""
        html = """
        <div class="container layer-group">
          <div class="bg-img"></div>
          <div class="child-a"></div>
          <div class="child-b"></div>
        </div>
        """
        css = {
            ".container": {"position": "absolute", "width": "300px", "height": "500px"},
            ".bg-img": {"position": "absolute", "left": "0px", "top": "0px", "width": "300px", "height": "500px"},
            ".child-a": {"position": "absolute", "left": "10px", "top": "20px", "width": "100px", "height": "50px"},
            ".child-b": {"position": "absolute", "left": "10px", "top": "90px", "width": "100px", "height": "50px"},
        }
        applier, soup, stats = _make_applier(html, css)
        elem = soup.find("div", class_="container")
        all_children = [
            {"class": "bg-img", "left": 0, "top": 0, "width": 300, "height": 500, "is_trend": True, "classes": ["bg-img"]},
            {"class": "child-a", "left": 10, "top": 20, "width": 100, "height": 50, "is_trend": True, "classes": ["child-a"]},
            {"class": "child-b", "left": 10, "top": 90, "width": 100, "height": 50, "is_trend": True, "classes": ["child-b"]},
        ]
        decor_classes = {"bg-img"}

        applier._apply_vertical_layout(
            elem, css[".container"], all_children, "container",
            vertical_changes=1, horizontal_changes=0,
            decor_classes=decor_classes,
        )

        # bg-img 应保持 absolute（被标记为 decor）
        assert css[".bg-img"]["position"] == "absolute"
        # child-a / child-b 参与 flex 化
        assert "position" not in css[".child-a"]
