"""SemanticClassRename + VirtualWrapperRename 单元测试。

覆盖：
  - _parse_named_class 解析
  - SemanticClassRename._allocate_name 命名分配
  - SemanticClassRename.run() 端到端（CSS/HTML/merge_groups 改写）
  - _parse_numbered_wrapper / _is_semantic_class / _pick_semantic_from_classes
  - VirtualWrapperRename._allocate_name
  - VirtualWrapperRename._find_semantic_prefix
  - VirtualWrapperRename.run() 端到端
"""
import pytest
from bs4 import BeautifulSoup

from targets.html.postprocess.layout_optimizer.transformers.semantic_class_rename import (
    SemanticClassRename,
    SemanticRenameConfig,
    _parse_named_class,
)
from targets.html.postprocess.layout_optimizer.transformers.virtual_wrapper_rename import (
    VirtualWrapperRename,
    VirtualWrapperRenameConfig,
    _parse_numbered_wrapper,
    _is_semantic_class,
    _pick_semantic_from_classes,
)


# ===========================================================================
# 辅助
# ===========================================================================

def _make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ===========================================================================
# SemanticClassRename 解析函数
# ===========================================================================

class TestParseNamedClass:
    def test_standard_format(self):
        assert _parse_named_class("nickname__37") == ("nickname", "37")

    def test_with_hyphen(self):
        assert _parse_named_class("btn-primary__42") == ("btn-primary", "42")

    def test_single_char_base(self):
        assert _parse_named_class("a__1") == ("a", "1")

    def test_no_match_no_underscore(self):
        assert _parse_named_class("nickname") is None

    def test_no_match_single_underscore(self):
        assert _parse_named_class("nick_name") is None

    def test_no_match_non_digit_after_dunder(self):
        assert _parse_named_class("nick__abc") is None

    def test_no_match_starts_with_digit(self):
        assert _parse_named_class("9item__1") is None

    def test_no_match_virtual_wrapper(self):
        # 虚拟 wrapper 类不匹配 _NAMED_RE（不含 __）
        assert _parse_named_class("v-stack-7") is None


# ===========================================================================
# SemanticClassRename._allocate_name
# ===========================================================================

class TestSemanticAllocateName:
    def test_first_gets_bare_name(self):
        result = SemanticClassRename._allocate_name("btn", 0, set())
        assert result == "btn"

    def test_second_gets_dash_2(self):
        result = SemanticClassRename._allocate_name("btn", 1, set())
        assert result == "btn-2"

    def test_skip_reserved(self):
        """当目标名已被占用时跳号"""
        reserved = {"btn", "btn-2"}
        result = SemanticClassRename._allocate_name("btn", 0, reserved)
        assert result == "btn-3"

    def test_multiple_allocations(self):
        reserved = set()
        names = []
        for i in range(4):
            n = SemanticClassRename._allocate_name("card", i, reserved)
            names.append(n)
            reserved.add(n)
        assert names == ["card", "card-2", "card-3", "card-4"]


# ===========================================================================
# SemanticClassRename.run() 端到端
# ===========================================================================

