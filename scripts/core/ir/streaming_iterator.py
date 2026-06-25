"""
流式 IR 迭代器 - 支持逐个节点的流式处理

第5周优化 (Day 23-24)：支持流式 IR 生成和消费
- StreamingIRIterator: 按 DFS 顺序流式生成节点
- IRBuffer: 节点缓冲区管理
- 支持预读和批处理

设计特点：
1. 完全无状态：支持多次迭代
2. 低内存占用：只缓冲 buffer_size 个节点
3. 兼容现有 IR：不改变 Document 结构
"""

from typing import List, Optional, Iterator, Tuple
from pathlib import Path
import logging

from core.ir.nodes import Node, GroupNode, ImageNode, TextNode
from core.ir.document import Document


logger = logging.getLogger(__name__)


class IRBuffer:
    """
    节点缓冲区
    
    管理一批节点的缓存，支持随机访问和顺序遍历。
    """

    def __init__(self, capacity: int = 100):
        self.capacity = capacity
        self.nodes: List[Node] = []
        self.position = 0

    def add(self, node: Node) -> None:
        """添加节点到缓冲区"""
        if len(self.nodes) >= self.capacity:
            self.nodes.pop(0)
        self.nodes.append(node)

    def peek(self, n: int = 1) -> List[Node]:
        """预读后续 n 个节点（不移动指针）"""
        remaining = self.nodes[self.position :]
        return remaining[:n]

    def get(self) -> Optional[Node]:
        """获取当前节点并移动指针"""
        if self.position < len(self.nodes):
            node = self.nodes[self.position]
            self.position += 1
            return node
        return None

    def reset(self) -> None:
        """重置缓冲区位置"""
        self.position = 0

    def is_empty(self) -> bool:
        """检查缓冲区是否为空"""
        return self.position >= len(self.nodes)

    def remaining_count(self) -> int:
        """返回剩余的节点数"""
        return len(self.nodes) - self.position


class StreamingIRIterator:
    """
    流式 IR 迭代器
    
    按 DFS 顺序逐个生成节点，支持预读和批处理。
    特别适合大型 IR 树的流式消费。
    """

    def __init__(
        self,
        document: Document,
        buffer_size: int = 100,
    ):
        """
        初始化流式迭代器
        
        Args:
            document: IR Document
            buffer_size: 缓冲区大小（节点数）
        """
        self.document = document
        self.buffer_size = buffer_size
        self.buffer = IRBuffer(capacity=buffer_size)
        self._init_position = 0
        self._total_nodes = 0
        self._current_node = 0

        # 预计算树的规模
        self._precompute_stats()

    def _precompute_stats(self) -> None:
        """预计算树的统计信息"""
        count = 0
        for _ in self.document.root.iter_all():
            count += 1
        self._total_nodes = count
        logger.info(f"StreamingIRIterator: 总共 {self._total_nodes} 个节点")

    def __iter__(self) -> "StreamingIRIterator":
        """支持迭代器协议"""
        self._init_position = 0
        self._current_node = 0
        return self

    def __next__(self) -> Node:
        """获取下一个节点"""
        if self._current_node >= self._total_nodes:
            raise StopIteration

        # 从 document 直接获取（不缓冲，保持简洁）
        node = self._get_node_at_index(self._current_node)
        self._current_node += 1
        return node

    def _get_node_at_index(self, index: int) -> Node:
        """
        获取第 index 个节点
        
        注意：这个方法每次调用都会进行 DFS 遍历到指定位置，
        因此性能与索引相关（O(n)）。对于频繁访问，
        应该使用缓存或预加载。
        """
        current_index = 0
        for node in self.document.root.iter_all():
            if current_index == index:
                return node
            current_index += 1

        raise IndexError(f"节点索引越界: {index}")

    def peek(self, n: int = 1) -> List[Node]:
        """
        预读后续 n 个节点
        
        Args:
            n: 预读的节点数
            
        Returns:
            节点列表
        """
        result = []
        for i in range(n):
            try:
                node = self._get_node_at_index(self._current_node + i)
                result.append(node)
            except IndexError:
                break
        return result

    def get_batch(self, batch_size: int = 10) -> List[Node]:
        """
        获取一批节点
        
        Args:
            batch_size: 批处理大小
            
        Returns:
            节点列表（可能少于 batch_size 个）
        """
        batch = []
        for _ in range(batch_size):
            if self._current_node >= self._total_nodes:
                break
            node = self._get_node_at_index(self._current_node)
            batch.append(node)
            self._current_node += 1
        return batch

    def progress(self) -> Tuple[int, int]:
        """
        获取进度信息
        
        Returns:
            (当前位置, 总数)
        """
        return (self._current_node, self._total_nodes)

    def progress_percent(self) -> float:
        """获取进度百分比"""
        if self._total_nodes == 0:
            return 0.0
        return (self._current_node / self._total_nodes) * 100.0

    def reset(self) -> None:
        """重置迭代器位置"""
        self._current_node = 0

    def statistics(self) -> dict:
        """获取统计信息"""
        leaf_count = 0
        group_count = 0

        for node in self.document.root.iter_all():
            if isinstance(node, GroupNode):
                group_count += 1
            else:  # ImageNode, TextNode, 等
                leaf_count += 1

        return {
            "total_nodes": self._total_nodes,
            "leaf_nodes": leaf_count,
            "group_nodes": group_count,
            "current_position": self._current_node,
            "buffer_size": self.buffer_size,
        }


