"""CssPretty 渲染器单元测试。

覆盖：
  - CssPrettyConfig 预设（expanded / compact）
  - _split_top_level 顶层块切分
  - _strip_top_marker_comments 注释剥离
  - _strip_top_canvas_block #canvas 块剥除
  - _render_skeleton_head 文件骨架（有/无 file_skeleton）
  - _order_layer_entries DOM 序 + 版块注释
  - _render_rule 单条规则（长/短）
  - _render_group 合并组（单行/多行）
  - _render_props 属性分段
  - _provenance_comment 坐标溯源注释
  - render() 端到端
"""
import pytest
from bs4 import BeautifulSoup

from targets.html.postprocess.layout_optimizer.transformers.css_pretty import (
    CssPretty,
    CssPrettyConfig,
    _prop_section,
    _natural_key,
    _PROPERTY_GROUPS,
)


# ===========================================================================
# 辅助
# ===========================================================================

def _make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _make_pretty(
    html: str = "<div></div>",
    css_rules: dict = None,
    merge_groups: list = None,
    global_header: str = "",
    config: CssPrettyConfig = None,
) -> CssPretty:
    soup = _make_soup(html)
    return CssPretty(
        soup=soup,
        css_rules=css_rules or {},
        merge_groups=merge_groups or [],
        global_header=global_header,
        config=config,
    )


# ===========================================================================
# CssPrettyConfig 测试
# ===========================================================================

class TestCssPrettyConfig:
    def test_compact_defaults(self):
        cfg = CssPrettyConfig(style="compact")
        assert cfg.property_grouping is False
        assert cfg.multiline_threshold == 4
        assert cfg.short_rule_max_props == 6
        assert cfg.coord_provenance is False
        assert cfg.section_comment_style == "single"

    def test_expanded_defaults(self):
        cfg = CssPrettyConfig(style="expanded")
        assert cfg.property_grouping is True
        assert cfg.multiline_threshold == 3
        assert cfg.short_rule_max_props == 2
        assert cfg.coord_provenance is True
        assert cfg.section_comment_style == "framed"

    def test_explicit_override_preserved(self):
        """显式值不被预设覆盖"""
        cfg = CssPrettyConfig(style="compact", coord_provenance=True)
        assert cfg.coord_provenance is True  # 显式设置
        assert cfg.property_grouping is False  # 预设填充

    def test_default_style_is_compact(self):
        cfg = CssPrettyConfig()
        assert cfg.style == "compact"


# ===========================================================================
# _prop_section / _natural_key 辅助函数
# ===========================================================================

class TestPropertyHelpers:
    def test_prop_section_known(self):
        gi, pi, name = _prop_section("position")
        assert name == "定位"
        assert gi == 0

    def test_prop_section_box_model(self):
        gi, pi, name = _prop_section("width")
        assert name == "盒模型"
        assert gi == 1

    def test_prop_section_unknown(self):
        gi, pi, name = _prop_section("unknown-prop")
        assert name == "其他"
        assert gi == len(_PROPERTY_GROUPS)

    def test_natural_key_ordering(self):
        sels = [".v-stack-10", ".v-stack-2", ".v-stack-1"]
        result = sorted(sels, key=_natural_key)
        assert result == [".v-stack-1", ".v-stack-2", ".v-stack-10"]


# ===========================================================================
# _split_top_level 顶层块切分
# ===========================================================================

class TestSplitTopLevel:
    def test_basic_split(self):
        css = "* { margin: 0; }\nbody { font-size: 14px; }"
        blocks = CssPretty._split_top_level(css)
        assert len(blocks) == 2
        assert blocks[0][0] == "*"
        assert blocks[1][0] == "body"

    def test_nested_braces(self):
        css = "@media (max-width: 750px) { #canvas { width: 100%; } }"
        blocks = CssPretty._split_top_level(css)
        assert len(blocks) == 1
        assert blocks[0][0] == "@media (max-width: 750px)"
        assert "#canvas" in blocks[0][1]

    def test_skip_comments(self):
        css = "/* header comment */\n* { margin: 0; }"
        blocks = CssPretty._split_top_level(css)
        assert len(blocks) == 1
        assert blocks[0][0] == "*"

    def test_empty_input(self):
        assert CssPretty._split_top_level("") == []
        assert CssPretty._split_top_level("   \n  ") == []


