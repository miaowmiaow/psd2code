#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PSD2HTML 配置模块
"""
import os

__version__ = '1.1.0'


class Config:
    """转换器配置"""

    # 输出目录（自动计算为 skill 下的 output 目录）
    # config.py 位于 scripts/config/，需要向上两层到达 skill 根目录
    OUTPUT_BASE_DIR: str = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'output'
    )

    # 图片格式
    IMAGE_FORMAT: str = 'png'

    # 文件名最大长度
    MAX_FILENAME_LENGTH: int = 50

    # 是否将组的 bbox 限制在画布范围内（避免超大组导致布局错乱）
    CONSTRAIN_GROUP_TO_CANVAS: bool = True

    # 是否裁剪超出画布的图片内容
    CROP_OVERFLOW_IMAGES: bool = True
