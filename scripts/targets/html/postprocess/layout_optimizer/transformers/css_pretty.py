"""CSS 美化渲染器（CssPretty）— 开发者友好的 style_optimized.css 输出。

这是 LayoutOptimizer 的最终一步（在 CssDedup 之后），把 ``css_rules`` 字典 +
``merge_groups`` + ``soup``（DOM 顺序）渲染为最终 CSS 字符串。

与 ``common.css_utils.dict_to_css`` 的区别
==========================================

``dict_to_css`` 是"机械字典渲染"：选择器按字母序、属性按字母序，对 CSS 工具
链友好（diff 稳定）但**对人类不友好**：

- 全文 281 个块按字母序，调"页面顶部"区域要在文件中段找；
- 同块内 ``position`` / ``left`` / ``top`` 被字母序拆散到 ``background-*`` 之前；
- 合并组写成 289 字节单行选择器，git diff / 折叠 / grep 都吃力。

``CssPretty`` 在保持 W3C 等价的前提下重排：

- **Pass 1 文件骨架**：``Reset → @media → #canvas → 图层规则``，固定四段；
- **Pass 2 DOM 序**：图层规则按 ``index_optimized.html`` 自顶向下出现顺序，
  并在版块边界（顶级 group / ``bankuai-*``）插入小注释；
- **Pass 3 属性分段**：每块内属性按 ``定位 / 盒模型 / 排版 / 外观 / 混合 / 其他``
  分段，段间空一行；
- **Pass 4 合并组多行**：成员 ≥ 3 时选择器逐行展开，并加 ``/* ↳ N 个等价规则 */`` 注释；
- **Pass 5 短规则单行**：单选择器 + 属性 ≤ 2 → 单行紧凑输出。

所有 Pass 通过 ``CssPrettyConfig`` 单独开关；失败可降级到 ``dict_to_css``。

约束（不破坏的）
================
- ``css_rules`` 字典本身不变（下游 react/vue 仍按选择器查样式）；
- 所有数值都过 ``_normalize_css_value``（继承 ``dict_to_css`` 的精度规范化）；
- 合并组的属性 dict 已由 ``CssDedup`` 保证组内逐字相等。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from common.css_utils import _normalize_css_value  # type: ignore


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class CssPrettyConfig:
    """CssPretty 的所有开关。

    两个预设风格（``style``）：

    * ``"expanded"``：开发者友好的全展开模式（旧默认）—— 属性分段标题 + 段间空行 +
      合并组多行 + 坐标溯源注释。可读性最高，但行数膨胀（约比 figma 多 6×）。
    * ``"compact"``：紧凑模式（新默认）—— 关闭属性分段、关闭坐标溯源、段间用单行
      `/* ---- xx ---- */` 注释、短规则单行（≤6 字段）、合并组多行阈值 ≥4。
      可读性接近手写 CSS，行数与 figma 同量级。

    用法：
      * 显式 ``style="expanded"`` / ``style="compact"`` 选择预设；
      * 或者直接覆盖单个字段（如 ``CssPrettyConfig(style="compact", coord_provenance=True)``
        既要紧凑又要保留溯源）。
    """

    style: str = "compact"  # "expanded" | "compact"

    enabled: bool = True
    # Pass 1：文件骨架（Reset / @media / #canvas / 图层四段）
    file_skeleton: bool = True
    # Pass 2：图层规则按 DOM 顺序排序
    dom_order: bool = True
    # Pass 2 子开关：在版块切换处插入注释
    section_comments: bool = True
    # Pass 2 子开关：段落注释样式 —— "framed"（4 行框框）/ "single"（1 行）
    section_comment_style: Optional[str] = None  # 由 style 预设决定
    # Pass 3：块内属性按维度分段
    property_grouping: Optional[bool] = None  # 由 style 预设决定
    # 块内属性 ≤ 此值时不分段（避免短规则被段标题撑大；阈值取自实测分布）
    property_grouping_min_props: int = 8
    # Pass 4：合并组多行展开
    merge_group_multiline: bool = True
    multiline_threshold: Optional[int] = None  # 由 style 预设决定
    # 合并组上方是否显示 ``/* ↳ N 个等价规则合并 */`` 注释
    merge_group_comment: bool = True
    # Pass 5：单选择器 + 属性 ≤ N 时单行输出
    short_rule_inline: bool = True
    short_rule_max_props: Optional[int] = None  # 由 style 预设决定
    # 优化2-Day7：合并组选择器分割
    # 当一个合并组包含 > max_selectors_per_group 个选择器时，
    # 分割成多个规则块避免单行选择器过长（便于 git diff 和可读性）
    max_selectors_per_group: int = 15  # 超过此数会分割
    # P2b「坐标溯源注释」：每条 ``.<class>`` 规则上方加一行注释，标注
    # 该 class 对应 PSD 图层 id / 原名 / 类型 / 父 layer id。
    # 数据来源：从 soup 中按首类名查到该元素，读 ``id`` / ``data-name`` /
    # ``data-type`` 以及 ``parent.get('id')``。绝对坐标从 ``props['left/top']`` 读。
    # 注释**不影响 W3C 等价**，仅作开发者排查锚点。
    coord_provenance: Optional[bool] = None  # 由 style 预设决定
    # 同一类被多元素复用时（P0a 合并后非常常见），只输出"代表元素"的注释。
    # 默认输出第 1 个匹配元素的元数据。
    coord_provenance_only_first: bool = True

    def __post_init__(self) -> None:
        # 应用 style 预设：仅对未被显式设置（保持默认 None）的字段填充
        if self.style == "expanded":
            defaults = dict(
                section_comment_style="framed",
                property_grouping=True,
                multiline_threshold=3,
                short_rule_max_props=2,
                coord_provenance=True,
            )
        else:  # compact（默认）
            defaults = dict(
                section_comment_style="single",
                property_grouping=False,
                multiline_threshold=4,
                short_rule_max_props=6,
                coord_provenance=False,
            )
        for k, v in defaults.items():
            if getattr(self, k) is None:
                setattr(self, k, v)


# ---------------------------------------------------------------------------
# 属性分组规则（写死，避免 bikeshed）
# ---------------------------------------------------------------------------

# 每段：(段名, 段内属性顺序)。属性匹配采用前缀匹配（前缀放第一段命中即归属，避免重复）。
_PROPERTY_GROUPS: List[Tuple[str, List[str]]] = [
    ("定位", [
        "position",
        "left", "top", "right", "bottom",
        "z-index",
        "transform", "transform-origin",
    ]),
    ("盒模型", [
        "display",
        "flex", "flex-direction", "flex-wrap", "flex-flow",
        "align-items", "align-content", "align-self",
        "justify-content", "justify-items", "justify-self",
        "gap", "row-gap", "column-gap",
        "width", "height", "min-width", "min-height", "max-width", "max-height",
        "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
        "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
        "border", "border-top", "border-right", "border-bottom", "border-left",
        "border-width", "border-style", "border-color",
        "box-sizing",
        "overflow", "overflow-x", "overflow-y",
    ]),
    ("排版", [
        "color",
        "font", "font-family", "font-size", "font-weight", "font-style", "font-variant",
        "line-height",
        "text-align", "text-decoration", "text-transform", "text-overflow",
        "letter-spacing", "word-spacing",
        "white-space", "word-break", "word-wrap",
    ]),
    ("外观", [
        "background", "background-image", "background-color",
        "background-position", "background-repeat", "background-size",
        "background-attachment", "background-clip", "background-origin",
        "border-radius",
        "box-shadow",
        "filter", "backdrop-filter",
        "opacity",
        "cursor", "pointer-events",
        "visibility",
    ]),
    ("混合", [
        "mix-blend-mode",
        "isolation",
    ]),
]

# 段内属性优先级（越小越靠前），未列入的归到"其他"段尾
_PROPERTY_RANK: Dict[str, Tuple[int, int]] = {}
for _gi, (_gname, _props) in enumerate(_PROPERTY_GROUPS):
    for _pi, _p in enumerate(_props):
        _PROPERTY_RANK[_p] = (_gi, _pi)


def _prop_section(prop: str) -> Tuple[int, int, str]:
    """返回 (段索引, 段内顺序, 段名)。未知属性进"其他"段（索引 = 段数）。"""
    rank = _PROPERTY_RANK.get(prop)
    if rank is not None:
        gi, pi = rank
        return gi, pi, _PROPERTY_GROUPS[gi][0]
    return len(_PROPERTY_GROUPS), 0, "其他"


# ---------------------------------------------------------------------------
# 选择器自然序排序（v-stack-7 < v-stack-10）
# ---------------------------------------------------------------------------

_NUM_TOKEN_RE = re.compile(r'(\d+)')


def _natural_key(sel: str):
    """``.v-stack-10`` → ``['.v-stack-', 10]``，使数字段按数值比较。"""
    return [int(t) if t.isdigit() else t for t in _NUM_TOKEN_RE.split(sel)]


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class CssPretty:
    """CSS 美化渲染器。

    输入：
        - soup           ：BeautifulSoup 解析的优化后 HTML（用于 DOM 序）
        - css_rules      ：``{selector: {prop: value}}``
        - merge_groups   ：``[[sel, sel, ...], ...]``，每组属性等价
        - global_header  ：``extract_global_css_header`` 的产出（含 *、body、@media）
        - config         ：``CssPrettyConfig``

    输出：
        ``render() -> str``，最终写盘的 CSS 文本。
    """

    def __init__(
        self,
        soup,
        css_rules: Dict[str, Dict[str, str]],
        merge_groups: Optional[List[List[str]]] = None,
        global_header: str = "",
        config: Optional[CssPrettyConfig] = None,
    ):
        self.soup = soup
        self.css_rules = css_rules
        self.merge_groups = merge_groups or []
        self.global_header = global_header or ""
        self.config = config or CssPrettyConfig()
        # P2b：selector → 代表元素的 PSD 元数据，懒加载（首次 _render_rule 触发）
        self._provenance_cache: Optional[Dict[str, Dict[str, Optional[str]]]] = None

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    def render(self) -> str:
        cfg = self.config
        if not cfg.enabled:
            # 兜底：调用方应自己降级到 dict_to_css；这里返回空让上层察觉
            return ""

        # #canvas 单独处理：从 css_rules 抽出来塞到"画布"段，避免按 DOM 序排到中段
        canvas_rule = self.css_rules.get('#canvas')

        # 选择器→所属合并组 idx；选择器→组内排序后的成员列表
        sel_to_group: Dict[str, int] = {}
        for idx, group in enumerate(self.merge_groups):
            for s in group:
                sel_to_group[s] = idx

        # 已被合并组消化的选择器集合
        consumed: set = set()
        for group in self.merge_groups:
            members = [s for s in group if s in self.css_rules and self.css_rules[s]]
            if len(members) >= 2:
                consumed.update(members)

        # 组装"待输出实体"列表：
        #   ('rule', selector)            —— 单条规则
        #   ('group', [sel, sel, ...])    —— 合并组规则
        #   ('section', '版块名')         —— 段间小注释
        # 顺序由 _order_layer_entries 决定。
        # #canvas 已在骨架段输出，DOM 序里跳过。
        entries = self._order_layer_entries(
            sel_to_group, consumed, skip_selectors={'#canvas'}
        )

        # 渲染
        out: List[str] = []
        out.append(self._render_skeleton_head(canvas_rule=canvas_rule))
        if self.config.file_skeleton:
            out.append(self._render_main_section_title("4. 图层样式（按 DOM 顺序）"))
        for ent in entries:
            kind = ent[0]
            if kind == 'section':
                out.append(self._render_section_comment(ent[1]))
            elif kind == 'rule':
                sel = ent[1]
                out.append(self._render_rule(sel, self.css_rules[sel]))
            elif kind == 'group':
                members = ent[1]
                out.append(self._render_group(members))
        # 尾部换行
        return "\n".join(out).rstrip() + "\n"

    # ------------------------------------------------------------------
    # 段标题样式（framed = 4 行框框 / single = 1 行）
    # ------------------------------------------------------------------

    def _render_main_section_title(self, title: str) -> str:
        if self.config.section_comment_style == "single":
            return f"\n/* ===== {title} ===== */"
        return ("/* =========================================================\n"
                f" * {title}\n"
                " * ========================================================= */")

    def _render_top_section_title(self, title: str) -> str:
        """骨架段（Reset / 视口 / 画布 / 其它）的标题渲染。"""
        return self._render_main_section_title(title)

    @staticmethod
    def _strip_top_marker_comments(header: str) -> str:
        """剥掉 global_header 顶部的"文件标识注释"。

        PSD2HTML 写出的 style.css 顶部固定带 ``/* PSD2HTML vX.Y.Z — 文件名 */``
        + ``/* BEM + 语义化命名 */`` 这类版本注释，对开发者无价值。``file_skeleton``
        关闭模式下我们直接拼回 global_header，把这两行顺手剥掉。

        策略：只剥"开头连续的单行 ``/* ... */`` + 空行"，遇到第一个真实块（包括
        多行注释、selector）就停。不破坏 ``* { } / body { } / @media { }`` 等
        全局规则。
        """
        if not header:
            return header
        lines = header.splitlines()
        i = 0
        # 单行注释 ``/* ... */`` 的简单匹配
        comment_re = re.compile(r'^\s*/\*[^\n]*\*/\s*$')
        while i < len(lines):
            ln = lines[i]
            if comment_re.match(ln) or ln.strip() == "":
                i += 1
                continue
            break
        return "\n".join(lines[i:])

    @classmethod
    def _strip_top_canvas_block(cls, header: str) -> str:
        """剥掉 ``global_header`` 中的"顶层 ``#canvas { ... }`` 块"。

        仅剥**顶层**的 ``#canvas { ... }``（紧贴左边距、无嵌套），不动
        ``@media (...) { #canvas { ... } }`` 内部的 ``#canvas``。

        用于 ``file_skeleton=False`` 模式：拼回 ``global_header`` 的同时把旧
        ``#canvas`` 剥除，再追加最终态 ``css_rules['#canvas']``，避免重复定义。
        """
        if not header:
            return header
        out: List[str] = []
        blocks = cls._split_top_level(header)
        # _split_top_level 只识别"selector { ... }"块，不会包含纯注释/空白；
        # 我们需要保留原始行序、注释、空行。改为：用 split_top_level 找出
        # #canvas 块的字面 substring，再 string.replace 一次。
        for sel, full in blocks:
            if sel.strip() == '#canvas':
                # 直接从 header 删掉这个完整 block 的字面文本（首次出现）。
                idx = header.find(full)
                if idx >= 0:
                    header = header[:idx] + header[idx + len(full):]
                break  # 顶层应只有一个 #canvas
        # 清理可能因删除产生的连续多空行
        header = re.sub(r'\n{3,}', '\n\n', header)
        return header

    # ------------------------------------------------------------------
    # Pass 1：文件骨架
    # ------------------------------------------------------------------

    def _render_skeleton_head(self, canvas_rule: Optional[Dict[str, str]] = None) -> str:
        """渲染前 3 段（Reset / @media / #canvas）。

        策略：从 ``global_header`` 里把 ``* / body / @media`` 等顶层块
        逐个识别出来，按固定段顺序重排；其它未识别块原样保留到"其他全局"段尾。
        ``#canvas`` 优先用 ``css_rules['#canvas']`` 渲染（已被 LayoutOptimizer
        修改的最终态）；如果不在 css_rules，再回落到 global_header 中的版本。

        若 ``file_skeleton`` 关闭，原样返回 ``global_header``（剔除文件
        头部纯标识注释，如 ``/* PSD2HTML v1.1.0 ... */`` —— 这类版本/作者
        标识对开发者无价值，与"section_comments=False 想要的无注释观感"
        语义一致）；同时把 ``css_rules['#canvas']`` 的最终态追加在末尾
        （``global_header`` 内的旧 ``#canvas`` 块会被先剥除，避免重复）。
        """
        if not self.config.file_skeleton:
            header_clean = self._strip_top_marker_comments(self.global_header)
            # 剥除 global_header 中的旧 #canvas 块（保留 @media 中的不动），
            # 避免与最终态 css_rules['#canvas'] 重复定义。
            header_clean = self._strip_top_canvas_block(header_clean)
            head = header_clean.rstrip()
            # 追加最终态 #canvas（含 LayoutOptimizer 可能的修改）。这是关键：
            # css_rules['#canvas'] 是 LayoutOptimizer 看到的最终态，必须输出。
            # 在 file_skeleton=True 模式下由"段3：画布"输出；这里是 False 分支
            # 的对偶补全。
            if canvas_rule:
                if head:
                    head += "\n\n"
                head += self._render_rule('#canvas', canvas_rule)
            return head.rstrip() + "\n"

        # 切分顶层块
        blocks = self._split_top_level(self.global_header)

        # 分类
        reset_blocks: List[str] = []
        media_blocks: List[str] = []
        canvas_blocks_from_header: List[str] = []
        other_blocks: List[str] = []
        for sel, full in blocks:
            sel_clean = sel.strip()
            if sel_clean in ('*', 'body', 'html'):
                reset_blocks.append(full.strip())
            elif sel_clean.startswith('@media'):
                media_blocks.append(full.strip())
            elif sel_clean == '#canvas':
                canvas_blocks_from_header.append(full.strip())
            else:
                other_blocks.append(full.strip())

        parts: List[str] = []

        # 段 1：Reset & 全局
        if reset_blocks:
            parts.append(self._render_top_section_title("1. Reset & 全局"))
            parts.extend(reset_blocks)
            parts.append("")

        # 段 2：视口适配
        if media_blocks:
            parts.append(self._render_top_section_title("2. 视口适配"))
            parts.extend(media_blocks)
            parts.append("")

        # 段 3：画布（优先用 css_rules['#canvas']，回落到 header 版本）
        if canvas_rule:
            parts.append(self._render_top_section_title("3. 画布"))
            parts.append(self._render_rule('#canvas', canvas_rule))
            parts.append("")
        elif canvas_blocks_from_header:
            parts.append(self._render_top_section_title("3. 画布"))
            parts.extend(canvas_blocks_from_header)
            parts.append("")

        # 其它非 .class / #id 顶层块（罕见，兜底原样保留）
        if other_blocks:
            parts.append("/* ---- 其它全局 ---- */")
            parts.extend(other_blocks)
            parts.append("")

        return "\n".join(parts) + "\n"

    @staticmethod
    def _split_top_level(css_text: str) -> List[Tuple[str, str]]:
        """按 `{}` 配对切分顶层块，返回 [(selector, full_block), ...]。

        与 ``common.css_utils._iter_top_level_blocks`` 行为一致，但这里只关心
        全局 header 内的小段，不需要 body/start/end，简化实现。
        """
        out: List[Tuple[str, str]] = []
        i = 0
        n = len(css_text)
        while i < n:
            while i < n and css_text[i] in ' \t\r\n':
                i += 1
            if i >= n:
                break
            # 跳过纯注释段
            if css_text.startswith('/*', i):
                end = css_text.find('*/', i + 2)
                if end < 0:
                    break
                i = end + 2
                continue
            brace = css_text.find('{', i)
            if brace < 0:
                break
            sel = css_text[i:brace].strip()
            depth = 1
            j = brace + 1
            while j < n and depth > 0:
                c = css_text[j]
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                j += 1
            full = css_text[i:j]
            out.append((sel, full))
            i = j
        return out

    # ------------------------------------------------------------------
    # Pass 2：DOM 序 + 版块注释
    # ------------------------------------------------------------------

    def _order_layer_entries(
        self,
        sel_to_group: Dict[str, int],
        consumed: set,
        skip_selectors: Optional[set] = None,
    ) -> List[Tuple]:
        """按 DOM 顺序产出 entries 列表（rule / group / section）。

        ``skip_selectors``：不在图层段输出的选择器（如 ``#canvas`` 已在骨架段）。
        """
        cfg = self.config
        skip = skip_selectors or set()

        # 1) DOM 出现顺序：first_class 序列（去重，保留首次出现）
        dom_order: List[str] = []
        seen: set = set()
        # 同时记录"该 class 所属的版块名"，给 section 注释用
        sel_to_section: Dict[str, str] = {}
        if self.soup is not None and cfg.dom_order:
            for el in self.soup.find_all(True):
                cls = el.get('class') if hasattr(el, 'get') else None
                if not cls:
                    continue
                first = cls[0]
                sel = f'.{first}'
                if sel in seen:
                    continue
                seen.add(sel)
                if sel in skip:
                    continue
                if sel in self.css_rules:
                    dom_order.append(sel)
                    if cfg.section_comments:
                        sel_to_section[sel] = self._infer_section(el)

        # 2) DOM 中没出现的选择器（FlexApplier 生成的辅助类等），按字母序追加
        in_dom = set(dom_order)
        leftover = sorted(
            (s for s in self.css_rules.keys()
             if s not in in_dom and s not in skip and self.css_rules[s]),
            key=_natural_key,
        )

        # 3) 转成 entries：rule / group（合并组只在它的"代表选择器"位置出现一次）
        entries: List[Tuple] = []
        emitted_groups: set = set()
        last_section: Optional[str] = None

        def emit_at(sel: str, section: Optional[str]):
            nonlocal last_section
            if cfg.section_comments and section is not None and section != last_section:
                entries.append(('section', section))
                last_section = section
            if sel in sel_to_group:
                gid = sel_to_group[sel]
                if gid in emitted_groups:
                    return  # 同组的其它 selector 已被代表渲染
                # 取该组在 css_rules 中真实存在的成员
                members = [s for s in self.merge_groups[gid] if s in self.css_rules and self.css_rules[s]]
                if len(members) >= 2:
                    # 组内按自然序排
                    members_sorted = sorted(members, key=_natural_key)
                    entries.append(('group', members_sorted))
                    emitted_groups.add(gid)
                else:
                    # 防御：组内不足 2 → 当单条
                    entries.append(('rule', sel))
            else:
                entries.append(('rule', sel))

        for sel in dom_order:
            emit_at(sel, sel_to_section.get(sel))

        # leftover：放在"工具类 / 合并组（非 DOM 直挂）"段
        if leftover or any(g not in emitted_groups for g in range(len(self.merge_groups))):
            if cfg.section_comments:
                entries.append(('section', '工具类 / 合并组（跨版块复用）'))
                last_section = '工具类 / 合并组（跨版块复用）'
            for sel in leftover:
                emit_at(sel, None)
            # 兜底：还有合并组里成员都没在 DOM 里命中 → 直接补输出
            for gid, group in enumerate(self.merge_groups):
                if gid in emitted_groups:
                    continue
                members = [s for s in group if s in self.css_rules and self.css_rules[s]]
                if len(members) >= 2:
                    entries.append(('group', sorted(members, key=_natural_key)))
                    emitted_groups.add(gid)

        return entries

    def _infer_section(self, el) -> Optional[str]:
        """根据元素 DOM 路径推断"版块名"。

        策略：
          - 元素本身就是顶层版块容器（class 含 ``bankuai`` 或 ``section``）→ 用自身首类名
          - 否则向上找最近的 ``bankuai-*`` / ``section-*`` 祖先 → 用祖先首类名
          - 顶层裸子（直接挂在 ``#canvas`` 下且非版块）→ "全局元素"
          - 找不到 → None（不输出 section）
        """
        def _is_section_class(cls_name: str) -> bool:
            return ('bankuai' in cls_name) or ('section' in cls_name)

        # 1) 自己就是版块容器
        cls = el.get('class') or []
        if cls and _is_section_class(cls[0]):
            return cls[0]

        # 2) 向上找版块祖先
        cur = el.parent
        while cur is not None and getattr(cur, 'name', None):
            if cur.name == 'body' or cur.get('id') == 'canvas':
                return '全局元素'
            pcls = cur.get('class') or []
            if pcls and _is_section_class(pcls[0]):
                return pcls[0]
            cur = cur.parent
        return None

    # ------------------------------------------------------------------
    # 渲染：单条规则 / 合并组 / 段注释
    # ------------------------------------------------------------------

    def _render_section_comment(self, section: str) -> str:
        return f"\n/* ---- 版块: {section} ---- */"

    def _render_rule(self, selector: str, props: Dict[str, str]) -> str:
        cfg = self.config
        prov = self._provenance_comment(selector, props) if cfg.coord_provenance else ""
        # Pass 5：短规则单行
        max_short = cfg.short_rule_max_props or 2
        if (
            cfg.short_rule_inline
            and len(props) <= max_short
            and ',' not in selector
        ):
            inline = "; ".join(
                f"{k}: {_normalize_css_value(v)}" for k, v in self._sorted_props_flat(props)
            )
            return f"{prov}{selector} {{ {inline}; }}"
        body = self._render_props(props)
        return f"{prov}{selector} {{\n{body}\n}}"

    def _render_group(self, members: List[str]) -> str:
        """渲染合并组，支持大型选择器组的自动分割。
        
        优化2-Day7：当选择器数 > max_selectors_per_group 时，
        将其分割成多个块以避免单行过长（便于 git diff）。
        """
        cfg = self.config
        # 取代表属性
        props = self.css_rules[members[0]]
        n = len(members)
        prov = self._provenance_comment(members[0], props) if cfg.coord_provenance else ""
        
        # 大型合并组分割优化
        max_per_group = getattr(cfg, 'max_selectors_per_group', 15)
        if n > max_per_group:
            # 分割成多个块，每块最多 max_per_group 个选择器
            chunks = []
            for i in range(0, n, max_per_group):
                chunk = members[i:i+max_per_group]
                chunks.append(chunk)
            
            # 渲染每个块
            body = self._render_props(props)
            result_lines = []
            for idx, chunk in enumerate(chunks):
                if idx == 0 and prov:
                    result_lines.append(prov)
                if cfg.merge_group_comment:
                    result_lines.append(f"/* ↳ {len(chunk)}/{n} 个等价规则 */")
                chunk_sel = ",\n".join(chunk)
                result_lines.append(f"{chunk_sel} {{\n{body}\n}}")
            
            return "\n".join(result_lines)

        # 选择器排版
        multi_thr = cfg.multiline_threshold or 3
        if cfg.merge_group_multiline and n >= multi_thr:
            sels = ",\n".join(members)
            head_comment = f"/* ↳ {n} 个等价规则合并 */\n" if cfg.merge_group_comment else ""
            body = self._render_props(props)
            return f"{prov}{head_comment}{sels} {{\n{body}\n}}"
        # 短组：单行选择器
        sels = ", ".join(members)
        body = self._render_props(props)
        return f"{prov}{sels} {{\n{body}\n}}"

    # ------------------------------------------------------------------
    # P2b：坐标溯源注释
    # ------------------------------------------------------------------

    def _build_provenance_index(self) -> Dict[str, Dict[str, Optional[str]]]:
        """扫描 soup，为每个首类名记录"代表元素"的 PSD 元数据。

        返回 ``{first_class: {layer_id, name, type, parent_layer_id, parent_name}}``。
        """
        index: Dict[str, Dict[str, Optional[str]]] = {}
        only_first = self.config.coord_provenance_only_first
        for el in self.soup.find_all(True):
            classes = el.get('class') or []
            if not classes:
                continue
            first = classes[0]
            if only_first and first in index:
                continue
            parent = el.parent
            parent_id = None
            parent_name = None
            if parent is not None and hasattr(parent, 'get'):
                parent_id = parent.get('id')
                parent_name = parent.get('data-name')
            index[first] = {
                'layer_id': el.get('id'),
                'name': el.get('data-name'),
                'type': el.get('data-type'),
                'parent_layer_id': parent_id,
                'parent_name': parent_name,
            }
        return index

    def _provenance_comment(
        self, selector: str, props: Dict[str, str],
    ) -> str:
        """为 ``.<class>`` 规则生成一行尾随换行的注释；非 ``.``选择器返回空串。

        注释形如：
            /* PSD: layer-31 "background" type=image | abs(0,0,750x192) | parent=group-26 "兑奖卡片" */
        """
        if not selector.startswith('.'):
            return ""
        if self._provenance_cache is None:
            self._provenance_cache = self._build_provenance_index()
        cls = selector[1:]
        meta = self._provenance_cache.get(cls)
        if not meta:
            return ""

        parts: List[str] = []
        layer_id = meta.get('layer_id') or ''
        name = (meta.get('name') or '').replace('*/', '* /')
        ltype = meta.get('type') or ''
        if layer_id or name:
            head = layer_id or '?'
            if name:
                head += f' "{name}"'
            if ltype:
                head += f' type={ltype}'
            parts.append(head)

        # abs 坐标：仅在 props 同时含 left/top/width/height 时输出
        if all(k in props for k in ('left', 'top', 'width', 'height')):
            parts.append(
                f'abs({props["left"]},{props["top"]} '
                f'{props["width"]}×{props["height"]})'
            )

        parent_id = meta.get('parent_layer_id') or ''
        parent_name = (meta.get('parent_name') or '').replace('*/', '* /')
        if parent_id or parent_name:
            ptag = parent_id or '?'
            if parent_name:
                ptag += f' "{parent_name}"'
            parts.append(f'parent={ptag}')

        if not parts:
            return ""
        return f'/* PSD: {" | ".join(parts)} */\n'

    # ------------------------------------------------------------------
    # 渲染：属性体
    # ------------------------------------------------------------------

    def _render_props(self, props: Dict[str, str]) -> str:
        """块内属性输出。

        策略：
          - ``property_grouping=False`` → 全按 (段索引, 段内顺序, 属性名) 排序，无段标题
          - 属性数 < ``property_grouping_min_props`` → 同上（避免短规则被段标题撑大）
          - 否则按段输出 + 段标题 + 段间空行
        """
        cfg = self.config
        if (
            not cfg.property_grouping
            or len(props) < cfg.property_grouping_min_props
        ):
            lines = [
                f"  {k}: {_normalize_css_value(v)};"
                for k, v in self._sorted_props_flat(props)
            ]
            return "\n".join(lines)

        # 按段分组
        sectioned: Dict[int, List[Tuple[int, str, str]]] = {}
        for k, v in props.items():
            gi, pi, _ = _prop_section(k)
            sectioned.setdefault(gi, []).append((pi, k, v))

        # 段顺序：固定按 _PROPERTY_GROUPS 顺序，最后是"其他"段
        section_order = sorted(sectioned.keys())
        chunks: List[str] = []
        for gi in section_order:
            items = sectioned[gi]
            # 段内按段内顺序，再按属性名兜底
            items.sort(key=lambda t: (t[0], t[1]))
            section_name = (
                _PROPERTY_GROUPS[gi][0] if gi < len(_PROPERTY_GROUPS) else "其他"
            )
            section_lines: List[str] = [f"  /* {section_name} */"]
            for _pi, k, v in items:
                section_lines.append(f"  {k}: {_normalize_css_value(v)};")
            chunks.append("\n".join(section_lines))
        # 段间空一行（块内可读性）
        return "\n\n".join(chunks)

    @staticmethod
    def _sorted_props_flat(props: Dict[str, str]):
        """属性按 (段索引, 段内顺序, 属性名) 排序，给非分段输出复用。"""
        return sorted(
            props.items(),
            key=lambda kv: (*_prop_section(kv[0])[:2], kv[0]),
        )
