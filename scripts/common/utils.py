#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PSD2HTML 工具模块

职责：
  * 图片文件命名（语义 token + 内容哈希短指纹）
  * 文件名清理（中文 → 拼音 / 语义抽取）

命名规则（2026-04 起）：
  ``<semantic>-<hash6>.<ext>``

  * ``semantic``：由 ``common.semantic.extract_semantic_token`` 从图层名抽取；
    PS 默认名（"矩形 3"/"图层 5"/"矢量智能对象" 等）自动过滤，走 ``ltype`` 兜底。
  * ``hash6``：**图片内容 md5 前 6 位**。好处：
      - 稳定：PSD 没改 → 产物名不变（git diff 友好、CDN 缓存友好）；
      - 天然去重：LayerExporter 本来就用 md5 做内容去重，两者口径一致。
  * 示例：
      旧：``yuanjiaojuxing_3_kaobei_10.png``  →  新：``rounded-a3f012.png``
      旧：``anniu_27.png``                    →  新：``btn-1c2a04.png``
      旧：``image_(1)_24.png``                →  新：``img-4e8c1d.png``  ← 括号/空格被剔除

向后兼容：
  * 旧签名 ``make_image_filename(name, max_length, fmt)`` **仍可用**——
    没传 ``content_hash`` 时降级到"全局递增计数器"方案，老调用方不用改。
  * 推荐新调用方显式传 ``content_hash=md5`` 获得稳定性收益。
"""

from __future__ import annotations

import re
from typing import Optional

from common.semantic import extract_semantic_token  # noqa: F401  (re-export, 兼容旧调用方)
from semantic import NameResolver
from semantic.name_resolver import get_default_resolver, reset_default_resolver


# ---------------------------------------------------------------------------
# 全局计数器（仅用于 content_hash 未提供时的降级兜底）
# ---------------------------------------------------------------------------

_image_counter: int = 0


def reset_image_counter() -> None:
    """重置图片全局计数器（每次转换前调用）。

    即便调用方都升级到 ``content_hash`` 模式，本计数器仍作为"名字冲突时"的
    追加后缀来源，因此 reset 仍需要调用。

    顺带把 semantic 的进程级共享 NameResolver 缓存清掉，避免
    上一次转换的图层名 → token 缓存污染本次转换（key 里有 layer_id，跨
    PSD 通常不会撞，但保险起见统一 reset）。
    """
    global _image_counter
    _image_counter = 0
    reset_default_resolver()


def next_image_id() -> int:
    """获取下一个唯一图片编号（降级路径用）。"""
    global _image_counter
    _image_counter += 1
    return _image_counter


# ---------------------------------------------------------------------------
# 名字冲突注册表
# ---------------------------------------------------------------------------
# key = 完整文件名（不含路径）；用于在同一次转换里检测"不同 content_hash 但
# 撞到同一文件名"的边界情况——例如图层名极短、hash 前 6 位又恰好相同。
_used_filenames: set[str] = set()


def reset_filename_registry() -> None:
    """重置文件名注册表（每次转换前调用）。"""
    _used_filenames.clear()


# ---------------------------------------------------------------------------
# 文件名抽取
# ---------------------------------------------------------------------------

# kebab-case 的合法字符：只保留 [a-z0-9-]
_UNSAFE = re.compile(r"[^a-z0-9-]+")


def sanitize_filename(name: str, max_length: int = 50) -> str:
    """把原始图层名转成安全的 kebab-case 短名。

    新实现：优先走语义抽取（共享 ``common.semantic`` 词表），不再盲目 pypinyin。
    对调用方保持 API 兼容——返回值仍是纯 ASCII 小写短名（不含扩展名）。

    Args:
        name: 原始图层名
        max_length: 最大长度（默认 50，新规则下实际产出通常 ≤20）

    Returns:
        kebab-case 短名；若完全提取不到，返回 ``"layer"``。
    """
    token = get_default_resolver().resolve_token(name or "", "")
    if not token:
        token = "layer"

    # 二次防御：确保只含 [a-z0-9-]
    token = _UNSAFE.sub("-", token.lower())
    token = re.sub(r"-+", "-", token).strip("-")
    return (token or "layer")[:max_length]


def make_image_filename(
    layer_name: str,
    max_length: int = 50,
    fmt: str = "png",
    *,
    content_hash: Optional[str] = None,
    ltype: str = "image",
) -> str:
    """生成图片文件名：``<semantic>-<hash6>.<ext>``。

    Args:
        layer_name: 原始图层名（允许中英混合、PS 默认名、含括号/空格等）
        max_length: 语义段最大长度（默认 50，实际通常更短）
        fmt: 图片扩展名（不含点号）
        content_hash: **推荐传入**的图片内容 md5 全值（或任意稳定哈希十六进制串）；
            传入则取前 6 位作为稳定短指纹；不传则降级到递增 id，文件名不稳定。
        ltype: 图层类型，用于 semantic 抽取失败时的兜底语义词
            （``image``→``img``，``shape``→``shape`` 等）。

    Returns:
        安全、短、全 ASCII 的文件名，例如 ``rounded-a3f012.png``。

    命名冲突处理：
        如果返回值在本次转换里已被占用（_used_filenames 命中），自动追加 ``-2/-3``
        后缀直到不冲突；同时打印 stderr 警告，提醒有罕见碰撞。
    """
    # 1) 语义段（统一走 semantic.NameResolver）
    token = get_default_resolver().resolve_token(layer_name or "", ltype)
    if not token:
        # ltype 兜底
        token = {"image": "img", "text": "text", "shape": "shape"}.get(ltype, "img")
    token = _UNSAFE.sub("-", token.lower()).strip("-") or "img"
    token = token[:max_length]

    # 2) 指纹段
    if content_hash and len(content_hash) >= 6:
        suffix = content_hash[:6].lower()
    else:
        # 降级：递增计数器（非稳定）
        suffix = f"n{next_image_id():04d}"

    base = f"{token}-{suffix}.{fmt}"

    # 3) 冲突兜底
    if base not in _used_filenames:
        _used_filenames.add(base)
        return base

    # 极罕见：同 semantic + 同 6 位 hash 但来自不同 layer
    # （content_hash 本应全局唯一，除非两个图层内容真的一样但调用方没走 dedup）
    for i in range(2, 100):
        candidate = f"{token}-{suffix}-{i}.{fmt}"
        if candidate not in _used_filenames:
            _used_filenames.add(candidate)
            return candidate

    # 理论到不了这里
    _used_filenames.add(base)
    return base


__all__ = [
    "reset_image_counter",
    "next_image_id",
    "reset_filename_registry",
    "sanitize_filename",
    "make_image_filename",
]
