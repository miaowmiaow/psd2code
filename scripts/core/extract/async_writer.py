"""
异步图片写入管理器（优化2-Day8）。

使用 ThreadPoolExecutor 进行非阻塞的磁盘 IO，让主线程继续导出。
MD5 去重检查在主线程中进行以确保准确性。
"""

from __future__ import annotations
from typing import Any
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading


class AsyncImageWriter:
    """异步图片写入管理。"""
    
    def __init__(self, max_workers: int = 2):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.lock = threading.Lock()
        self.enabled = True
    
    def submit_write(self, data: bytes, path: Path) -> None:
        """提交异步写入任务。"""
        if self.enabled:
            self.executor.submit(self._write_to_disk, data, path)
        else:
            self._write_to_disk(data, path)
    
    @staticmethod
    def _write_to_disk(data: bytes, path: Path) -> None:
        """执行磁盘写入（可在线程中运行）。"""
        path.write_bytes(data)
    
    def shutdown(self, wait: bool = True) -> None:
        """关闭线程池。"""
        self.executor.shutdown(wait=wait)