# ===========================================================================
# _strip_top_marker_comments
# ===========================================================================

class TestStripTopMarkerComments:
    def test_strips_leading_comments(self):
        header = "/* PSD2HTML v1.0 */\n/* BEM */\n\n* { margin: 0; }"
        result = CssPretty._strip_top_marker_comments(header)
        assert result.startswith("* {")

    def test_preserves_non_comment_content(self):
        header = "* { margin: 0; }\nbody { color: #333; }"
        result = CssPretty._strip_top_marker_comments(header)
        assert result == header

    def test_empty_string(self):
        assert CssPretty._strip_top_marker_comments("") == ""

    def test_only_comments(self):
        header = "/* comment 1 */\n/* comment 2 */\n"
        result = CssPretty._strip_top_marker_comments(header)
        assert result == ""  # 全被剥掉


# ===========================================================================
# _strip_top_canvas_block
# ===========================================================================

class TestStripTopCanvasBlock:
    def test_removes_canvas_block(self):
        header = "* { margin: 0; }\n#canvas { width: 750px; }\nbody { color: red; }"
        result = CssPretty._strip_top_canvas_block(header)
        assert "#canvas" not in result
        assert "* { margin: 0; }" in result
        assert "body { color: red; }" in result

    def test_preserves_canvas_in_media(self):
        header = "@media (max-width: 750px) { #canvas { width: 100%; } }"
        result = CssPretty._strip_top_canvas_block(header)
        # @media 内的 #canvas 不应被删
        assert "#canvas" in result

    def test_empty_input(self):
        assert CssPretty._strip_top_canvas_block("") == ""


# ===========================================================================
# _render_skeleton_head
# ===========================================================================

class TestRenderSkeletonHead:
    def test_file_skeleton_enabled_segments(self):
        """file_skeleton=True 时，global_header 被分段重排"""
        global_header = "* { margin: 0; }\n#canvas { width: 750px; }"
        p = _make_pretty(
            global_header=global_header,
            css_rules={"#canvas": {"width": "750px", "height": "1334px"}},
            config=CssPrettyConfig(file_skeleton=True, section_comment_style="single"),
        )
        result = p._render_skeleton_head(canvas_rule={"width": "750px", "height": "1334px"})
        assert "Reset" in result
        assert "画布" in result
        assert "width: 750px" in result

    def test_file_skeleton_disabled_passthrough(self):
        """file_skeleton=False 时，直接输出清理过的 global_header + #canvas"""
        global_header = "/* PSD2HTML v1 */\n* { margin: 0; }\n#canvas { width: 750px; }"
        p = _make_pretty(
            global_header=global_header,
            css_rules={"#canvas": {"width": "750px", "height": "1334px"}},
            config=CssPrettyConfig(file_skeleton=False),
        )
        result = p._render_skeleton_head(canvas_rule={"width": "750px", "height": "1334px"})
        # 原始 #canvas 被剥除，最终态被追加
        assert "height: 1334px" in result
        assert "margin: 0" in result
        # 文件标识注释被剥掉
        assert "PSD2HTML" not in result

    def test_no_canvas_rule(self):
        """无 #canvas 时不崩溃"""
        p = _make_pretty(
            global_header="* { margin: 0; }",
            config=CssPrettyConfig(file_skeleton=True, section_comment_style="single"),
        )
        result = p._render_skeleton_head(canvas_rule=None)
        assert "Reset" in result
        assert "画布" not in result


# ===========================================================================
# _render_rule / _render_props
# ===========================================================================

