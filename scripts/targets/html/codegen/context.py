# -*- coding: utf-8 -*-
"""CodegenContext：HTML 代码生成过程中的共享状态容器。

之前这些状态散落在 HTMLGenerator 的 self 上，并被多个 mixin 隐式共享。
现在通过显式的 Context 对象在独立组件之间传递，消除 mixin 间的隐式耦合。
"""

from dataclasses import dataclass, field
from pathlib import Path

from .naming import SimpleNamer


@dataclass
class CodegenContext:
    """HTML/CSS/JS 代码生成的共享上下文。"""

    # 画布与产物信息
    psd_width: int
    psd_height: int
    output_dir: Path
    psd_name: str

    # 命名器（有自身状态，需要在 generate_html 开始时 reset）
    namer: SimpleNamer = field(default_factory=SimpleNamer)

    # 收集产物
    css_rules: list[str] = field(default_factory=list)

    def reset(self) -> None:
        """重置每一轮 generate_html 前需要重置的状态。"""
        self.css_rules = []
        self.namer.reset()
