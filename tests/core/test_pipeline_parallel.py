"""
ParallelPipeline 的单元测试

第5周优化 (Day 21-22)：验证并行执行的正确性
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, MagicMock
from core.pipeline_parallel import StageDepGraph, ThreadSafeContext, ParallelPipeline
from framework.stage import Stage
from framework.context import PipelineContext


class MockStage(Stage):
    """用于测试的 Mock Stage"""

    def __init__(self, name: str, inputs: set, outputs: set, delay: float = 0.0):
        self.name = name
        self.inputs = inputs
        self.outputs = outputs
        self.delay = delay
        self.executed = False

    def run(self, ctx: PipelineContext) -> PipelineContext:
        """执行 Stage"""
        import time
        time.sleep(self.delay)

        # 检查输入
        for key in self.inputs:
            if key not in ctx.artifacts:
                raise ValueError(f"缺少输入: {key}")

        # 产生输出
        for key in self.outputs:
            ctx.artifacts[key] = f"{self.name}_{key}"

        self.executed = True
        return ctx


class TestStageDepGraph:
    """测试 StageDepGraph 依赖分析"""

    def test_simple_dependency(self):
        """测试简单的依赖关系"""
        stages = [
            MockStage("A", inputs=set(), outputs={"x"}),
            MockStage("B", inputs={"x"}, outputs={"y"}),
            MockStage("C", inputs={"y"}, outputs={"z"}),
        ]

        graph = StageDepGraph(stages)

        assert graph.get_dependencies("A") == set()
        assert graph.get_dependencies("B") == {"A"}
        assert graph.get_dependencies("C") == {"B"}

    def test_parallel_branches(self):
        """测试并行分支"""
        stages = [
            MockStage("A", inputs=set(), outputs={"x"}),
            MockStage("B", inputs={"x"}, outputs={"y1"}),
            MockStage("C", inputs={"x"}, outputs={"y2"}),
            MockStage("D", inputs={"y1", "y2"}, outputs={"z"}),
        ]

        graph = StageDepGraph(stages)

        # A → [B, C] → D
        assert graph.get_dependencies("A") == set()
        assert graph.get_dependencies("B") == {"A"}
        assert graph.get_dependencies("C") == {"A"}
        assert graph.get_dependencies("D") == {"B", "C"}

    def test_parallelizable_groups(self):
        """测试可并行的分组"""
        stages = [
            MockStage("A", inputs=set(), outputs={"x"}),
            MockStage("B", inputs={"x"}, outputs={"y"}),
            MockStage("C", inputs={"x"}, outputs={"z"}),
        ]

        graph = StageDepGraph(stages)
        groups = graph.get_parallelizable_groups()

        # 期望: [["A"], ["B", "C"]] 或 [["A"], ["C", "B"]]
        assert len(groups) == 2
        assert groups[0] == ["A"]
        assert set(groups[1]) == {"B", "C"}

    def test_complex_parallelizable_groups(self):
        """测试复杂的并行分组"""
        stages = [
            MockStage("parse", inputs=set(), outputs={"ir"}),
            MockStage("html", inputs={"ir"}, outputs={"html"}),
            MockStage("react", inputs={"ir"}, outputs={"react"}),
            MockStage("vue", inputs={"ir"}, outputs={"vue"}),
            MockStage("layout", inputs={"html"}, outputs={"html_opt"}),
        ]

        graph = StageDepGraph(stages)
        groups = graph.get_parallelizable_groups()

        # 期望:
        # Group 0: ["parse"]
        # Group 1: ["html", "react", "vue"]
        # Group 2: ["layout"]
        assert len(groups) == 3
        assert groups[0] == ["parse"]
        assert set(groups[1]) == {"html", "react", "vue"}
        assert groups[2] == ["layout"]


class TestThreadSafeContext:
    """测试 ThreadSafeContext 的线程安全性"""

    def test_basic_set_get(self):
        """测试基本的 set/get 操作"""
        ctx = ThreadSafeContext(ir=None, psd=None, psd_path=None, output_dir=None)
        ctx.set("key1", "value1")
        assert ctx.get("key1") == "value1"

    def test_concurrent_set(self):
        """测试并发 set 操作"""
        import threading

        ctx = ThreadSafeContext(ir=None, psd=None, psd_path=None, output_dir=None)

        def worker(thread_id):
            for i in range(100):
                ctx.set(f"key_{thread_id}_{i}", f"value_{i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 验证所有数据都被正确写入
        for i in range(5):
            for j in range(100):
                assert ctx.get(f"key_{i}_{j}") == f"value_{j}"

    def test_concurrent_get(self):
        """测试并发 get 操作"""
        import threading

        ctx = ThreadSafeContext(ir=None, psd=None, psd_path=None, output_dir=None)
        ctx.set("shared_key", "shared_value")

        results = []

        def reader():
            for _ in range(100):
                val = ctx.get("shared_key")
                results.append(val)

        threads = [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 验证所有读取都获得了正确的值
        assert all(v == "shared_value" for v in results)


class TestParallelPipeline:
    """测试 ParallelPipeline 的并行执行"""

    def test_sequential_fallback(self):
        """测试序列执行降级"""
        from pathlib import Path
        
        stages = [
            MockStage("A", inputs=set(), outputs={"x"}),
            MockStage("B", inputs={"x"}, outputs={"y"}),
            MockStage("C", inputs={"y"}, outputs={"z"}),
        ]

        pipeline = ParallelPipeline(stages, enable_parallel=False)
        ctx = PipelineContext(psd_path=Path("/tmp/test.psd"))

        result_ctx = pipeline.run(ctx)

        # 验证所有 Stage 都执行了
        assert all(stage.executed for stage in stages)

        # 验证输出
        assert result_ctx.artifacts.get("x") == "A_x"
        assert result_ctx.artifacts.get("y") == "B_y"
        assert result_ctx.artifacts.get("z") == "C_z"

    def test_parallel_execution(self):
        """测试并行执行"""
        import time
        from pathlib import Path

        stages = [
            MockStage("A", inputs=set(), outputs={"x"}, delay=0.1),
            MockStage("B", inputs={"x"}, outputs={"y1"}, delay=0.1),
            MockStage("C", inputs={"x"}, outputs={"y2"}, delay=0.1),
        ]

        pipeline = ParallelPipeline(stages, enable_parallel=True, max_workers=2)
        ctx = PipelineContext(psd_path=Path("/tmp/test.psd"))

        start_time = time.time()
        result_ctx = pipeline.run(ctx)
        elapsed = time.time() - start_time

        # 验证所有 Stage 都执行了
        assert all(stage.executed for stage in stages)

        # 验证输出
        assert result_ctx.artifacts.get("x") is not None
        assert result_ctx.artifacts.get("y1") is not None
        assert result_ctx.artifacts.get("y2") is not None

        # 验证加速：B 和 C 应该并行执行
        # 预期时间: A(0.1s) + max(B(0.1s), C(0.1s)) = 0.2s
        # 序列时间: A(0.1s) + B(0.1s) + C(0.1s) = 0.3s
        # 加上开销，允许 0.25s 以内
        assert elapsed < 0.25, f"并行执行太慢: {elapsed}s"

    def test_dependency_violation(self):
        """测试依赖冲突检测"""
        from pathlib import Path
        
        stages = [
            MockStage("A", inputs=set(), outputs={"x"}),
            MockStage("B", inputs={"y"}, outputs={"z"}),  # 缺少输入 y
        ]

        pipeline = ParallelPipeline(stages, enable_parallel=True)
        ctx = PipelineContext(psd_path=Path("/tmp/test.psd"))

        # 应该抛出异常（缺少输入）
        with pytest.raises(ValueError, match="缺少输入"):
            pipeline.run(ctx)

    def test_context_wrap(self):
        """测试 PipelineContext 的转换"""
        from pathlib import Path
        
        stages = []

        pipeline = ParallelPipeline(stages)
        ctx = PipelineContext(psd_path=Path("/tmp/test.psd"))
        ctx.artifacts["key1"] = "value1"

        safe_ctx = pipeline._wrap_context(ctx)

        assert isinstance(safe_ctx, ThreadSafeContext)
        assert safe_ctx.get("key1") == "value1"


class TestParallelPipelineIntegration:
    """集成测试：完整的并行 Pipeline 流程"""

    def test_three_target_parallel(self):
        """模拟 HTML + React + Vue 三个 target 并行导出"""
        from pathlib import Path
        
        stages = [
            MockStage("parse_to_ir", inputs=set(), outputs={"ir", "exporter"}),
            MockStage("html_codegen", inputs={"ir", "exporter"}, outputs={"html_path"}),
            MockStage("react_codegen", inputs={"ir", "exporter"}, outputs={"react_path"}),
            MockStage("vue_codegen", inputs={"ir", "exporter"}, outputs={"vue_path"}),
        ]

        pipeline = ParallelPipeline(stages, enable_parallel=True, max_workers=3)
        ctx = PipelineContext(psd_path=Path("/tmp/test.psd"))

        result_ctx = pipeline.run(ctx)

        # 验证所有输出都生成了
        assert result_ctx.artifacts.get("html_path") is not None
        assert result_ctx.artifacts.get("react_path") is not None
        assert result_ctx.artifacts.get("vue_path") is not None

        # 验证依赖顺序
        # parse_to_ir 必须在其他 Stage 之前
        # html_codegen / react_codegen / vue_codegen 可以并行

    def test_enable_disable_parallel(self):
        """测试启用/禁用并行模式"""
        import time
        from pathlib import Path

        stages = [
            MockStage("A", inputs=set(), outputs={"x"}, delay=0.05),
            MockStage("B", inputs={"x"}, outputs={"y1"}, delay=0.05),
            MockStage("C", inputs={"x"}, outputs={"y2"}, delay=0.05),
        ]

        # 序列执行
        pipeline_seq = ParallelPipeline(stages, enable_parallel=False)
        ctx1 = PipelineContext(psd_path=Path("/tmp/test.psd"))

        start = time.time()
        pipeline_seq.run(ctx1)
        time_seq = time.time() - start

        # 并行执行
        pipeline_par = ParallelPipeline(stages, enable_parallel=True, max_workers=2)
        ctx2 = PipelineContext(psd_path=Path("/tmp/test.psd"))

        start = time.time()
        pipeline_par.run(ctx2)
        time_par = time.time() - start

        # 并行应该比序列快（尽管在这个小测试中差异可能不明显）
        # 但至少不应该慢太多
        assert time_par <= time_seq * 1.2  # 允许 20% 的开销


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