class TestRenderRule:
    def test_short_rule_inline(self):
        """属性 ≤ max_props → 单行"""
        p = _make_pretty(config=CssPrettyConfig(short_rule_max_props=3))
        result = p._render_rule(".box", {"width": "100px", "height": "50px"})
        assert result.count("\n") == 0
        assert ".box {" in result
        assert "width: 100px" in result
        assert "}" in result

    def test_long_rule_multiline(self):
        """属性 > max_props → 多行展开"""
        props = {
            "position": "absolute",
            "left": "10px",
            "top": "20px",
            "width": "100px",
            "height": "50px",
            "background-color": "red",
            "opacity": "0.8",
        }
        p = _make_pretty(config=CssPrettyConfig(short_rule_max_props=2))
        result = p._render_rule(".card", props)
        assert result.count("\n") > 1
        assert ".card {" in result
        assert "position: absolute;" in result

    def test_property_grouping_expanded(self):
        """expanded 模式下属性 ≥ min_props 触发分段"""
        props = {
            "position": "absolute",
            "left": "0",
            "top": "0",
            "width": "100px",
            "height": "50px",
            "margin": "10px",
            "padding": "5px",
            "color": "#333",
            "font-size": "14px",
            "background": "red",
        }
        p = _make_pretty(config=CssPrettyConfig(
            style="expanded",
            short_rule_inline=False,
            property_grouping_min_props=5,
        ))
        result = p._render_props(props)
        assert "/* 定位 */" in result
        assert "/* 盒模型 */" in result
        assert "/* 排版 */" in result
        assert "/* 外观 */" in result

    def test_property_grouping_disabled(self):
        """compact 模式（property_grouping=False）无段标题"""
        props = {
            "position": "absolute",
            "width": "100px",
            "color": "#333",
            "background": "red",
        }
        p = _make_pretty(config=CssPrettyConfig(style="compact"))
        result = p._render_props(props)
        assert "/* 定位 */" not in result
        # 但属性仍按段序排
        lines = [l.strip() for l in result.strip().splitlines()]
        # position 应在 width 前面
        pos_idx = next(i for i, l in enumerate(lines) if "position" in l)
        w_idx = next(i for i, l in enumerate(lines) if "width" in l)
        assert pos_idx < w_idx


# ===========================================================================
# _render_group 合并组
# ===========================================================================

class TestRenderGroup:
    def test_group_multiline(self):
        """成员 ≥ threshold → 多行展开"""
        members = [".a", ".b", ".c", ".d"]
        css_rules = {s: {"width": "100px"} for s in members}
        p = _make_pretty(
            css_rules=css_rules,
            config=CssPrettyConfig(multiline_threshold=3, merge_group_comment=True),
        )
        result = p._render_group(members)
        # 选择器逐行展开
        assert ".a,\n.b,\n.c,\n.d" in result
        # 注释
        assert "4 个等价规则合并" in result

    def test_group_inline(self):
        """成员 < threshold → 单行"""
        members = [".x", ".y"]
        css_rules = {s: {"color": "red"} for s in members}
        p = _make_pretty(
            css_rules=css_rules,
            config=CssPrettyConfig(multiline_threshold=3),
        )
        result = p._render_group(members)
        assert ".x, .y" in result
        assert "等价规则" not in result

    def test_group_comment_disabled(self):
        """merge_group_comment=False → 无注释"""
        members = [".a", ".b", ".c", ".d"]
        css_rules = {s: {"width": "100px"} for s in members}
        p = _make_pretty(
            css_rules=css_rules,
            config=CssPrettyConfig(
                multiline_threshold=3,
                merge_group_comment=False,
            ),
        )
        result = p._render_group(members)
        assert "等价规则" not in result


# ===========================================================================
# 坐标溯源注释（P2b）
# ===========================================================================

