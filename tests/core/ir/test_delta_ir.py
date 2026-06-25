"""DeltaIR 单元测试（第4周 Day 20）。"""

from __future__ import annotations

import pytest

from core.ir import (
    Document,
    GroupNode,
    ImageNode,
    Style,
    BBox,
    AssetRef,
)
from core.ir.delta_ir import DeltaIR, DeltaIRTracker


@pytest.fixture
def prev_document() -> Document:
    """构造上一版本的 IR 文档。"""
    bbox_root = BBox(left=0, top=0, right=100, bottom=100)
    bbox_img1 = BBox(left=10, top=10, right=30, bottom=30)
    bbox_img2 = BBox(left=40, top=40, right=60, bottom=60)

    img1 = ImageNode(
        id="img-1",
        name="image1",
        style=Style(bbox=bbox_img1, opacity=1.0, z_index=1),
        asset=AssetRef(kind="image", src="img1.png"),
    )

    img2 = ImageNode(
        id="img-2",
        name="image2",
        style=Style(bbox=bbox_img2, opacity=0.8, z_index=2),
        asset=AssetRef(kind="image", src="img2.png"),
    )

    root = GroupNode(
        id="root",
        name="root",
        style=Style(bbox=bbox_root, opacity=1.0, z_index=0),
        children=[img1, img2],
    )

    return Document(width=100, height=100, root=root, title="Previous")


@pytest.fixture
def curr_document_with_changes() -> Document:
    """构造当前版本的 IR 文档（包含变化）。

    变化：
    - img-1 保持不变
    - img-2 被删除
    - img-3 新增
    - img-4 新增
    """
    bbox_root = BBox(left=0, top=0, right=100, bottom=100)
    bbox_img1 = BBox(left=10, top=10, right=30, bottom=30)
    bbox_img3 = BBox(left=50, top=50, right=70, bottom=70)
    bbox_img4 = BBox(left=70, top=70, right=90, bottom=90)

    img1 = ImageNode(
        id="img-1",
        name="image1",
        style=Style(bbox=bbox_img1, opacity=1.0, z_index=1),
        asset=AssetRef(kind="image", src="img1.png"),
    )

    img3 = ImageNode(
        id="img-3",
        name="image3",
        style=Style(bbox=bbox_img3, opacity=0.9, z_index=3),
        asset=AssetRef(kind="image", src="img3.png"),
    )

    img4 = ImageNode(
        id="img-4",
        name="image4",
        style=Style(bbox=bbox_img4, opacity=0.7, z_index=4),
        asset=AssetRef(kind="image", src="img4.png"),
    )

    root = GroupNode(
        id="root",
        name="root",
        style=Style(bbox=bbox_root, opacity=1.0, z_index=0),
        children=[img1, img3, img4],
    )

    return Document(width=100, height=100, root=root, title="Current")


@pytest.fixture
def curr_document_with_modified() -> Document:
    """构造当前版本的 IR 文档（修改节点）。

    变化：
    - img-1 的 opacity 从 1.0 变为 0.5
    - img-2 保持不变
    """
    bbox_root = BBox(left=0, top=0, right=100, bottom=100)
    bbox_img1 = BBox(left=10, top=10, right=30, bottom=30)
    bbox_img2 = BBox(left=40, top=40, right=60, bottom=60)

    img1 = ImageNode(
        id="img-1",
        name="image1",
        style=Style(bbox=bbox_img1, opacity=0.5, z_index=1),  # 修改
        asset=AssetRef(kind="image", src="img1.png"),
    )

    img2 = ImageNode(
        id="img-2",
        name="image2",
        style=Style(bbox=bbox_img2, opacity=0.8, z_index=2),
        asset=AssetRef(kind="image", src="img2.png"),
    )

    root = GroupNode(
        id="root",
        name="root",
        style=Style(bbox=bbox_root, opacity=1.0, z_index=0),
        children=[img1, img2],
    )

    return Document(width=100, height=100, root=root, title="Current Modified")


class TestDeltaIR:
    """测试 DeltaIR 数据模型。"""

    def test_empty_delta(self) -> None:
        delta = DeltaIR()
        assert delta.is_empty
        assert delta.total_changes == 0
        assert delta.summary() == "No changes"

    def test_delta_with_adds(self) -> None:
        delta = DeltaIR(added_nodes={"n1", "n2"})
        assert not delta.is_empty
        assert delta.total_changes == 2
        assert "+" in delta.summary()

    def test_delta_with_removes(self) -> None:
        delta = DeltaIR(removed_nodes={"n1", "n2", "n3"})
        assert not delta.is_empty
        assert delta.total_changes == 3
        assert "-" in delta.summary()

    def test_delta_with_modifies(self) -> None:
        delta = DeltaIR(modified_nodes={"n1": None, "n2": None})  # type: ignore
        assert not delta.is_empty
        assert delta.total_changes == 2
        assert "~" in delta.summary()

    def test_get_affected_node_ids(self) -> None:
        delta = DeltaIR(
            added_nodes={"a1", "a2"},
            removed_nodes={"r1"},
            modified_nodes={"m1": None},  # type: ignore
        )
        affected = delta.get_affected_node_ids()
        assert affected == {"a1", "a2", "r1", "m1"}


