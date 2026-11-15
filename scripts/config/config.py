#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PSD2HTML 配置模块
"""
from pathlib import Path

# 产物格式版本（写入 metadata.json / CSS 头注释）。
# 与 targets/html/codegen/version.py 保持一致，由测试 test_version_sources_agree 守护。
__version__ = '1.1.0'

# 安装（非 editable）场景下的包目录标记：此时源码相对路径指向 site-packages，
# 绝不能把产物写进 Python 安装目录。
_SITE_MARKERS = ('site-packages', 'dist-packages')


def _resolve_output_base(
    package_root: Path | None = None,
    cwd: Path | None = None,
) -> str:
    """推导默认输出基目录。

    源码/可编辑安装：``<repo>/output``（保持历史行为，仓库根目录下）。
    wheel 安装：``__file__`` 位于 ``site-packages/``，上溯得到的目录是 Python
    安装目录 —— 在那里写产物既不可写也可能污染环境，因此退化到
    ``<cwd>/output``。

    Args:
        package_root: 用于测试注入；默认从 ``__file__`` 上溯三层。
        cwd: 用于测试注入；默认 ``Path.cwd()``。
    """
    root = package_root or Path(__file__).resolve().parent.parent.parent
    if any(marker in root.parts for marker in _SITE_MARKERS):
        return str((cwd or Path.cwd()) / 'output')
    return str(root / 'output')


class Config:
    """转换器配置"""

    # 输出目录（默认 `<repo>/output`；wheel 安装下为 `<cwd>/output`）
    OUTPUT_BASE_DIR: str = _resolve_output_base()

    # 图片格式
    IMAGE_FORMAT: str = 'png'

    # 文件名最大长度
    MAX_FILENAME_LENGTH: int = 50

    # 是否将组的 bbox 限制在画布范围内（避免超大组导致布局错乱）
    CONSTRAIN_GROUP_TO_CANVAS: bool = True

    # 是否裁剪超出画布的图片内容
    CROP_OVERFLOW_IMAGES: bool = True
