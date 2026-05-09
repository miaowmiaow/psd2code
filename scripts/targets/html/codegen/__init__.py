"""HTML codegen 子包：把图层树转换为 index.html / style.css / main.js。

公共入口：HTMLGenerator。保留 SimpleNamer / _esc / __version__ 的顶层可见性，
以便旧调用方 `from ...codegen.html_generator import HTMLGenerator, _esc` 继续工作。
"""

from .version import __version__
from .escape import _esc
from .naming import SimpleNamer
from .html_generator import HTMLGenerator

__all__ = ['HTMLGenerator', 'SimpleNamer', '_esc', '__version__']