class TestDeltaIRTracker:
    """测试 DeltaIRTracker 追踪功能。"""

    def test_tracker_with_no_prev(self) -> None:
        """不指定上一版本时，所有新节点都是新增。"""
        tracker = DeltaIRTracker(prev_doc=None)

        # 模拟构建三个节点
        tracker.track_node_change(
            ImageNode(
                id="n1",
                name="node1",
                style=Style(bbox=BBox(left=0, top=0, right=10, bottom=10)),
                asset=AssetRef(kind="image", src="n1.png"),
            )
        )
        tracker.track_node_change(
            ImageNode(
                id="n2",
                name="node2",
                style=Style(bbox=BBox(left=10, top=10, right=20, bottom=20)),
                asset=AssetRef(kind="image", src="n2.png"),
            )
        )

        delta = tracker.finalize()
        assert len(delta.added_nodes) == 2
        assert len(delta.removed_nodes) == 0
        assert len(delta.modified_nodes) == 0

    def test_tracker_detects_additions(
        self, prev_document: Document, curr_document_with_changes: Document
    ) -> None:
        """检测新增节点。"""
        tracker = DeltaIRTracker(prev_doc=prev_document)

        # 模拟处理当前版本的节点
        for node in curr_document_with_changes.iter_nodes():
            tracker.track_node_change(node)

        delta = tracker.finalize()
        # 新增：img-3, img-4（以及 root 作为组节点）
        assert "img-3" in delta.added_nodes
        assert "img-4" in delta.added_nodes

    def test_tracker_detects_removals(
        self, prev_document: Document, curr_document_with_changes: Document
    ) -> None:
        """检测删除的节点。"""
        tracker = DeltaIRTracker(prev_doc=prev_document)

        # 模拟处理当前版本的节点
        for node in curr_document_with_changes.iter_nodes():
            tracker.track_node_change(node)

        delta = tracker.finalize()
        # 删除：img-2
        assert "img-2" in delta.removed_nodes

    def test_tracker_detects_modifications(
        self, prev_document: Document, curr_document_with_modified: Document
    ) -> None:
        """检测修改的节点。"""
        tracker = DeltaIRTracker(prev_doc=prev_document)

        # 模拟处理当前版本的节点
        for node in curr_document_with_modified.iter_nodes():
            tracker.track_node_change(node)

        delta = tracker.finalize()
        # 修改：img-1（opacity 从 1.0 变为 0.5）
        assert "img-1" in delta.modified_nodes
        # 未修改：img-2
        assert "img-2" not in delta.modified_nodes

    def test_tracker_stats(self, prev_document: Document, curr_document_with_changes: Document) -> None:
        """检查追踪统计。"""
        tracker = DeltaIRTracker(prev_doc=prev_document)

        # 模拟处理当前版本的节点
        for node in curr_document_with_changes.iter_nodes():
            tracker.track_node_change(node)

        delta = tracker.finalize()
        stats = tracker.get_stats()

        assert "prev_node_count" in stats
        assert "curr_node_count" in stats
        assert "added" in stats
        assert "removed" in stats
        assert "modified" in stats
        assert "unchanged" in stats

        # 检查数据合理性
        assert stats["added"] > 0  # 有新增
        assert stats["removed"] > 0  # 有删除


class TestDeltaIRComparison:
    """测试节点比较逻辑。"""

    def test_identical_nodes_are_equal(self) -> None:
        """相同的节点被认为相等。"""
        bbox = BBox(left=10, top=10, right=30, bottom=30)
        node1 = ImageNode(
            id="n1",
            name="image",
            style=Style(bbox=bbox, opacity=0.8, z_index=1),
            asset=AssetRef(kind="image", src="img.png"),
        )
        node2 = ImageNode(
            id="n1",
            name="image",
            style=Style(bbox=bbox, opacity=0.8, z_index=1),
            asset=AssetRef(kind="image", src="img.png"),
        )

        assert DeltaIRTracker._nodes_equal(node1, node2)

    def test_different_opacity_not_equal(self) -> None:
        """不同的 opacity 导致不相等。"""
        bbox = BBox(left=10, top=10, right=30, bottom=30)
        node1 = ImageNode(
            id="n1",
            name="image",
            style=Style(bbox=bbox, opacity=1.0, z_index=1),
            asset=AssetRef(kind="image", src="img.png"),
        )
        node2 = ImageNode(
            id="n1",
            name="image",
            style=Style(bbox=bbox, opacity=0.5, z_index=1),
            asset=AssetRef(kind="image", src="img.png"),
        )

        assert not DeltaIRTracker._nodes_equal(node1, node2)

    def test_different_bbox_not_equal(self) -> None:
        """不同的 bbox 导致不相等。"""
        bbox1 = BBox(left=10, top=10, right=30, bottom=30)
        bbox2 = BBox(left=10, top=10, right=40, bottom=40)

        node1 = ImageNode(
            id="n1",
            name="image",
            style=Style(bbox=bbox1, opacity=1.0, z_index=1),
            asset=AssetRef(kind="image", src="img.png"),
        )
        node2 = ImageNode(
            id="n1",
            name="image",
            style=Style(bbox=bbox2, opacity=1.0, z_index=1),
            asset=AssetRef(kind="image", src="img.png"),
        )

        assert not DeltaIRTracker._nodes_equal(node1, node2)