class StreamingBatchIterator:
    """
    批处理流式迭代器
    
    支持按批处理节点，适合批量代码生成。
    """

    def __init__(
        self,
        document: Document,
        batch_size: int = 50,
    ):
        """
        初始化批处理迭代器
        
        Args:
            document: IR Document
            batch_size: 每批的节点数
        """
        self.iterator = StreamingIRIterator(document, buffer_size=batch_size)
        self.batch_size = batch_size

    def __iter__(self):
        """支持迭代器协议"""
        self.iterator.reset()
        return self

    def __next__(self) -> List[Node]:
        """获取下一批节点"""
        batch = self.iterator.get_batch(self.batch_size)
        if not batch:
            raise StopIteration
        return batch

    def total_batches(self) -> int:
        """计算总批数"""
        stats = self.iterator.statistics()
        return (stats["total_nodes"] + self.batch_size - 1) // self.batch_size


class StreamingDepthIterator:
    """
    按深度分层的流式迭代器
    
    支持按树的深度逐层处理节点。
    """

    def __init__(self, document: Document):
        """初始化深度迭代器"""
        self.document = document
        self._depth_layers = self._build_depth_layers()

    def _build_depth_layers(self) -> dict:
        """
        按深度分层构建节点列表
        
        Returns:
            {depth: [nodes]}
        """
        layers = {}
        for node in self.document.root.iter_all():
            depth = node.depth if hasattr(node, "depth") else 0
            if depth not in layers:
                layers[depth] = []
            layers[depth].append(node)
        return layers

    def get_layer(self, depth: int) -> List[Node]:
        """获取指定深度的所有节点"""
        return self._depth_layers.get(depth, [])

    def get_max_depth(self) -> int:
        """获取最大深度"""
        return max(self._depth_layers.keys()) if self._depth_layers else 0

    def iterate_by_depth(self) -> Iterator[Tuple[int, List[Node]]]:
        """按深度顺序迭代"""
        for depth in sorted(self._depth_layers.keys()):
            yield depth, self._depth_layers[depth]

    def statistics(self) -> dict:
        """获取分层统计"""
        return {
            "total_depths": len(self._depth_layers),
            "max_depth": self.get_max_depth(),
            "nodes_by_depth": {d: len(nodes) for d, nodes in self._depth_layers.items()},
        }
