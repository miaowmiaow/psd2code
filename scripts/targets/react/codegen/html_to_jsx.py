# -*- coding: utf-8 -*-
"""HTML → JSX 转换器。

输入：优化后的完整 HTML 文档（包含 ``<!DOCTYPE>``、``<html>``、``<body>``、``<div id="canvas">``）。
输出：仅保留 ``#canvas`` 子树的 JSX 片段字符串 + 一些伴随信息（图片引用集合等）。

样式策略说明
------------
本 target 采用**普通全局 CSS**（``import './App.css'``），而非 CSS Module。
原因：
  * HTML target 生成的类名是 BEM 风格（``.section-foo__image``），**已经天然唯一**，
    不需要哈希来避免冲突。
  * 样式表里大量使用属性选择器（``[class*="__image"]``、``[class$="-container"]``），
    这类选择器在 CSS Module 下不会匹配哈希后的类名，除非用 ``:global`` 包裹整份 CSS，
    而那样又让 ``styles['foo']`` 拿不到映射——得不偿失。
  * 维持 ``className="foo bar"`` 字符串形式，JSX 直接与 HTML 一一对应，便于人类阅读/调试。

转换规则
--------
  1. 属性名映射：``class`` → ``className``，``for`` → ``htmlFor``，
     ``tabindex`` → ``tabIndex``，``readonly`` → ``readOnly`` 等常见 HTML → React 驼峰。
  2. ``data-*`` / ``aria-*`` 保持原名。
  3. 自闭合：``<img>``、``<br>``、``<hr>``、``<input>``、``<meta>``、``<link>`` 等在 JSX 里必须
     显式闭合（``<img ... />``）。
  4. ``className="foo bar"`` 保留为 **字符串属性**（不引入 CSS Module）。
  5. 图片路径：``<img src="images/x.png">`` 改写为 ``./assets/images/x.png``，
     并记录到 ``image_refs`` 以便 Stage 复制资源。CSS 里的 ``url("images/...")``
     由 css_to_module 模块处理。
  6. 注释 ``<!-- ... -->`` → ``{/* ... */}``。
  7. ``style="..."`` 内联字符串（罕见，本项目主要走 CSS）会尽力转成对象字面量。

实现方式：基于 BeautifulSoup 解析树递归重写。避免用正则解析 HTML。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Set

from bs4 import BeautifulSoup, Comment, NavigableString, Tag


# ---------------------------------------------------------------------------
# 属性名映射表（HTML 属性 -> JSX/React 属性）
# ---------------------------------------------------------------------------

HTML_TO_JSX_ATTR: dict[str, str] = {
    "class": "className",
    "for": "htmlFor",
    "tabindex": "tabIndex",
    "readonly": "readOnly",
    "maxlength": "maxLength",
    "minlength": "minLength",
    "colspan": "colSpan",
    "rowspan": "rowSpan",
    "autocomplete": "autoComplete",
    "autofocus": "autoFocus",
    "autoplay": "autoPlay",
    "contenteditable": "contentEditable",
    "spellcheck": "spellCheck",
    "crossorigin": "crossOrigin",
    "enctype": "encType",
    "formaction": "formAction",
    "formenctype": "formEncType",
    "formmethod": "formMethod",
    "formnovalidate": "formNoValidate",
    "formtarget": "formTarget",
    "frameborder": "frameBorder",
    "novalidate": "noValidate",
    "srcdoc": "srcDoc",
    "srclang": "srcLang",
    "srcset": "srcSet",
    "usemap": "useMap",
    "accept-charset": "acceptCharset",
    "http-equiv": "httpEquiv",
    # SVG 常见
    "stroke-width": "strokeWidth",
    "stroke-linecap": "strokeLinecap",
    "stroke-linejoin": "strokeLinejoin",
    "fill-rule": "fillRule",
    "clip-path": "clipPath",
    "xlink:href": "xlinkHref",
}

# JSX 里必须自闭合（HTML void elements）
VOID_ELEMENTS: Set[str] = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------

@dataclass
class JsxResult:
    jsx: str
    """JSX 片段字符串，不含任何 import / 组件包裹。"""

    image_refs: Set[str] = field(default_factory=set)
    """所有被引用的图片相对路径（如 ``images/xxx.png``）。"""

    class_names: Set[str] = field(default_factory=set)
    """出现过的 CSS 类名集合，方便后续生成 CSS 时做交叉校验。"""


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def html_to_jsx(
    html_content: str,
    *,
    canvas_selector: str = "#canvas",
    images_prefix: str = "./assets/images/",
) -> JsxResult:
    """Convert a complete HTML document into a JSX fragment.

    Args:
        html_content: Full HTML document string (the output of the HTML target).
        canvas_selector: CSS selector picking the root subtree to convert.
            By default we extract ``#canvas`` (ignoring ``<head>``/``<body>``).
        images_prefix: Replacement prefix for relative ``images/...`` paths
            inside ``src=``/``href=`` attributes.

    Returns:
        JsxResult with the JSX string and collected metadata.
    """
    soup = BeautifulSoup(html_content, "html.parser")

    root = soup.select_one(canvas_selector)
    if root is None:
        # 兜底：没有 #canvas 时退回到 <body>
        root = soup.body or soup

    result = JsxResult(jsx="")
    buf: List[str] = []
    _render_node(root, buf, indent=2, result=result, images_prefix=images_prefix)
    result.jsx = "".join(buf).rstrip() + "\n"
    return result


# ---------------------------------------------------------------------------
# 递归渲染
# ---------------------------------------------------------------------------

def _render_node(
    node,
    buf: List[str],
    indent: int,
    result: JsxResult,
    images_prefix: str,
) -> None:
    if isinstance(node, Comment):
        text = str(node).strip()
        buf.append(f"{'  ' * indent}{{/* {text} */}}\n")
        return

    if isinstance(node, NavigableString):
        text = str(node)
        if text.strip() == "":
            # 忽略纯空白节点；JSX 的排版由父级控制。
            return
        buf.append(_escape_jsx_text(text))
        return

    if not isinstance(node, Tag):
        return

    name = node.name
    # 文档节点或壳标签：只递归子节点。
    if name in ("[document]", "html", "head", "body"):
        for child in node.children:
            _render_node(child, buf, indent, result, images_prefix)
        return

    pad = "  " * indent
    attrs_str = _render_attrs(node, result, images_prefix)

    is_void = name in VOID_ELEMENTS
    children = [c for c in node.children if not _is_blank_text(c)]
    has_children = len(children) > 0

    if is_void or not has_children:
        buf.append(f"{pad}<{name}{attrs_str} />\n")
        return

    # 若唯一子节点是非空纯文本，合并为单行 <tag>text</tag>
    if (
        len(children) == 1
        and isinstance(children[0], NavigableString)
        and not isinstance(children[0], Comment)
    ):
        text = _escape_jsx_text(str(children[0])).strip()
        if text and "\n" not in text:
            buf.append(f"{pad}<{name}{attrs_str}>{text}</{name}>\n")
            return

    buf.append(f"{pad}<{name}{attrs_str}>\n")
    for child in children:
        _render_node(child, buf, indent + 1, result, images_prefix)
    buf.append(f"{pad}</{name}>\n")


def _is_blank_text(node) -> bool:
    return (
        isinstance(node, NavigableString)
        and not isinstance(node, Comment)
        and str(node).strip() == ""
    )


# ---------------------------------------------------------------------------
# 属性渲染
# ---------------------------------------------------------------------------

def _render_attrs(node: Tag, result: JsxResult, images_prefix: str) -> str:
    parts: List[str] = []

    for key, value in node.attrs.items():
        # class -> className，保留字符串形式（全局 CSS）
        if key == "class":
            classes = value if isinstance(value, list) else str(value).split()
            classes = [c for c in classes if c]
            if not classes:
                continue
            for c in classes:
                result.class_names.add(c)
            parts.append(f'className="{" ".join(classes)}"')
            continue

        # style="a: b; c: d" -> style={{ a: 'b', c: 'd' }}
        if key == "style":
            style_obj = _inline_style_to_object(str(value))
            if style_obj:
                parts.append(f"style={{{{{style_obj}}}}}")
            continue

        # src/href 路径重写：images/xxx.png -> ./assets/images/xxx.png
        if key in ("src", "href") and isinstance(value, str) and value.startswith("images/"):
            result.image_refs.add(value)
            value = images_prefix + value[len("images/"):]

        # data-* / aria-* 保留原名；其他走映射表
        if key.startswith("data-") or key.startswith("aria-"):
            jsx_name = key
        else:
            jsx_name = HTML_TO_JSX_ATTR.get(key, key)

        if isinstance(value, list):
            value = " ".join(value)

        # 布尔/空串属性
        if value is True or value == "":
            parts.append(jsx_name)
            continue

        parts.append(f"{jsx_name}={_attr_value(value)}")

    if not parts:
        return ""
    return " " + " ".join(parts)


def _attr_value(value: str) -> str:
    """Render an attribute value as a JSX attribute literal."""
    if '"' in value:
        # 用 JS 表达式字符串避免与 JSX 双引号冲突
        return "{" + _js_str(value) + "}"
    return '"' + value + '"'


def _js_str(s: str) -> str:
    """Safe single-quoted JS string literal."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