class TestSemanticClassRenameRun:
    def test_basic_rename(self):
        html = '<div class="nickname__37">A</div><div class="nickname__102">B</div>'
        soup = _make_soup(html)
        css_rules = {
            ".nickname__37": {"color": "red"},
            ".nickname__102": {"color": "blue"},
        }
        stats = {}
        renamer = SemanticClassRename(soup, css_rules, stats)
        renamer.run()

        # css_rules 应被重写
        assert ".nickname" in css_rules
        assert ".nickname-2" in css_rules
        assert ".nickname__37" not in css_rules
        assert ".nickname__102" not in css_rules

        # HTML 也应更新
        divs = soup.find_all("div")
        assert "nickname" in divs[0]["class"]
        assert "nickname-2" in divs[1]["class"]

        # stats
        assert stats["semantic_class_renamed"] == 2
        assert "nickname__37" in stats["_class_alias_map"]
        assert "nickname__102" in stats["_class_alias_map"]

    def test_disabled(self):
        html = '<div class="a__1">X</div>'
        soup = _make_soup(html)
        css_rules = {".a__1": {"width": "10px"}}
        stats = {}
        renamer = SemanticClassRename(
            soup, css_rules, stats, config=SemanticRenameConfig(enabled=False)
        )
        renamer.run()
        # 不做任何改写
        assert ".a__1" in css_rules
        assert stats["semantic_class_renamed"] == 0

    def test_respects_existing_base_class(self):
        """RepeatClassUnifier 已产出裸 .btn 时，从 -2 开始"""
        html = '<div class="btn__5">X</div><div class="btn__8">Y</div>'
        soup = _make_soup(html)
        css_rules = {
            ".btn": {"display": "flex"},  # 已被 RepeatClassUnifier 产出
            ".btn__5": {"color": "red"},
            ".btn__8": {"color": "blue"},
        }
        stats = {}
        renamer = SemanticClassRename(soup, css_rules, stats)
        renamer.run()

        # .btn 已存在于 reserved → 从 -2 开始分配
        assert ".btn" in css_rules
        assert ".btn-2" in css_rules
        assert ".btn-3" in css_rules
        assert ".btn__5" not in css_rules

    def test_merge_groups_rewritten(self):
        html = '<div class="x__1"></div><div class="y__2"></div>'
        soup = _make_soup(html)
        css_rules = {
            ".x__1": {"width": "50px"},
            ".y__2": {"width": "50px"},
        }
        stats = {"_css_merge_groups": [[".x__1", ".y__2"]]}
        renamer = SemanticClassRename(soup, css_rules, stats)
        renamer.run()

        groups = stats["_css_merge_groups"]
        # 合并组中的选择器应被替换
        all_sels = [s for g in groups for s in g]
        assert ".x__1" not in all_sels
        assert ".y__2" not in all_sels
        assert ".x" in all_sels
        assert ".y" in all_sels

    def test_no_named_classes_noop(self):
        html = '<div class="plain">X</div>'
        soup = _make_soup(html)
        css_rules = {".plain": {"color": "red"}}
        stats = {}
        renamer = SemanticClassRename(soup, css_rules, stats)
        renamer.run()
        assert ".plain" in css_rules
        assert stats["semantic_class_renamed"] == 0


# ===========================================================================
# VirtualWrapperRename 识别函数
# ===========================================================================

class TestVirtualWrapperParsing:
    def test_parse_v_stack(self):
        assert _parse_numbered_wrapper("v-stack-7") == ("v-stack", "7")

    def test_parse_v_row(self):
        assert _parse_numbered_wrapper("v-row-2") == ("v-row", "2")

    def test_parse_v_col(self):
        assert _parse_numbered_wrapper("v-col-33") == ("v-col", "33")

    def test_parse_grid_row(self):
        assert _parse_numbered_wrapper("grid-row-5") == ("grid-row", "5")

    def test_parse_v_grid_row(self):
        assert _parse_numbered_wrapper("v-grid-row-5") == ("v-grid-row", "5")

    def test_no_match_named_class(self):
        assert _parse_numbered_wrapper("nickname__37") is None

    def test_no_match_plain(self):
        assert _parse_numbered_wrapper("header") is None

    def test_is_semantic_class_true(self):
        assert _is_semantic_class("nickname") is True
        assert _is_semantic_class("btn-primary") is True
        assert _is_semantic_class("card") is True

    def test_is_semantic_class_false(self):
        assert _is_semantic_class("layer") is False
        assert _is_semantic_class("layer-group") is False
        assert _is_semantic_class("v-stack") is False
        assert _is_semantic_class("v-row") is False
        assert _is_semantic_class("") is False
        assert _is_semantic_class("123abc") is False
        assert _is_semantic_class("-weird") is False
        assert _is_semantic_class("v-stack-7") is False

    def test_pick_semantic_from_classes(self):
        assert _pick_semantic_from_classes(["v-stack", "layer", "card"]) == "card"
        assert _pick_semantic_from_classes(["v-stack", "layer-group"]) is None
        assert _pick_semantic_from_classes(["header", "layer"]) == "header"


# ===========================================================================
# VirtualWrapperRename._allocate_name
# ===========================================================================

class TestVirtualWrapperAllocateName:
    def test_basic(self):
        result = VirtualWrapperRename._allocate_name("card", "v-stack", set())
        # prefix-kind(去掉v-前缀)
        assert result == "card-stack"

    def test_row(self):
        result = VirtualWrapperRename._allocate_name("header", "v-row", set())
        assert result == "header-row"

    def test_col(self):
        result = VirtualWrapperRename._allocate_name("items", "v-col", set())
        assert result == "items-col"

    def test_collision_increments(self):
        reserved = {"card-stack"}
        result = VirtualWrapperRename._allocate_name("card", "v-stack", reserved)
        assert result == "card-stack-2"

    def test_grid_row_kind(self):
        result = VirtualWrapperRename._allocate_name("list", "v-grid-row", set())
        assert result == "list-grid-row"


