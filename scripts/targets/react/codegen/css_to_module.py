# -*- coding: utf-8 -*-
"""CSS 改写器（React target 专用）。

本 target 不使用 CSS Module（见 html_to_jsx 模块注释），因此此处的工作只有：

  1. 把 ``url("images/xxx.png")`` 改写为 ``url("./assets/images/xxx.png")``，
     以便 Vite 正确解析并打包资源。
  2. 收集出现过的类名与图片引用，供调用方做交叉校验。

函数名保留 ``css_to_module`` 是历史设计残留（初版曾计划 CSS Module）；
为减少 diff 继续使用此命名，但语义现在是「React-friendly CSS 改写」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Set


_CLASS_SELECTOR_RE = re.compile(r"\.(-?[_a-zA-Z][_a-zA-Z0-9-]*)")
# 匹配 url(images/...)、url("images/...")、url('images/...')。
# 注意：文件名可能含括号（如 PSD 图层 ``image (1)`` 导出的 ``image_(1)_24.png``），
# 因此带引号分支不能把 ``)`` 纳入终止字符；无引号分支才排除 ``)``。
_URL_IMG_RE = re.compile(
    r'''url\(\s*(?:"images/(?P<dq>[^"]+)"|'images/(?P<sq>[^']+)'|images/(?P<nq>[^)"'\s]+))\s*\)'''
)


@dataclass
class CssModuleResult:
    css: str
    """Rewritten CSS content suitable for ``import './App.css'``."""

    class_names: Set[str] = field(default_factory=set)
    """All class names declared in the stylesheet."""

    image_refs: Set[str] = field(default_factory=set)
    """Image paths (``images/xxx.png``) referenced inside ``url(...)``."""


def css_to_module(
    css_content: str,
    *,
    images_prefix: str = "./assets/images/",
) -> CssModuleResult:
    """Rewrite stylesheet for React consumption.

    Args:
        css_content: Raw CSS text.
        images_prefix: Replacement prefix for ``url("images/...")``.

    Returns:
        CssModuleResult with rewritten CSS + collected metadata.
    """
    result = CssModuleResult(css="")

    for m in _CLASS_SELECTOR_RE.finditer(css_content):
        result.class_names.add(m.group(1))

    def _sub_url(m: "re.Match[str]") -> str:
        if m.group("dq") is not None:
            rel, quote = m.group("dq"), '"'
        elif m.group("sq") is not None:
            rel, quote = m.group("sq"), "'"
        else:
            rel, quote = m.group("nq"), ""
        result.image_refs.add(f"images/{rel}")
        return f"url({quote}{images_prefix}{rel}{quote})"

    result.css = _URL_IMG_RE.sub(_sub_url, css_content)
    return result
