"""CSS解析工具

单一语义的长度解析函数：`parse_length(value, default=0.0)`。

历史上本文件并存过两份同义函数：
  - parse_px(value)   —— 仅能处理 "<number>px" 或纯数字，输入非法时抛 ValueError
  - parse_size(value) —— 用正则容错提取数字，失败返回 0.0

这是典型的"同一概念两份实现、行为微妙不同"的方案 1 陷阱：
调用方只要调错一个就会改变降级行为。现已合并为 `parse_length`，
显式接受 `default` 参数控制失败时的降级值：

  - 传入 `default=None`（默认传入具体 default 外显语义）时，不允许失败 → 抛 ValueError
  - 传入 `default=0.0`（或任意浮点）时，失败静默返回该 default

`parse_px` / `parse_size` 作为薄 wrapper 仍然保留（内部都委托 parse_length），
仅供尚未迁移的外部代码使用；新代码一律使用 `parse_length`。
"""

import re
from typing import Optional, Union


# 匹配"可选负号 + 数字（含小数）"；会截取字符串中第一个符合的片段。
_NUM_RE = re.compile(r'(-?\d+(?:\.\d+)?)')


class CSSParser:
    """CSS 解析工具类。所有长度解析统一走 `parse_length`。"""

    @staticmethod
    def parse_length(
        value: Union[str, int, float, None],
        default: Optional[float] = 0.0,
    ) -> float:
        """解析任意 CSS 长度值为浮点数。

        行为：
          - None / 空字符串 → 返回 default（或在 default=None 时抛 ValueError）
          - int / float      → 直接 float(value)
          - 字符串           → 先剥离 "px" 后 float()，不行则用正则提取第一个数字
                               仍失败则走 default / 抛错

        Args:
            value:   CSS 值，例如 "100px" / "-3px" / "auto" / 100 / None
            default: 失败时的降级值。显式传 None 则失败时抛 ValueError
                     （适合调用方有 try/except 兜底的场景）

        Returns:
            解析得到的 float
        """
        if value is None:
            if default is None:
                raise ValueError("parse_length: value is None and no default given")
            return default

        if isinstance(value, (int, float)):
            return float(value)

        s = str(value).strip()
        if not s:
            if default is None:
                raise ValueError("parse_length: empty string and no default given")
            return default

        # 快路径：直接剥 "px"
        try:
            return float(s.replace('px', '').strip())
        except ValueError:
            pass

        # 慢路径：正则提取第一个数字（容忍 "auto 100px" 这类噪声）
        m = _NUM_RE.search(s)
        if m:
            return float(m.group(1))

        if default is None:
            raise ValueError(f"parse_length: cannot parse {value!r}")
        return default

    # --- 兼容薄 wrapper（推荐新代码直接用 parse_length） -----------------

    @staticmethod
    def parse_px(value: Union[str, int, float, None]) -> float:
        """兼容 wrapper：失败时抛 ValueError（与历史行为一致）"""
        return CSSParser.parse_length(value, default=None)

    @staticmethod
    def parse_size(value: Union[str, int, float, None]) -> float:
        """兼容 wrapper：失败时静默降级为 0.0（与历史行为一致）"""
        return CSSParser.parse_length(value, default=0.0)