class TestProvenanceComment:
    def test_provenance_with_full_meta(self):
        html = '''<div id="canvas"><div id="layer-5" class="card" data-name="背景" data-type="image"></div></div>'''
        css_rules = {
            ".card": {"left": "10px", "top": "20px", "width": "100px", "height": "50px"},
        }
        p = _make_pretty(
            html=html,
            css_rules=css_rules,
            config=CssPrettyConfig(coord_provenance=True),
        )
        result = p._provenance_comment(".card", css_rules[".card"])
        assert "PSD:" in result
        assert 'layer-5' in result
        assert '"背景"' in result
        assert "type=image" in result
        assert "abs(10px,20px" in result

    def test_provenance_no_dot_selector(self):
        """非 .class 选择器不生成注释"""
        p = _make_pretty(config=CssPrettyConfig(coord_provenance=True))
        assert p._provenance_comment("#canvas", {"width": "750px"}) == ""

    def test_provenance_disabled(self):
        """coord_provenance=False → render_rule 不输出注释"""
        html = '<div id="layer-1" class="box" data-type="text" data-name="title"></div>'
        p = _make_pretty(
            html=html,
            css_rules={".box": {"width": "50px"}},
            config=CssPrettyConfig(coord_provenance=False),
        )
        result = p._render_rule(".box", {"width": "50px"})
        assert "PSD:" not in result

    def test_provenance_parent_info(self):
        html = '''<div id="group-1" data-name="容器"><div id="layer-2" class="inner" data-name="子层" data-type="text"></div></div>'''
        css_rules = {".inner": {"color": "red"}}
        p = _make_pretty(
            html=html,
            css_rules=css_rules,
            config=CssPrettyConfig(coord_provenance=True),
        )
        result = p._provenance_comment(".inner", css_rules[".inner"])
        assert 'parent=group-1 "容器"' in result


# ===========================================================================
# DOM 序 + 版块注释
# ===========================================================================

class TestOrderLayerEntries:
    def test_dom_order_respected(self):
        """entries 顺序反映 DOM 出现顺序"""
        html = '''
        <div id="canvas">
            <div class="header" data-type="group"></div>
            <div class="body" data-type="group"></div>
            <div class="footer" data-type="group"></div>
        </div>
        '''
        css_rules = {
            ".header": {"width": "100px"},
            ".body": {"width": "200px"},
            ".footer": {"width": "300px"},
        }
        p = _make_pretty(
            html=html,
            css_rules=css_rules,
            config=CssPrettyConfig(section_comments=False, dom_order=True),
        )
        entries = p._order_layer_entries({}, set(), skip_selectors=set())
        rule_sels = [e[1] for e in entries if e[0] == 'rule']
        assert rule_sels == [".header", ".body", ".footer"]

    def test_leftover_appended_alphabetically(self):
        """不在 DOM 中的选择器按自然序追加"""
        html = '<div class="a"></div>'
        css_rules = {
            ".a": {"color": "red"},
            ".z-util": {"display": "flex"},
            ".b-helper": {"margin": "0"},
        }
        p = _make_pretty(
            html=html,
            css_rules=css_rules,
            config=CssPrettyConfig(section_comments=False, dom_order=True),
        )
        entries = p._order_layer_entries({}, set(), skip_selectors=set())
        rule_sels = [e[1] for e in entries if e[0] == 'rule']
        # .a 在 DOM 中排首位，.b-helper 和 .z-util 按自然序追加
        assert rule_sels[0] == ".a"
        assert ".b-helper" in rule_sels
        assert ".z-util" in rule_sels

    def test_section_comments_inserted(self):
        """版块切换处插入 section 注释"""
        html = '''
        <div id="canvas">
            <div class="bankuai-top" data-type="group">
                <div class="item-a" data-type="text"></div>
            </div>
            <div class="bankuai-bottom" data-type="group">
                <div class="item-b" data-type="text"></div>
            </div>
        </div>
        '''
        css_rules = {
            ".bankuai-top": {"width": "100px"},
            ".item-a": {"color": "red"},
            ".bankuai-bottom": {"width": "200px"},
            ".item-b": {"color": "blue"},
        }
        p = _make_pretty(
            html=html,
            css_rules=css_rules,
            config=CssPrettyConfig(section_comments=True, dom_order=True),
        )
        entries = p._order_layer_entries({}, set(), skip_selectors=set())
        sections = [e[1] for e in entries if e[0] == 'section']
        assert len(sections) >= 2
        assert "bankuai-top" in sections[0]
        assert "bankuai-bottom" in sections[1]

    def test_merge_group_emitted_once(self):
        """合并组只在代表位置输出一次"""
        html = '<div class="a"></div><div class="b"></div><div class="c"></div>'
        css_rules = {
            ".a": {"width": "100px"},
            ".b": {"width": "100px"},
            ".c": {"color": "red"},
        }
        merge_groups = [[".a", ".b"]]
        p = _make_pretty(
            html=html,
            css_rules=css_rules,
            merge_groups=merge_groups,
            config=CssPrettyConfig(section_comments=False, dom_order=True),
        )
        sel_to_group = {".a": 0, ".b": 0}
        consumed = {".a", ".b"}
        entries = p._order_layer_entries(sel_to_group, consumed, skip_selectors=set())
        group_entries = [e for e in entries if e[0] == 'group']
        assert len(group_entries) == 1
        assert set(group_entries[0][1]) == {".a", ".b"}