# ===========================================================================
# VirtualWrapperRename.run() 端到端
# ===========================================================================

class TestVirtualWrapperRenameRun:
    def test_basic_rename(self):
        html = '''
        <div class="bankuai">
            <div class="v-stack-1 v-stack">
                <div class="card">内容</div>
            </div>
        </div>
        '''
        soup = _make_soup(html)
        css_rules = {
            ".bankuai": {"width": "750px"},
            ".v-stack-1": {"position": "relative"},
            ".card": {"color": "red"},
        }
        stats = {}
        renamer = VirtualWrapperRename(soup, css_rules, stats)
        renamer.run()

        # v-stack-1 应被重命名为基于后代语义的名字
        assert ".v-stack-1" not in css_rules
        # 新名应包含 "card" 前缀（从后代 DFS 找到）
        new_keys = [k for k in css_rules if "card" in k and "stack" in k]
        assert len(new_keys) == 1
        assert stats["virtual_wrapper_renamed"] == 1

    def test_disabled(self):
        html = '<div class="v-row-1"><div class="item">X</div></div>'
        soup = _make_soup(html)
        css_rules = {".v-row-1": {"display": "flex"}, ".item": {"color": "red"}}
        stats = {}
        renamer = VirtualWrapperRename(
            soup, css_rules, stats, config=VirtualWrapperRenameConfig(enabled=False)
        )
        renamer.run()
        assert ".v-row-1" in css_rules
        assert stats["virtual_wrapper_renamed"] == 0

    def test_fallback_to_ancestor(self):
        """后代无语义类时，从祖先找前缀"""
        html = '''
        <div class="section-hero">
            <div class="v-col-5 v-col">
                <img class="layer" />
            </div>
        </div>
        '''
        soup = _make_soup(html)
        css_rules = {
            ".section-hero": {"width": "100%"},
            ".v-col-5": {"display": "flex"},
        }
        stats = {}
        renamer = VirtualWrapperRename(soup, css_rules, stats)
        renamer.run()

        assert ".v-col-5" not in css_rules
        # 应从祖先 section-hero 获得前缀
        new_keys = [k for k in css_rules if "section-hero" in k and "col" in k]
        assert len(new_keys) == 1

    def test_fallback_wrapper_prefix(self):
        """后代和祖先都无语义类 → 用 'wrapper' 前缀"""
        html = '<div class="v-stack-9 v-stack"><div class="layer"></div></div>'
        soup = _make_soup(html)
        css_rules = {".v-stack-9": {"position": "relative"}}
        stats = {}
        renamer = VirtualWrapperRename(soup, css_rules, stats)
        renamer.run()

        assert ".v-stack-9" not in css_rules
        new_keys = [k for k in css_rules if "wrapper" in k]
        assert len(new_keys) == 1

    def test_no_candidates_noop(self):
        """无编号 wrapper → 不做任何改写"""
        html = '<div class="card">Content</div>'
        soup = _make_soup(html)
        css_rules = {".card": {"color": "red"}}
        stats = {}
        renamer = VirtualWrapperRename(soup, css_rules, stats)
        renamer.run()
        assert ".card" in css_rules
        assert stats["virtual_wrapper_renamed"] == 0

    def test_coalesce_equivalent_wrappers(self):
        """同一等价组 + 同前缀 → 合流到单一新类名"""
        html = '''
        <div class="parent">
            <div class="v-stack-1 v-stack"><div class="item">A</div></div>
            <div class="v-stack-2 v-stack"><div class="item">B</div></div>
        </div>
        '''
        soup = _make_soup(html)
        css_rules = {
            ".parent": {"width": "750px"},
            ".v-stack-1": {"width": "100px", "height": "50px"},
            ".v-stack-2": {"width": "100px", "height": "50px"},
            ".item": {"color": "red"},
        }
        # CssDedup 标记它们为等价
        stats = {"_css_merge_groups": [[".v-stack-1", ".v-stack-2"]]}
        renamer = VirtualWrapperRename(
            soup, css_rules, stats,
            config=VirtualWrapperRenameConfig(coalesce_equivalent_wrappers=True),
        )
        renamer.run()

        # 两者应合流到同一个新类名
        alias_map = stats["_class_alias_map"]
        assert alias_map["v-stack-1"] == alias_map["v-stack-2"]
        assert stats["virtual_wrapper_renamed"] == 2
