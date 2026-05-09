# -*- coding: utf-8 -*-
"""Layer 1：清洗 + 扩展词典命中。

职责：
    把原始 PSD 图层名（可能含中文、空格、emoji、"拷贝 N"、全半角符号、数字
    后缀等）做轻量清洗，然后查 ``common/cn_dict.json`` 扩展词典，命中即产出
    ``NameCandidate(source="layer1", confidence=0.85)``。

设计原则：
    1. **不替代** ``common/semantic.py``：现有 _KEYWORDS 仍然是 fallback 路径，
       Layer 1 只是在它**之前**先用更大的词表试一次。这样升级更安全——即使
       cn_dict.json 漏词，最终行为也最多退化到"现状"。
    2. **长 key 优先**：词典加载时按 key 长度降序排序，避免 "立即领取按钮"
       命中 "按钮" 而错过 "立即领取"。
    3. **中英文都走小写比较**，但保留中文原字符（中文不需要 lowercase）。
    4. **匹配方式**：清洗后整名 ``in`` 比较（子串匹配），与 legacy 一致。
    5. **无副作用**：纯函数，无全局状态——词典加载用模块级缓存（仅一次 IO）。

公共 API：
    * ``Layer1Cleaner().analyze(name, ltype) -> NameCandidate | None``
    * ``Layer1Cleaner.dict_size`` （属性，调试 / 报告用）
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from semantic.name_resolver import NameCandidate

# ---------------------------------------------------------------------------
# 词典加载（模块级单例，避免重复 IO）
# ---------------------------------------------------------------------------

# 词典文件相对路径：scripts/common/cn_dict.json
_DICT_PATH = Path(__file__).resolve().parent.parent / "common" / "cn_dict.json"

# 摊平后的 [(pattern_lower, token, confidence), ...]，按 pattern 长度降序——
# 长串优先匹配。confidence 默认 0.85；``shapes_fallback`` 组弱信号 0.6
# （让 Layer 2 等结构化推断有机会压过它，详见 Layer2RoleInferer.R2）。
_FLAT_DICT: Optional[list[tuple[str, str, float]]] = None

# 弱信号词组：纯几何形状词（"矩形/圆/形状"），不构成强语义
_WEAK_GROUPS: frozenset[str] = frozenset({"shapes_fallback"})
_WEAK_CONFIDENCE: float = 0.6


def _load_flat_dict() -> list[tuple[str, str, float]]:
    """加载并摊平 cn_dict.json 为 [(pattern_lower, token, confidence), ...]。

    cn_dict.json 顶层是分组（buttons_actions / structure_layout / ...），
    每组是 {pattern: token}。本函数把所有组合并、跳过 ``_meta``、按 pattern
    长度降序排序。

    confidence：默认 ``Layer1Cleaner.DEFAULT_CONFIDENCE`` (0.85)；属于
    ``_WEAK_GROUPS`` 的组（当前仅 shapes_fallback）使用 ``_WEAK_CONFIDENCE``
    (0.6) —— 这样 Layer 2 的 R2 (shape 按钮强化, 0.7) 能压过纯"矩形→rect"。
    """
    global _FLAT_DICT
    if _FLAT_DICT is not None:
        return _FLAT_DICT

    if not _DICT_PATH.exists():
        _FLAT_DICT = []
        return _FLAT_DICT

    with open(_DICT_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    items: list[tuple[str, str, float]] = []
    for group_name, mapping in raw.items():
        if group_name.startswith("_") or not isinstance(mapping, dict):
            continue
        conf = (_WEAK_CONFIDENCE if group_name in _WEAK_GROUPS
                else Layer1Cleaner.DEFAULT_CONFIDENCE)
        for pattern, token in mapping.items():
            if not pattern or not isinstance(token, str) or not token:
                continue
            items.append((pattern.lower(), token, conf))

    # 长 key 优先（同长度按字典序）
    items.sort(key=lambda kv: (-len(kv[0]), kv[0]))
    _FLAT_DICT = items
    return _FLAT_DICT


# ---------------------------------------------------------------------------
# 清洗
# ---------------------------------------------------------------------------

# 与 common/semantic.py 的 _COPY_SUFFIX 一致——剥 "拷贝/copy/副本 N"
_COPY_SUFFIX = re.compile(r"(\s*(拷贝|copy|副本)\s*\d*)+\s*$", re.IGNORECASE)

# 全角 → 半角（仅针对常见标点和数字）。"（）！？，。：；" → "()!?,.:;"
_FULLWIDTH_MAP = str.maketrans({
    "（": "(", "）": ")",
    "【": "[", "】": "]",
    "！": "!", "？": "?",
    "，": ",", "。": ".",
    "：": ":", "；": ";",
    "“": '"', "”": '"',
    "‘": "'", "’": "'",
    "　": " ",
    "／": "/",
})

# 行内括号 / 方括号内容：通常是版本/序号/备注（"按钮(已选中)" → "按钮"）
_BRACKETED = re.compile(r"[\(\[][^\)\]]{0,20}[\)\]]")

# 末尾纯数字 + 可选短横/下划线（如 "按钮-3" / "icon_02"）
_TAIL_INDEX = re.compile(r"[\s\-_]*\d+\s*$")

# emoji / 不可见控制字符 / 私有区
_EMOJI = re.compile(
    r"[\U0001F300-\U0001FAFF"   # 大部分 emoji
    r"\U00002600-\U000027BF"    # 杂项符号
    r"\u200B-\u200F\uFEFF]+"   # 零宽字符
)


def clean_name(name: str) -> str:
    """规范化原始图层名，便于词典匹配。

    步骤：
        1) 去 emoji / 零宽字符
        2) 全角 → 半角
        3) 剥 "拷贝 N" 后缀
        4) 去括号备注（"按钮(已选中)" → "按钮"）
        5) 去末尾纯数字编号（"按钮 3" / "btn_02" → "按钮" / "btn"）
        6) 折叠多余空白

    保留中文字符与字母数字。返回值仍可能含中英文。
    """
    if not name:
        return ""
    s = name
    s = _EMOJI.sub(" ", s)
    s = s.translate(_FULLWIDTH_MAP)
    s = _COPY_SUFFIX.sub("", s).strip()
    s = _BRACKETED.sub(" ", s)
    s = _TAIL_INDEX.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

class Layer1Cleaner:
    """Layer 1：清洗 + 扩展词典查找。

    用法：
        cleaner = Layer1Cleaner()
        cand = cleaner.analyze("立即领取按钮 拷贝 2", "group")
        # cand == NameCandidate("btn-receive", 0.85, "layer1", reason=...)
        # 未命中返回 None

    线程安全：词典加载用模块级缓存（一次 IO），analyze 本身无状态。
    """

    # 命中扩展词典的置信度——比 fallback (0.5) 高，比未来 layer2 DOM 角色 (0.9)
    # 低，让 DOM 推断的"按钮形状/位置"在矛盾时压过纯文本词典。
    DEFAULT_CONFIDENCE: float = 0.85

    def __init__(self) -> None:
        # 不在 __init__ 加载词典（懒加载，单元测试可临时改 _DICT_PATH）
        pass

    # ------------------------------------------------------------------
    # 调试用属性
    # ------------------------------------------------------------------

    @property
    def dict_size(self) -> int:
        """当前加载的词典条目数（已摊平）。"""
        return len(_load_flat_dict())

    # ------------------------------------------------------------------
    # 主分析
    # ------------------------------------------------------------------

    def analyze(self, name: str, ltype: str = "") -> Optional[NameCandidate]:
        """对原始图层名查扩展词典，命中返回 NameCandidate，未命中返回 None。

        Args:
            name:  原始 PSD 图层名（未清洗）
            ltype: 图层类型，仅用于在 reason 里记录上下文，当前不参与匹配

        Returns:
            ``NameCandidate(source="layer1", confidence=0.85)`` 或 ``None``
        """
        if not name:
            return None

        cleaned = clean_name(name)
        if not cleaned:
            return None

        # 中文部分不做 lower（lower 对中文是 no-op，但保险起见显式处理）
        haystack = cleaned.lower()

        for pattern, token, conf in _load_flat_dict():
            if pattern in haystack:
                return NameCandidate(
                    name=token,
                    confidence=conf,
                    source="layer1",
                    reason=f"cn_dict hit: '{pattern}' -> '{token}' "
                           f"(cleaned='{cleaned}', ltype='{ltype}', conf={conf})",
                )

        return None


__all__ = ["Layer1Cleaner", "clean_name"]
