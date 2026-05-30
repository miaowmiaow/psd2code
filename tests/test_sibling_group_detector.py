"""SiblingGroupDetector transformer 单测

覆盖核心纯函数和端到端逻辑：
  - _extract_class_root：词根提取
  - _values_close：相对误差比较
  - _cluster_axis：一维容差聚类
  - _compute_gap：gap 计算
  - _sizes_close：bbox 尺寸近似判定
  - _detect_grid：网格检测
  - _wrap_as_list：包裹为 v-list（集成）
"""

import pytest
from bs4 import BeautifulSoup

from targets.html.postprocess.layout_optimizer.transformers.sibling_group_detector import (
    SiblingGroupDetector,
    SiblingGroupConfig,
    SiblingItem,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _make_detector(html: str, css_rules: dict, config=None):
    soup = _make_soup(html)
    stats: dict = {}
    det = SiblingGroupDetector(soup, css_rules, stats, config)
    return det, soup, stats


# ===========================================================================
# _extract_class_root
# ===========================================================================


class TestExtractClassRoot:
    """词根提取"""

    def test_simple_name(self):
        assert SiblingGroupDetector._extract_class_root("prop__30") == "prop"

    def test_with_sequence(self):
        assert SiblingGroupDetector._extract_class_root("prop-2__38") == "prop"

    def test_large_sequence(self):
        assert SiblingGroupDetector._extract_class_root("prop-10__101") == "prop"

    def test_compound_name(self):
        assert SiblingGroupDetector._extract_class_root("card-item__5") == "card-item"

    def test_compound_with_sequence(self):
        assert SiblingGroupDetector._extract_class_root("card-item-2__7") == "card-item"

    def test_no_suffix(self):
        assert SiblingGroupDetector._extract_class_root("btn") == "btn"

    def test_only_layer_id(self):
        assert SiblingGroupDetector._extract_class_root("box__100") == "box"

    def test_trailing_dash_number_no_layer_id(self):
        assert SiblingGroupDetector._extract_class_root("item-3") == "item"


# ===========================================================================
# _values_close
# ===========================================================================


class TestValuesClose:
    """相对误差比较"""

    def test_identical(self):
        assert SiblingGroupDetector._values_close([100, 100, 100], 0.05) is True

    def test_within_tolerance(self):
        # range = 4, avg = 100, 4/100 = 0.04 < 0.05
        assert SiblingGroupDetector._values_close([98, 100, 102], 0.05) is True

    def test_exceeds_tolerance(self):
        # range = 20, avg = 100, 20/100 = 0.2 > 0.05
        assert SiblingGroupDetector._values_close([90, 100, 110], 0.05) is False

    def test_empty_list(self):
        assert SiblingGroupDetector._values_close([], 0.05) is False

    def test_single_value(self):
        assert SiblingGroupDetector._values_close([50], 0.05) is True

    def test_zero_average(self):
        """平均值为零时返回 False"""
        assert SiblingGroupDetector._values_close([0, 0, 0], 0.05) is False


# ===========================================================================
# _cluster_axis
# ===========================================================================


class TestClusterAxis:
    """一维容差聚类"""

    def test_single_cluster(self):
        result = SiblingGroupDetector._cluster_axis([10, 11, 10.5], 2.0)
        assert len(result) == 1
        assert abs(result[0] - 10.5) < 0.01

    def test_two_clusters(self):
        result = SiblingGroupDetector._cluster_axis([10, 11, 50, 51], 2.0)
        assert len(result) == 2
        assert result[0] < result[1]

    def test_exact_tolerance_boundary(self):
        # 差恰好 = tol → 同簇
        result = SiblingGroupDetector._cluster_axis([0, 2], 2.0)
        assert len(result) == 1

    def test_just_over_tolerance(self):
        # 差 > tol → 分两簇
        result = SiblingGroupDetector._cluster_axis([0, 3], 2.0)
        assert len(result) == 2

    def test_empty(self):
        result = SiblingGroupDetector._cluster_axis([], 2.0)
        assert result == []

    def test_sorted_output(self):
        result = SiblingGroupDetector._cluster_axis([100, 0, 50], 2.0)
        assert result == sorted(result)


# ===========================================================================
# _compute_gap
# ===========================================================================


class TestComputeGap:
    """gap 计算"""

    def test_basic_gap(self):
        # positions = [0, 320], item_size = 312 → gap = 8
        gap = SiblingGroupDetector._compute_gap([0, 320], 312)
        assert abs(gap - 8) < 0.01

    def test_single_position(self):
        # 单行/单列 → gap = 0
        gap = SiblingGroupDetector._compute_gap([50], 100)
        assert gap == 0.0

    def test_inconsistent_diffs(self):
        """相邻间距不一致 → 返回 None"""
        # diffs = [100, 200], max - min = 100 > 1.5 → None
        gap = SiblingGroupDetector._compute_gap([0, 100, 300], 50)
        assert gap is None

    def test_consistent_diffs(self):
        # diffs = [200, 200], item_size = 180 → gap = 20
        gap = SiblingGroupDetector._compute_gap([0, 200, 400], 180)
        assert abs(gap - 20) < 0.01

    def test_zero_gap(self):
        # positions = [0, 100], item_size = 100 → gap = 0
        gap = SiblingGroupDetector._compute_gap([0, 100], 100)
        assert abs(gap) < 0.01


# ===========================================================================
# _detect_grid
# ===========================================================================


class TestDetectGrid:
    """网格检测"""

    def test_2x2_grid(self):
        """2x2 规则网格"""
        html = "<div id='canvas'></div>"
        css = {
            ".a__1": {"position": "absolute", "left": "0px", "top": "0px", "width": "100px", "height": "80px"},
            ".a-2__2": {"position": "absolute", "left": "120px", "top": "0px", "width": "100px", "height": "80px"},
            ".a-3__3": {"position": "absolute", "left": "0px", "top": "100px", "width": "100px", "height": "80px"},
            ".a-4__4": {"position": "absolute", "left": "120px", "top": "100px", "width": "100px", "height": "80px"},
        }
        det, soup, stats = _make_detector(html, css)
        items = []
        for cls, props in css.items():
            name = cls[1:]  # remove dot
            items.append(SiblingItem(
                element=None, css_class=name,
                class_root=det._extract_class_root(name),
                data_name="",
                left=float(props["left"].replace("px", "")),
                top=float(props["top"].replace("px", "")),
                width=float(props["width"].replace("px", "")),
                height=float(props["height"].replace("px", "")),
            ))
        grid = det._detect_grid(items)
        assert grid is not None
        assert grid["cols"] == 2
        assert grid["rows"] == 2
        assert grid["col_gap"] == pytest.approx(20, abs=1)
        assert grid["row_gap"] == pytest.approx(20, abs=1)

    def test_single_row(self):
        """单行 3 列"""
        html = "<div id='canvas'></div>"
        css = {}
        det, soup, stats = _make_detector(html, css)
        items = [
            SiblingItem(None, "card__1", "card", "", 0, 0, 100, 80),
            SiblingItem(None, "card-2__2", "card", "", 120, 0, 100, 80),
            SiblingItem(None, "card-3__3", "card", "", 240, 0, 100, 80),
        ]
        grid = det._detect_grid(items)
        assert grid is not None
        assert grid["cols"] == 3
        assert grid["rows"] == 1

    def test_not_a_grid_missing_slot(self):
        """非满格 → 不是规则网格"""
        html = "<div id='canvas'></div>"
        css = {}
        det, soup, stats = _make_detector(html, css)
        # 3 items 在 2x2 格中 → cols*rows (4) != n (3)
        items = [
            SiblingItem(None, "x__1", "x", "", 0, 0, 100, 80),
            SiblingItem(None, "x-2__2", "x", "", 120, 0, 100, 80),
            SiblingItem(None, "x-3__3", "x", "", 0, 100, 100, 80),
        ]
        grid = det._detect_grid(items)
        assert grid is None

    def test_gap_out_of_range(self):
        """gap 超出最大限制 → 不识别"""
        html = "<div id='canvas'></div>"
        css = {}
        config = SiblingGroupConfig(max_gap_px=50)
        det, soup, stats = _make_detector(html, css, config)
        items = [
            SiblingItem(None, "y__1", "y", "", 0, 0, 100, 80),
            SiblingItem(None, "y-2__2", "y", "", 200, 0, 100, 80),
            SiblingItem(None, "y-3__3", "y", "", 400, 0, 100, 80),
        ]
        # gap = 200 - 100 = 100 > max 50
        grid = det._detect_grid(items)
        assert grid is None


# ===========================================================================
# _sizes_close
# ===========================================================================


class TestSizesClose:
    """bbox 尺寸近似"""

    def test_sizes_match(self):
        html = "<div></div>"
        det, _, _ = _make_detector(html, {})
        items = [
            SiblingItem(None, "a", "a", "", 0, 0, 100, 80),
            SiblingItem(None, "b", "b", "", 0, 0, 102, 79),
            SiblingItem(None, "c", "c", "", 0, 0, 101, 81),
        ]
        assert det._sizes_close(items) is True

    def test_sizes_mismatch_width(self):
        html = "<div></div>"
        det, _, _ = _make_detector(html, {})
        items = [
            SiblingItem(None, "a", "a", "", 0, 0, 100, 80),
            SiblingItem(None, "b", "b", "", 0, 0, 200, 80),
        ]
        assert det._sizes_close(items) is False


# ===========================================================================
# 集成测试：run() 端到端
# ===========================================================================


class TestRunEndToEnd:
    """端到端测试：识别同质兄弟并包裹为 v-list"""

    def test_basic_3_items_single_row(self):
        """3 个同类卡片单行排列 → 创建 v-list"""
        html = """
        <div id="canvas">
          <div class="card__1 layer-group" data-name="card"></div>
          <div class="card-2__2 layer-group" data-name="card"></div>
          <div class="card-3__3 layer-group" data-name="card"></div>
        </div>
        """
        css = {
            ".card__1": {"position": "absolute", "left": "0px", "top": "0px", "width": "100px", "height": "80px"},
            ".card-2__2": {"position": "absolute", "left": "120px", "top": "0px", "width": "100px", "height": "80px"},
            ".card-3__3": {"position": "absolute", "left": "240px", "top": "0px", "width": "100px", "height": "80px"},
        }
        det, soup, stats = _make_detector(html, css)
        det.run()

        assert stats["sibling_lists_created"] == 1
        assert stats["sibling_items_wrapped"] == 3

        # v-list wrapper 应该存在
        wrapper = soup.find("div", attrs={"data-virtual": "list"})
        assert wrapper is not None
        # 包裹的子节点 position 应该被去掉
        assert "position" not in css[".card__1"]
        assert "left" not in css[".card__1"]
        assert "top" not in css[".card__1"]

    def test_min_count_not_met(self):
        """不足 min_count 个 → 不创建 v-list"""
        html = """
        <div id="canvas">
          <div class="item__1 layer-group" data-name="item"></div>
          <div class="item-2__2 layer-group" data-name="item"></div>
        </div>
        """
        css = {
            ".item__1": {"position": "absolute", "left": "0px", "top": "0px", "width": "100px", "height": "80px"},
            ".item-2__2": {"position": "absolute", "left": "120px", "top": "0px", "width": "100px", "height": "80px"},
        }
        det, soup, stats = _make_detector(html, css)
        det.run()
        assert stats["sibling_lists_created"] == 0

    def test_skip_v_star_children(self):
        """v-* 虚拟 wrapper 子元素不参与同质判定"""
        html = """
        <div id="canvas">
          <div class="v-stack-1 layer-group"></div>
          <div class="v-stack-2 layer-group"></div>
          <div class="v-stack-3 layer-group"></div>
        </div>
        """
        css = {
            ".v-stack-1": {"position": "absolute", "left": "0px", "top": "0px", "width": "100px", "height": "80px"},
            ".v-stack-2": {"position": "absolute", "left": "120px", "top": "0px", "width": "100px", "height": "80px"},
            ".v-stack-3": {"position": "absolute", "left": "240px", "top": "0px", "width": "100px", "height": "80px"},
        }
        det, soup, stats = _make_detector(html, css)
        det.run()
        assert stats["sibling_lists_created"] == 0

    def test_skip_parent_with_v_row_class(self):
        """父容器含 v-row class → 跳过"""
        html = """
        <div id="canvas">
          <div class="v-row parent-wrapper">
            <div class="box__1 layer-group"></div>
            <div class="box-2__2 layer-group"></div>
            <div class="box-3__3 layer-group"></div>
          </div>
        </div>
        """
        css = {
            ".box__1": {"position": "absolute", "left": "0px", "top": "0px", "width": "100px", "height": "80px"},
            ".box-2__2": {"position": "absolute", "left": "120px", "top": "0px", "width": "100px", "height": "80px"},
            ".box-3__3": {"position": "absolute", "left": "240px", "top": "0px", "width": "100px", "height": "80px"},
        }
        det, soup, stats = _make_detector(html, css)
        det.run()
        assert stats["sibling_lists_created"] == 0

    def test_css_grid_for_multi_row_col(self):
        """2x2 网格 + enable_css_grid → 使用 display:grid"""
        html = """
        <div id="canvas">
          <div class="prop__1 layer-group" data-name="prop"></div>
          <div class="prop-2__2 layer-group" data-name="prop"></div>
          <div class="prop-3__3 layer-group" data-name="prop"></div>
          <div class="prop-4__4 layer-group" data-name="prop"></div>
        </div>
        """
        css = {
            ".prop__1": {"position": "absolute", "left": "0px", "top": "0px", "width": "100px", "height": "80px"},
            ".prop-2__2": {"position": "absolute", "left": "120px", "top": "0px", "width": "100px", "height": "80px"},
            ".prop-3__3": {"position": "absolute", "left": "0px", "top": "100px", "width": "100px", "height": "80px"},
            ".prop-4__4": {"position": "absolute", "left": "120px", "top": "100px", "width": "100px", "height": "80px"},
        }
        config = SiblingGroupConfig(enable_css_grid=True, grid_min_cols=2, grid_min_rows=2)
        det, soup, stats = _make_detector(html, css, config)
        det.run()

        assert stats["sibling_lists_created"] == 1
        assert stats["grid_lists_created"] == 1
        # 找到 wrapper 的 css
        wrapper_sel = None
        for sel in css:
            if sel.startswith(".v-list-"):
                wrapper_sel = sel
                break
        assert wrapper_sel is not None
        assert css[wrapper_sel]["display"] == "grid"
        assert "grid-template-columns" in css[wrapper_sel]

    def test_flex_wrap_for_single_row(self):
        """单行（1xN）→ 使用 flex-wrap 而非 grid"""
        html = """
        <div id="canvas">
          <div class="tag__1 layer-group" data-name="tag"></div>
          <div class="tag-2__2 layer-group" data-name="tag"></div>
          <div class="tag-3__3 layer-group" data-name="tag"></div>
        </div>
        """
        css = {
            ".tag__1": {"position": "absolute", "left": "0px", "top": "0px", "width": "80px", "height": "40px"},
            ".tag-2__2": {"position": "absolute", "left": "100px", "top": "0px", "width": "80px", "height": "40px"},
            ".tag-3__3": {"position": "absolute", "left": "200px", "top": "0px", "width": "80px", "height": "40px"},
        }
        config = SiblingGroupConfig(enable_css_grid=True)
        det, soup, stats = _make_detector(html, css, config)
        det.run()

        assert stats["sibling_lists_created"] == 1
        assert stats.get("grid_lists_created", 0) == 0
        # 找到 wrapper
        wrapper_sel = None
        for sel in css:
            if sel.startswith(".v-list-"):
                wrapper_sel = sel
                break
        assert wrapper_sel is not None
        assert css[wrapper_sel]["display"] == "flex"
        assert css[wrapper_sel]["flex-wrap"] == "wrap"

    def test_non_absolute_items_skipped(self):
        """position 不是 absolute 的元素不参与同质判定"""
        html = """
        <div id="canvas">
          <div class="item__1 layer-group"></div>
          <div class="item-2__2 layer-group"></div>
          <div class="item-3__3 layer-group"></div>
        </div>
        """
        css = {
            ".item__1": {"position": "relative", "left": "0px", "top": "0px", "width": "100px", "height": "80px"},
            ".item-2__2": {"position": "relative", "left": "120px", "top": "0px", "width": "100px", "height": "80px"},
            ".item-3__3": {"position": "relative", "left": "240px", "top": "0px", "width": "100px", "height": "80px"},
        }
        det, soup, stats = _make_detector(html, css)
        det.run()
        assert stats["sibling_lists_created"] == 0
