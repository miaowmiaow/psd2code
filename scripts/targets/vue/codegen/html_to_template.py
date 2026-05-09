# -*- coding: utf-8 -*-
"""HTML → Vue ``<template>`` 转换器。

输入：优化后的完整 HTML 文档（含 ``<!DOCTYPE>``、``<html>``、``<body>``、``<div id="canvas">``）。
输出：仅保留 ``#canvas`` 子树的模板字符串（适合塞进 SFC 的 ``<template>`` 节点）+
伴随信息（图片引用集合、类名集合）。

为什么 Vue 模板比 JSX 简单很多
------------------------------
Vue ``<template>`` 与 HTML 语法**几乎等同**：
  * ``class`` 属性不需要改名（不像 JSX 要 ``className``）。
  * ``<img>`` 等 void 元素既可写 ``<img />`` 也可写 ``<img>``，但 Vue 模板编译
    要求所有标签**严格闭合**，所以这里统一写成自闭合形式。
  * ``data-*`` / ``aria-*`` / 大小写属性都直接保留。
  * 注释 ``<!-- ... -->`` 与 HTML 一致，无须转写。

因此本模块的核心工作只有：
  1. 提取 ``#canvas`` 子树（丢掉 ``<head>`` / ``<body>`` 等壳标签）。
  2. ``<img src="images/x.png">`` → ``<img src="./assets/images/x.png">``，
     并记录到 ``image_refs`` 供 Stage 复制资源。CSS 里的 ``url(...)`` 由
     :mod:`css_rewrite` 处理。
  3. void 元素统一自闭合，避免 Vue 模板编译报错。
  4. 收集出现过的类名供 Stage 做交叉校验。

实现方式：BeautifulSoup 解析树递归重写，避免用正则解析 HTML。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Set

from bs4 import BeautifulSoup, Comment, NavigableString, Tag


# Vue 模板里建议保持自闭合的 HTML void elements
VOID_ELEMENTS: Set[str] = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


@dataclass
class VueTemplateResult:
    template: str
    """``<template>`` 内部的片段字符串（不含 ``<template>`` 包裹）。"""

    image_refs: Set[str] = field(default_factory=set)
    """所有被引用的图片相对路径（如 ``images/xxx.png``）。"""

    class_names: Set[str] = field(default_factory=set)
    """出现过的 CSS 类名集合，方便后续生成 SFC 时做交叉校验。"""


def html_to_vue_template(
    html_content: str,
    *,
    canvas_selector: str = "#canvas",
    images_prefix: str = "./assets/images/",
) -> VueTemplateResult:
    """Convert a complete HTML document into a Vue ``<template>`` fragment.

    Args:
        html_content: Full HTML document string (the output of the HTML target).
        canvas_selector: CSS selector picking the root subtree to convert.
            By default we extract ``#canvas`` (ignoring ``<head>`` / ``<body>``).
        images_prefix: Replacement prefix for relative ``images/...`` paths
            inside ``src=`` / ``href=`` attributes.

    Returns:
        VueTemplateResult with the template string and collected metadata.
    """
    soup = BeautifulSoup(html_content, "html.parser")

    root = soup.select_one(canvas_selector)
    if root is None:
        # 兜底：没有 #canvas 时退回到 <body>
        root = soup.body or soup

    result = VueTemplateResult(template="")
    buf: List[str] = []
    _render_node(root, buf, indent=1, result=result, images_prefix=images_prefix)
    result.template = "".join(buf).rstrip() + "\n"
    return result


# ---------------------------------------------------------------------------
# 递归渲染
# ---------------------------------------------------------------------------

def _render_node(
    node,
    buf: List[str],
    indent: int,
    result: VueTemplateResult,
    images_prefix: str,
) -> None:
    if isinstance(node, Comment):
        text = str(node).strip()
        buf.append(f"{'  ' * indent}<!-- {text} -->\n")
        return

    if isinstance(node, NavigableString):
        text = str(node)
        if text.strip() == "":
            return
        # Vue 模板里 ``{{`` 会被识别为插值，需要转义
        buf.append(_escape_vue_text(text))
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

    if is_void:
        # void 元素强制自闭合，确保 Vue 模板编译器不会因为缺失闭合标签报错
        buf.append(f"{pad}<{name}{attrs_str} />\n")
        return

    if not has_children:
        buf.append(f"{pad}<{name}{attrs_str}></{name}>\n")
        return

    # 若唯一子节点是非空纯文本，合并为单行 <tag>text</tag>
    if (
        len(children) == 1
        and isinstance(children[0], NavigableString)
        and not isinstance(children[0], Comment)
    ):
        text = _escape_vue_text(str(children[0])).strip()
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

def _render_attrs(node: Tag, result: VueTemplateResult, images_prefix: str) -> str:
    parts: List[str] = []

    for key, value in node.attrs.items():
        # class 属性：保持原名，仅记录类名
        if key == "class":
            classes = value if isinstance(value, list) else str(value).split()
            classes = [c for c in classes if c]
            if not classes:
                continue
            for c in classes:
                result.class_names.add(c)
            parts.append(f'class="{" ".join(classes)}"')
            continue

        # src/href 路径重写：images/xxx.png -> ./assets/images/xxx.png
        if key in ("src", "href") and isinstance(value, str) and value.startswith("images/"):
            result.image_refs.add(value)
            value = images_prefix + value[len("images/"):]

        if isinstance(value, list):
            value = " ".join(value)

        # 布尔/空串属性
        if value is True or value == "":
            parts.append(key)
            continue

        parts.append(f'{key}={_attr_value(str(value))}')

    if not parts:
        return ""
    return " " + " ".join(parts)


def _attr_value(value: str) -> str:
    """Render an attribute value as a (HTML/Vue)-compatible attribute literal."""
    if '"' in value and "'" not in value:
        return f"'{value}'"
    # 双引号内的 " 用 &quot; 转义
    safe = value.replace('"', "&quot;")
    return f'"{safe}"'


# ---------------------------------------------------------------------------
# 文本转义
# ---------------------------------------------------------------------------

def _escape_vue_text(text: str) -> str:
    """Escape sequences that conflict with Vue template syntax.

    主要是 ``{{`` / ``}}``：Vue 默认把 ``{{ expr }}`` 识别为插值表达式，
    若原文本含双花括号，必须转义掉一个字符避免误识别。
    """
    return text.replace("{{", "{ {").replace("}}", "} }")
