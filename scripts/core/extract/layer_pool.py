"""
PIL Image 对象池管理（优化3-Day11）。

用于减少大型 PIL Image 对象频繁创建销毁的内存压力。
支持按尺寸/模式复用，自动驱逐超期对象。
"""

from __future__ import annotations
from typing import Any
from PIL import Image
from collections import deque
import threading


class ImagePool:
    """PIL Image 对象池，支持线程安全操作。"""
    
    def __init__(self, max_size: int = 10):
        self.pool: deque = deque(maxlen=max_size)
        self.lock = threading.Lock()
        self.stats = {'created': 0, 'reused': 0, 'evicted': 0}
    
    def get(self, size: tuple[int, int], mode: str = 'RGBA') -> Image.Image:
        """从池中获取图像对象，尺寸或模式不匹配则新建。"""
        with self.lock:
            while self.pool:
                img = self.pool.popleft()
                if img.size == size and img.mode == mode:
                    if mode == 'RGBA':
                        img.putalpha(0)
                    self.stats['reused'] += 1
                    return img
        
        self.stats['created'] += 1
        default_color = (0, 0, 0, 0) if mode == 'RGBA' else (255, 255, 255, 255)
        return Image.new(mode, size, default_color)
    
    def put(self, img: Image.Image) -> None:
        """将使用完的图像对象归还池。"""
        if img is None or img.size[0] * img.size[1] == 0:
            return
        
        with self.lock:
            before = len(self.pool)
            self.pool.append(img)
            if len(self.pool) < before + 1:
                self.stats['evicted'] += 1
    
    def clear(self) -> None:
        """清空池。"""
        with self.lock:
            self.pool.clear()
    
    def get_stats(self) -> dict[str, int]:
        """获取池统计数据。"""
        with self.lock:
            return dict(self.stats)