# ---------------------------------------------------------------------------
# 文本与内联样式
# ---------------------------------------------------------------------------

_JSX_TEXT_SUBS = {
    "{": "{'{'}",
    "}": "{'}'}",
    "<": "&lt;",
    ">": "&gt;",
}
_JSX_TEXT_RE = re.compile("|".join(re.escape(k) for k in _JSX_TEXT_SUBS))


def _escape_jsx_text(text: str) -> str:
    """Escape characters that are special inside JSX text nodes.

    Single-pass substitution so that replacements do not feed into each other
    (e.g. ``{`` → ``{'{'}`` should not trigger the ``{``/``}`` rules again).
    """
    return _JSX_TEXT_RE.sub(lambda m: _JSX_TEXT_SUBS[m.group(0)], text)


_STYLE_PROP_RE = re.compile(r"^\s*([-a-zA-Z]+)\s*:\s*(.+?)\s*$")


def _inline_style_to_object(style: str) -> str:
    """Convert ``a: 1px; b: red`` string into ``a: '1px', b: 'red'`` JSX object body."""
    items: List[str] = []
    for part in style.split(";"):
        if not part.strip():
            continue
        m = _STYLE_PROP_RE.match(part)
        if not m:
            continue
        prop, value = m.group(1), m.group(2)
        if not prop.startswith("--"):
            prop = _css_to_camel(prop)
            items.append(f"{prop}: {_js_str(value)}")
        else:
            items.append(f"{_js_str(prop)}: {_js_str(value)}")
    return ", ".join(items)


def _css_to_camel(name: str) -> str:
    head, *rest = name.split("-")
    return head + "".join(w.capitalize() for w in rest)
