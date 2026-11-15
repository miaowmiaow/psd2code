"""统一日志门面 —— 库代码不再直接 ``print``。

背景：整条流水线历史上用 ``print`` 输出进度（含 emoji），坏处是
- 被当作库调用时无法静音 / 重定向（会直接污染宿主进程 stdout）；
- 无法分级（``--verbose`` 只能靠 ``ctx.log`` 的手写判断）；
- 无法在测试里用 ``caplog`` 断言，只能 ``capsys``。

约定：
- 库代码统一 ``logger = get_logger(__name__)``；
- 日志统一挂在 ``psd2code`` 命名空间下，便于整体调级 / 过滤；
- **只有 CLI 入口**（``psd_to_code.py``）调用 :func:`configure`：
  INFO 打到 stdout、WARNING 及以上打到 stderr（符合 CLI 惯例：
  ``cmd > log`` 不会吞掉错误，``2>`` 能单独收集失败原因）；
- 作为库导入 / 跑单测时不配置 handler，INFO 默认不输出。
"""

from __future__ import annotations

import logging
import sys

_ROOT_NAME = "psd2code"
_FORMAT = "%(message)s"

_configured = False
_stdout_handler: logging.Handler | None = None
_stderr_handler: logging.Handler | None = None


class _BelowLevelFilter(logging.Filter):
    """只放行低于 ``max_level`` 的记录（用于让 INFO 独占 stdout）。"""

    def __init__(self, max_level: int) -> None:
        super().__init__()
        self._max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - 覆写标准接口
        return record.levelno < self._max_level


def get_logger(name: str) -> logging.Logger:
    """返回模块级 logger（统一挂在 ``psd2code.*`` 命名空间下）。"""
    if name == _ROOT_NAME or name.startswith(_ROOT_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT_NAME}.{name}")


def configure(verbose: bool = False) -> None:
    """CLI 入口调用：配置 stdout / stderr 两条日志通道。

    - ``INFO``（及 ``verbose`` 下的 ``DEBUG``）→ stdout，仅输出 message，
      保持历史命令行观感（含 emoji 进度行）；
    - ``WARNING`` / ``ERROR`` / ``CRITICAL`` → stderr，便于 ``2>`` 单独收集。

    幂等：重复调用只更新级别，不重复挂 handler。

    Args:
        verbose: True 时输出 DEBUG 级别（含内部诊断信息）。
    """
    global _configured, _stdout_handler, _stderr_handler

    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter(_FORMAT)

    if not _configured:
        _stdout_handler = logging.StreamHandler(stream=sys.stdout)
        _stdout_handler.setFormatter(fmt)
        # WARNING 及以上交给 stderr，避免同一条记录出现两次
        _stdout_handler.addFilter(_BelowLevelFilter(logging.WARNING))

        _stderr_handler = logging.StreamHandler(stream=sys.stderr)
        _stderr_handler.setFormatter(fmt)
        _stderr_handler.setLevel(logging.WARNING)

        root = logging.getLogger()
        root.addHandler(_stdout_handler)
        root.addHandler(_stderr_handler)
        # 第三方库（psd-tools / PIL）默认只露 warning 以上，避免刷屏
        if root.level == logging.NOTSET or root.level > logging.WARNING:
            root.setLevel(logging.WARNING)
        _configured = True

    if _stdout_handler is not None:
        _stdout_handler.setLevel(level)
    logging.getLogger(_ROOT_NAME).setLevel(level)
