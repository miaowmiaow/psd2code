"""
StreamingIRIterator 的简化单元测试

第5周优化 (Day 23-24)：验证流式迭代的基本功能
"""

import pytest
from core.ir.streaming_iterator import IRBuffer, StreamingIRIterator


class TestIRBuffer:
    """测试 IRBuffer 缓冲区"""

    def test_add_and_get(self):
        """测试添加和获取"""
        buffer = IRBuffer(capacity=3)
        nodes = ["node0", "node1", "node2"]

        for node in nodes:
            buffer.add(node)

        buffer.reset()
        assert buffer.get() == "node0"
        assert buffer.get() == "node1"
        assert buffer.get() == "node2"
        assert buffer.get() is None

    def test_capacity_limit(self):
        """测试容量限制"""
        buffer = IRBuffer(capacity=2)

        buffer.add("n1")
        buffer.add("n2")
        buffer.add("n3")  # n1 应该被弹出

        buffer.reset()
        assert buffer.get() == "n2"
        assert buffer.get() == "n3"
        assert buffer.get() is None

    def test_peek(self):
        """测试预读"""
        buffer = IRBuffer(capacity=5)

        for i in range(3):
            buffer.add(f"node{i}")

        buffer.reset()
        peeked = buffer.peek(2)
        assert peeked == ["node0", "node1"]
        assert buffer.get() == "node0"  # 位置应该不变

    def test_remaining_count(self):
        """测试剩余计数"""
        buffer = IRBuffer(capacity=5)

        for i in range(3):
            buffer.add(f"node{i}")

        buffer.reset()
        assert buffer.remaining_count() == 3
        buffer.get()
        assert buffer.remaining_count() == 2

    def test_is_empty(self):
        """测试空检查"""
        buffer = IRBuffer(capacity=2)
        
        buffer.add("n1")
        buffer.reset()
        
        assert not buffer.is_empty()
        buffer.get()
        assert buffer.is_empty()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
