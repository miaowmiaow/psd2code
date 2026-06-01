# -*- coding: utf-8 -*-
"""语义词抽取（Semantic Token Extraction）

把原始 PSD 图层名归一化为**短、安全、有业务含义**的英文语义词，供：
  * CSS 类名生成（``targets/html/codegen/naming.py``）
  * 图片文件名生成（``common/utils.make_image_filename``）
  * 未来任何需要"人类可读短标识"的场景

两处调用者**必须共享同一套词表**，否则同一图层在类名里叫 ``btn``、在图片里
叫 ``anniu`` 将完全失去对应关系。

抽取规则（按优先级命中第一个）：
  1. 原名已是纯 ASCII 且仅含 ``[A-Za-z0-9_ -]`` → kebab 化后直接用。
  2. 命中语义关键词（中英文均支持，大小写不敏感）→ 返回对应英文语义词。
  3. 命中 PS 默认名正则（"图层 5"/"矩形 3 拷贝 2"/"矢量智能对象" 等）
     → 视为"没起名"，交给调用方用 ``ltype`` 兜底。
  4. 中文且未命中任何关键词 → pypinyin 拼音首词（仅保留**第一个中文片段**
     的拼音，避免 ``yuanjiaojuxing_3_kaobei`` 这种超长串）。
  5. 空串 / 仅标点 → ``""``（让调用方走 ltype 兜底）。

对外 API：
  * ``extract_semantic_token(name, ltype="") -> str``：返回 kebab-case 短词；
    若返回 ``""``，表示"没拿到有效语义"，调用方应用 ``ltype`` 兜底
    （例如 ``group``/``img``/``text``）。
  * ``is_default_ps_name(name) -> bool``：仅用于日志/调试。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 语义关键词表
# ---------------------------------------------------------------------------
# 结构说明：``(pattern, token)``。``pattern`` 为原图层名中需要匹配的**子串**
# （小写后比较），命中即返回对应的 ``token``。顺序即优先级——更具体的写在前面
# （如 "副标题" 必须在 "标题" 前面，否则会被后者吞掉）。
#
# 业务侧扩词只需在这里追加一行，不需要碰命名器代码。
_KEYWORDS: list[tuple[str, str]] = [
    # ---- 按钮类（子类型优先级从高到低）----
    ("立即领取", "btn-receive"),
    ("领取",     "btn-receive"),
    ("待开启",   "btn-locked"),
    ("去参与",   "btn-join"),
    ("去完成",   "btn-join"),
    ("参与",     "btn-join"),
    ("按钮",     "btn"),
    ("button",   "btn"),
    ("btn",      "btn"),

    # ---- 标题 ----
    ("主标题",   "title"),
    ("副标题",   "title-sub"),
    ("小标题",   "title-sub"),
    ("标题",     "title"),
    ("title",    "title"),
    ("solgan",   "slogan"),   # 常见拼写错误
    ("slogan",   "slogan"),

    # ---- 结构/背景 ----
    ("背景",     "bg"),
    ("底图",     "bg"),
    ("底框",     "frame"),
    ("边框",     "frame"),
    ("头像框",   "avatar-frame"),
    ("头像",     "avatar"),

    # ---- 业务元素（南瓜大作战主题词，作为示范）----
    ("南瓜",     "pumpkin"),
    ("大糖果",   "candy-big"),
    ("糖果",     "candy"),
    ("道具",     "prop"),
    ("奖励",     "reward"),

    # ---- 形状兜底 ----
    ("圆角矩形", "rounded"),
    ("圆角",     "rounded"),
    ("矩形",     "rect"),
    ("椭圆",     "ellipse"),
    ("形状",     "shape"),

    # ---- 通用英文语义词（原样 kebab）----
    ("icon",     "icon"),
    ("card",     "card"),
    ("item",     "item"),
    ("list",     "list"),
    ("row",      "row"),
    ("col",      "col"),
    ("group",    "group"),
    ("header",   "header"),
    ("footer",   "footer"),
    ("modal",    "modal"),
    ("popup",    "popup"),
    ("tab",      "tab"),
    ("tag",      "tag"),
    ("badge",    "badge"),
    ("dialog",   "dialog"),
    ("banner",   "banner"),
]


# PS 自动生成的默认名（没有任何业务含义）。命中则让调用方用 ltype 兜底。
_PS_DEFAULT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^图层\s*\d+"),
    re.compile(r"^矩形\s*\d+"),
    re.compile(r"^圆角矩形\s*\d+"),  # 纯 "圆角矩形 3" 没有别的信息时算默认名
    re.compile(r"^椭圆\s*\d+"),
    re.compile(r"^形状\s*\d+"),
    re.compile(r"^矢量智能对象"),
    re.compile(r"^组\s*\d+"),
    re.compile(r"^group\s*\d+", re.IGNORECASE),
    re.compile(r"^layer\s*\d+", re.IGNORECASE),
    re.compile(r"^rectangle\s*\d+", re.IGNORECASE),
    re.compile(r"^ellipse\s*\d+", re.IGNORECASE),
    # 原始资源哈希（如 "b9e7ecaf...104131-5yS"）
    re.compile(r"^[a-f0-9]{16,}", re.IGNORECASE),
]

# "拷贝 N" 后缀：无语义，抽取时应剔除再匹配关键词。
# 例："按钮 拷贝 3" → 先剥成 "按钮" 再找语义词。
_COPY_SUFFIX = re.compile(r"(\s*(拷贝|copy|副本)\s*\d*)+\s*$", re.IGNORECASE)

# 允许出现在 kebab-case token 里的字符。
_SAFE_CHARS = re.compile(r"[^a-z0-9-]+")


def is_default_ps_name(name: str) -> bool:
    """判断图层名是否为 PS 自动生成的默认名（没有业务含义）。"""
    stripped = (name or "").strip()
    if not stripped:
        return True
    for pat in _PS_DEFAULT_PATTERNS:
        if pat.match(stripped):
            return True
    return False


def _strip_copy_suffix(name: str) -> str:
    """剥去 ``拷贝 N`` / ``copy N`` 等无语义后缀。"""
    return _COPY_SUFFIX.sub("", name).strip()


def _match_keyword(lower_name: str) -> str | None:
    """按优先级扫描关键词表。"""
    for pattern, token in _KEYWORDS:
        if pattern in lower_name:
            return token
    return None


def _to_kebab(raw: str) -> str:
    """把 ASCII 字符串归一化为 kebab-case，保证全安全字符。

    CSS 标识符规范要求不能以数字开头（否则 ``.18th-anniversary`` 是无效选择器，
    浏览器会完全忽略该规则）。若归一化后仍以数字开头，则加 ``n`` 前缀。
    """
    # 下划线、空格、点号都视为分隔符
    s = re.sub(r"[_\s.]+", "-", raw.lower())
    s = _SAFE_CHARS.sub("-", s)        # 其他非法字符也归一化为 "-"
    s = re.sub(r"-+", "-", s).strip("-")
    # CSS 标识符不能以数字或连字符+数字开头 → 加 "n" 前缀
    if s and s[0].isdigit():
        s = "n" + s
    return s


def _first_chinese_segment_pinyin(name: str) -> str:
    """取第一个中文片段转拼音（只取首段，避免超长）。

    例："圆角矩形 3 拷贝" → ``"yuanjiaojuxing"``；但因为已经剥掉 "拷贝" 后缀，
    实际输入会是 "圆角矩形 3" → 关键词表能直接命中 ``rounded``，不会走到这里。
    真正落到这里的是：业务自定义中文名未命中关键词的情况，例如 "节日氛围图"
    → ``"jieri"``（仅取前一个中文片段，至多 3 个汉字，再截断）。
    """
    try:
        from pypinyin import lazy_pinyin, Style
    except ImportError:
        # 降级：整段中文剥掉
        return ""

    m = re.search(r"[\u4e00-\u9fff]+", name)
    if not m:
        return ""
    chunk = m.group(0)[:3]   # 最多取前 3 个汉字拼音拼起来
    return "".join(lazy_pinyin(chunk, style=Style.NORMAL))


def extract_semantic_token(name: str, ltype: str = "") -> str:
    """从原始图层名抽取语义 token。

    参数：
        name: 原始图层名（可能含中文、空格、括号、PS 默认名等）
        ltype: 图层类型（``"group"`` / ``"image"`` / ``"text"`` / ``"shape"`` 等）
            当前未使用，保留给未来"按类型分别走不同抽取策略"的扩展；调用方传入
            后，本函数返回 ``""`` 时仍需调用方自行拼 ``ltype`` 兜底。

    返回：
        kebab-case 语义 token，如 ``"btn-receive"`` / ``"title-sub"`` /
        ``"rounded"``；若无法识别，返回 ``""``（调用方用 ``ltype`` 兜底）。
    """
    if not name:
        return ""

    _ = ltype  # 预留签名参数，当前实现未使用（将来可按 ltype 分流）

    # 1. 剥掉 "拷贝" 类后缀
    cleaned = _strip_copy_suffix(name)
    lower = cleaned.lower()

    # 2. 命中语义关键词 → 直接返回
    hit = _match_keyword(lower)
    if hit:
        return hit

    # 3. PS 默认名 → 交给 ltype 兜底
    if is_default_ps_name(cleaned):
        return ""

    # 4. 纯 ASCII 路径
    if re.fullmatch(r"[A-Za-z0-9_\s.\-]+", cleaned):
        # 纯数字（如 "01"、"10"）视作无语义——归 ltype 兜底
        if re.fullmatch(r"[\s\d.\-_]+", cleaned):
            return ""
        token = _to_kebab(cleaned)
        return token[:20] if token else ""

    # 5. 含中文且未命中词表 → 取首中文片段拼音
    pinyin = _first_chinese_segment_pinyin(cleaned)
    if pinyin:
        return _to_kebab(pinyin)[:20]

    # 6. 全是标点/奇怪字符 → 让调用方兜底
    return ""


__all__ = ["extract_semantic_token", "is_default_ps_name"]