# ===========================================================================
# render() 端到端
# ===========================================================================

class TestRenderEndToEnd:
    def test_disabled_returns_empty(self):
        p = _make_pretty(config=CssPrettyConfig(enabled=False))
        assert p.render() == ""

    def test_basic_render(self):
        html = '<div class="box"></div>'
        css_rules = {".box": {"width": "100px", "height": "50px"}}
        p = _make_pretty(
            html=html,
            css_rules=css_rules,
            config=CssPrettyConfig(
                file_skeleton=True,
                section_comments=False,
                section_comment_style="single",
                short_rule_max_props=6,
            ),
        )
        result = p.render()
        # 图层样式段标题
        assert "图层样式" in result
        # 规则存在
        assert ".box" in result
        assert "width: 100px" in result
        # 结尾有换行
        assert result.endswith("\n")

    def test_render_with_merge_groups(self):
        html = '<div class="a"></div><div class="b"></div><div class="c"></div>'
        css_rules = {
            ".a": {"color": "red"},
            ".b": {"color": "red"},
            ".c": {"color": "red"},
            ".d": {"width": "50px"},
        }
        merge_groups = [[".a", ".b", ".c"]]
        p = _make_pretty(
            html=html,
            css_rules=css_rules,
            merge_groups=merge_groups,
            config=CssPrettyConfig(
                file_skeleton=False,
                section_comments=False,
                multiline_threshold=3,
                merge_group_comment=True,
            ),
        )
        result = p.render()
        assert "3 个等价规则合并" in result
        assert ".a,\n.b,\n.c" in result

    def test_render_with_global_header(self):
        global_header = "* { margin: 0; padding: 0; }\n#canvas { width: 750px; }"
        html = '<div class="main"></div>'
        css_rules = {
            "#canvas": {"width": "750px", "position": "relative"},
            ".main": {"display": "flex"},
        }
        p = _make_pretty(
            html=html,
            css_rules=css_rules,
            global_header=global_header,
            config=CssPrettyConfig(file_skeleton=True, section_comment_style="single"),
        )
        result = p.render()
        assert "Reset" in result
        assert "画布" in result
        assert "图层样式" in result
        assert ".main" in result


# ===========================================================================
# 边界 / 防御
# ===========================================================================

class TestEdgeCases:
    def test_empty_css_rules(self):
        p = _make_pretty(css_rules={}, config=CssPrettyConfig(file_skeleton=False))
        result = p.render()
        assert isinstance(result, str)
        assert result.endswith("\n")

    def test_soup_none_no_crash(self):
        """soup=None 时不崩溃（降级到非 DOM 序）"""
        cp = CssPretty(
            soup=None,
            css_rules={".a": {"color": "red"}},
            config=CssPrettyConfig(file_skeleton=False, section_comments=False),
        )
        result = cp.render()
        assert ".a" in result

    def test_normalize_css_value_applied(self):
        """值经过 _normalize_css_value 规范化"""
        p = _make_pretty(
            css_rules={".x": {"width": "100.000px"}},
            config=CssPrettyConfig(file_skeleton=False, short_rule_max_props=6),
        )
        result = p.render()
        # _normalize_css_value 会去掉尾部多余零
        assert "100px" in result
