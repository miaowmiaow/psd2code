#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LayerExporter 上下文对象

为 LayerExporter 及其子模块（light_effect_renderer, clipping_handler, image_compositor）
提供共享状态管理，消除深层依赖耦合。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LayerExporterContext:
    """LayerExporter 导出上下文 - 子模块共享状态的单一来源。
    
    Attributes:
        canvas_width: 画布宽度
        canvas_height: 画布高度
        psd: PSD 文档对象
        images_dir: 图片输出目录
        image_hash_map: md5 → image_path 去重映射表
        ancestor_group_masks: 祖先组 mask 栈（处理 group mask 传播）
    """
    canvas_width: int
    canvas_height: int
    psd: Any
    images_dir: Path
    image_hash_map: dict[str, str] = field(default_factory=dict)
    ancestor_group_masks: list[tuple[Any, tuple[int, int, int, int]]] = field(default_factory=list)

    # 统计计数器
    exported_count: int = 0
    skipped_count: int = 0
    dedup_count: int = 0

    # 光效层穿透映射（由 LightEffectRenderer 填充）
    penetrate_map: dict[int, Any] = field(default_factory=dict)
    suppressed_light_layers: set[int] = field(default_factory=set)
    fallback_light_layers: set[int] = field(default_factory=set)

    # 混合渲染临时缓存
    phase3_img_cache: dict[int, Any] = field(default_factory=dict)

    def clear_img_cache(self) -> None:
        """清空 Phase 3 临时图像缓存以释放内存。"""
        self.phase3_img_cache.clear()

    def add_image_mapping(self, md5: str, rel_path: str) -> None:
        """添加图片去重映射。"""
        if md5 not in self.image_hash_map:
            self.image_hash_map[md5] = rel_path

    def get_image_mapping(self, md5: str) -> str | None:
        """获取已有的图片去重映射。"""
        return self.image_hash_map.get(md5)

    def is_light_layer_suppressed(self, layer_id: int) -> bool:
        """判断光效层是否应被抑制独立导出。"""
        return layer_id in self.suppressed_light_layers

    def is_light_layer_fallback(self, layer_id: int) -> bool:
        """判断光效层是否应降级为 CSS blend。"""
        return layer_id in self.fallback_light_layers

    def increment_exported(self) -> None:
        """递增已导出图层计数。"""
        self.exported_count += 1

    def increment_skipped(self) -> None:
        """递增已跳过图层计数。"""
        self.skipped_count += 1

    def increment_dedup(self) -> None:
        """递增图片去重计数。"""
        self.dedup_count += 1

    def get_stats(self) -> dict[str, int]:
        """获取导出统计数据。"""
        return {
            'exported': self.exported_count,
            'skipped': self.skipped_count,
            'dedup': self.dedup_count,
        }
