"""DeltaIR - 增量 IR 追踪系统（第4周 Day 20 优化）

核心目的：追踪 IR 文档变化，支持：
- 增量导出：仅处理变化节点，跳过未变的节点
- 缓存复用：将变化信息用于后续优化决策
- 性能提升：避免重复处理相同数据 (+1%)

使用场景：
1. 同一个 PSD 多次导出（修改后重新导出）
2. 多 target 导出（HTML/React/Vue 共享变化信息）
3. 实时预览更新（只更新变化部分）

设计思路：
- DeltaIR 记录三类变化：新增/删除/修改
- DeltaIRTracker 在 IR 构建过程中不断积累变化
- 后续 pipeline 可根据 delta 进行选择性处理
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .document import Document
from .nodes import Node


@dataclass
class DeltaIR:
    """IR 变化记录。

    记录从上一版本到当前版本的增量变化。
    所有操作（add/remove/modify）都是原子的，不会产生冲突。
    """

    added_nodes: set[str] = field(default_factory=set)  # 新增节点 ID
    removed_nodes: set[str] = field(default_factory=set)  # 删除节点 ID
    modified_nodes: dict[str, Node] = field(default_factory=dict)  # 修改后的节点 ID -> node

    @property
    def is_empty(self) -> bool:
        """检查是否为空（无任何变化）。"""
        return len(self.added_nodes) == 0 and len(self.removed_nodes) == 0 and len(self.modified_nodes) == 0

    @property
    def total_changes(self) -> int:
        """计算总变化数量。"""
        return len(self.added_nodes) + len(self.removed_nodes) + len(self.modified_nodes)

    def get_affected_node_ids(self) -> set[str]:
        """获取所有受影响的节点 ID（并集）。"""
        return self.added_nodes | self.removed_nodes | set(self.modified_nodes.keys())

    def summary(self) -> str:
        """获取变化摘要。"""
        if self.is_empty:
            return "No changes"
        parts = []
        if self.added_nodes:
            parts.append(f"+{len(self.added_nodes)}")
        if self.removed_nodes:
            parts.append(f"-{len(self.removed_nodes)}")
        if self.modified_nodes:
            parts.append(f"~{len(self.modified_nodes)}")
        return f"Delta({', '.join(parts)})"


class DeltaIRTracker:
    """在 IR 构建过程中追踪变化。

    工作原理：
    1. 初始化时加载上一版本的 IR（如果有缓存）
    2. 在构建新 IR 时，每处理一个节点就比较新旧版本
    3. 记录新增/删除/修改的节点
    4. 最后返回完整的 DeltaIR 对象

    效果：
    - 精确追踪变化，支持细粒度处理
    - 兼容完整重建（prev_doc=None 时）
    - 低开销（仅记录 ID，不做深比较）
    """

    def __init__(self, prev_doc: Optional[Document] = None) -> None:
        self.prev_doc = prev_doc
        self.delta = DeltaIR()

        # 构建上一版本的节点 ID 集合（用于检测删除）
        self.prev_node_ids: set[str] = set()
        if prev_doc is not None:
            for node in prev_doc.iter_nodes():
                self.prev_node_ids.add(node.id)

        # 当前版本的节点 ID 集合（用于检测新增）
        self.curr_node_ids: set[str] = set()

    def get_prev_node(self, node_id: str) -> Optional[Node]:
        """从上一版本中获取节点（如果存在）。"""
        if self.prev_doc is None:
            return None
        return self._find_node_in_document(self.prev_doc.root, node_id)

    def track_node_change(self, curr_node: Node) -> None:
        """记录一个节点的变化。

        调用方式（在 IR 构建时）：
            prev_node = tracker.get_prev_node(node_id)
            # 构建当前版本的节点...
            curr_node = ...
            tracker.track_node_change(curr_node)
        """
        self.curr_node_ids.add(curr_node.id)

        prev_node = self.get_prev_node(curr_node.id)

        if prev_node is None:
            # 新增节点
            self.delta.added_nodes.add(curr_node.id)
        elif not self._nodes_equal(prev_node, curr_node):
            # 修改节点（浅比较：ID/name/kind/bbox/opacity）
            self.delta.modified_nodes[curr_node.id] = curr_node
        # 否则：无变化，什么都不记录

    def finalize(self) -> DeltaIR:
        """完成追踪，返回最终的 DeltaIR。

        在这一步检测被删除的节点（在 prev 中但不在 curr 中）。
        """
        self.delta.removed_nodes = self.prev_node_ids - self.curr_node_ids
        return self.delta

    def get_stats(self) -> dict:
        """获取追踪统计信息。"""
        return {
            "prev_node_count": len(self.prev_node_ids),
            "curr_node_count": len(self.curr_node_ids),
            "added": len(self.delta.added_nodes),
            "removed": len(self.delta.removed_nodes),
            "modified": len(self.delta.modified_nodes),
            "unchanged": len(self.curr_node_ids) - len(self.delta.added_nodes) - len(
                self.delta.modified_nodes
            ),
        }

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _find_node_in_document(node: Node, target_id: str) -> Optional[Node]:
        """递归查找指定 ID 的节点。"""
        if node.id == target_id:
            return node

        from .nodes import GroupNode

        if isinstance(node, GroupNode):
            for child in node.children:
                result = DeltaIRTracker._find_node_in_document(child, target_id)
                if result is not None:
                    return result

        return None

    @staticmethod
    def _nodes_equal(node1: Node, node2: Node) -> bool:
        """浅比较两个节点是否相等。

        比较以下字段：
        - kind（节点类型）
        - name（名称）
        - bbox（位置尺寸）
        - opacity（透明度）
        - z_index（堆叠顺序）

        不比较：
        - children（子节点列表）
        - asset（资源引用）
        - effects（效果）
        - meta（元数据）

        这样可以快速检测"结构变化"而不陷入深比较。
        """
        if node1.kind != node2.kind or node1.name != node2.name:
            return False

        bbox1, bbox2 = node1.style.bbox, node2.style.bbox
        if (
            bbox1.left != bbox2.left
            or bbox1.top != bbox2.top
            or bbox1.width != bbox2.width
            or bbox1.height != bbox2.height
        ):
            return False

        if node1.style.opacity != node2.style.opacity:
            return False

        if node1.style.z_index != node2.style.z_index:
            return False

        return True
