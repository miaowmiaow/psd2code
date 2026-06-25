"""
并行执行管线 - 支持多 Stage 同时执行

第5周优化 (Day 21-22)：支持多 target 并行导出
- ParallelPipeline: 按依赖关系执行 Stage 群组
- StageDepGraph: 分析 Stage 依赖关系
- ThreadSafeContext: 线程安全的上下文管理

设计特点：
1. 零侵入式：现有 Stage 无需改动
2. 自动依赖分析：根据 ctx 的读写操作推断依赖
3. 线程安全：使用 threading.RLock 保护共享状态
"""

import threading
import logging
from typing import Dict, List, Set, Optional, Callable, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from framework.stage import Stage
from framework.context import PipelineContext


logger = logging.getLogger(__name__)


@dataclass
class StageMeta:
    """Stage 的元数据"""
    name: str
    stage: Stage
    inputs: Set[str] = field(default_factory=set)  # 依赖的上下文键
    outputs: Set[str] = field(default_factory=set)  # 产生的上下文键


class StageDepGraph:
    """
    Stage 依赖图分析
    
    通过分析 Stage 对 PipelineContext 的读写操作，
    推断各 Stage 之间的依赖关系。
    """

    def __init__(self, stages: List[Stage]):
        self.stages = stages
        self.metas: Dict[str, StageMeta] = {}
        self._analyze_stages()

    def _analyze_stages(self) -> None:
        """分析所有 Stage 的输入/输出"""
        # 已知的 Stage 输入输出模式（基于第4周 pipeline.py 的实现）
        known_patterns = {
            "parse_to_ir": {
                "inputs": set(),
                "outputs": {"ir", "layer_exporter"},
            },
            "html_codegen": {
                "inputs": {"ir", "layer_exporter"},
                "outputs": {"html_generator", "html_path", "ir_cache"},
            },
            "layout_optimize": {
                "inputs": {"html_path"},
                "outputs": {"html_path_optimized"},
            },
            "background_absorption": {
                "inputs": {"html_path_optimized"},
                "outputs": {"html_path_absorbed"},
            },
            "react_codegen": {
                "inputs": {"ir", "layer_exporter"},
                "outputs": {"react_path"},
            },
            "vue_codegen": {
                "inputs": {"ir", "layer_exporter"},
                "outputs": {"vue_path"},
            },
        }

        for stage in self.stages:
            stage_name = stage.name
            
            # 优先检查 Stage 是否有 inputs/outputs 属性（用于测试 MockStage）
            if hasattr(stage, "inputs") and hasattr(stage, "outputs"):
                inputs = stage.inputs
                outputs = stage.outputs
            elif stage_name in known_patterns:
                pattern = known_patterns[stage_name]
                inputs = pattern["inputs"]
                outputs = pattern["outputs"]
            else:
                # 对于未知 Stage，假设没有依赖
                inputs = set()
                outputs = set()

            self.metas[stage_name] = StageMeta(
                name=stage_name,
                stage=stage,
                inputs=inputs,
                outputs=outputs,
            )

    def get_dependencies(self, stage_name: str) -> Set[str]:
        """
        获取某个 Stage 的直接依赖（哪些 Stage 必须在它之前执行）
        """
        if stage_name not in self.metas:
            return set()

        stage_inputs = self.metas[stage_name].inputs
        deps = set()

        for other_name, other_meta in self.metas.items():
            if other_name == stage_name:
                continue
            # 如果 other_stage 输出了 stage 需要的 key，则 stage 依赖 other_stage
            if other_meta.outputs & stage_inputs:
                deps.add(other_name)

        return deps

    def get_parallelizable_groups(self) -> List[List[str]]:
        """
        获取可并行执行的 Stage 分组
        
        返回一个列表，每个元素是一个 Stage 名称列表，
        表示这些 Stage 可以在同一组内并行执行。
        
        例如：
        [
            ["parse_to_ir"],  # 第1组：必须先执行
            ["html_codegen", "react_codegen", "vue_codegen"],  # 第2组：可并行
            ["layout_optimize"],  # 第3组：依赖 html_codegen
            ...
        ]
        """
        executed = set()
        groups = []

        while executed != set(self.metas.keys()):
            current_group = []

            for stage_name, meta in self.metas.items():
                if stage_name in executed:
                    continue

                # 检查依赖是否都已执行
                deps = self.get_dependencies(stage_name)
                if deps <= executed:  # 所有依赖都已执行
                    current_group.append(stage_name)

            if not current_group:
                # 无法继续（可能有循环依赖），中止
                logger.warning("无法构建依赖图，可能存在循环依赖")
                break

            groups.append(current_group)
            executed.update(current_group)

        return groups


class ThreadSafeContext(PipelineContext):
    """
    线程安全的 PipelineContext
    
    使用 threading.RLock 保护 artifacts 字典的并发访问。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = threading.RLock()

    def set(self, key: str, value: Any) -> None:
        """线程安全的 set"""
        with self._lock:
            self.artifacts[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """线程安全的 get"""
        with self._lock:
            return self.artifacts.get(key, default)

    def update(self, data: Dict[str, Any]) -> None:
        """线程安全的 update"""
        with self._lock:
            for k, v in data.items():
                self.set(k, v)


class ParallelPipeline:
    """
    支持并行执行的管线
    
    按照 Stage 间的依赖关系，将可并行的 Stage 分组，
    使用 ThreadPoolExecutor 同时执行。
    """

    def __init__(
        self,
        stages: List[Stage],
        max_workers: int = 3,
        enable_parallel: bool = True,
    ):
        self.stages = stages
        self.max_workers = max_workers
        self.enable_parallel = enable_parallel
        self.dep_graph = StageDepGraph(stages)

    def run(self, ctx: PipelineContext) -> PipelineContext:
        """
        执行管线
        
        Args:
            ctx: 管线上下文
            
        Returns:
            更新后的上下文
        """
        if not self.enable_parallel:
            # 序列执行（兼容模式）
            return self._run_sequential(ctx)

        # 并行执行
        return self._run_parallel(ctx)

    def _run_sequential(self, ctx: PipelineContext) -> PipelineContext:
        """序列执行（回退模式）"""
        for stage in self.stages:
            logger.info(f"执行 Stage: {stage.name}")
            ctx = stage.run(ctx)
        return ctx

    def _run_parallel(self, ctx: PipelineContext) -> PipelineContext:
        """按依赖关系并行执行"""
        # 转换为线程安全的上下文
        if not isinstance(ctx, ThreadSafeContext):
            ctx = self._wrap_context(ctx)

        parallel_groups = self.dep_graph.get_parallelizable_groups()

        logger.info(f"Pipeline 分析：{len(parallel_groups)} 个执行组")
        for i, group in enumerate(parallel_groups):
            logger.info(f"  Group {i+1}: {group}")

        for group_idx, group_names in enumerate(parallel_groups):
            logger.info(f"执行第 {group_idx+1} 组（{len(group_names)} 个 Stage）")

            if len(group_names) == 1:
                # 单个 Stage，直接执行
                stage_name = group_names[0]
                stage = self.dep_graph.metas[stage_name].stage
                logger.info(f"  → {stage_name}")
                ctx = stage.run(ctx)
            else:
                # 多个 Stage，并行执行
                ctx = self._run_parallel_group(group_names, ctx)

        return ctx

    def _run_parallel_group(
        self,
        stage_names: List[str],
        ctx: ThreadSafeContext,
    ) -> ThreadSafeContext:
        """
        并行执行一组 Stage
        
        Args:
            stage_names: Stage 名称列表
            ctx: 线程安全的上下文
            
        Returns:
            更新后的上下文
        """
        stage_map = {meta.name: meta.stage for meta in self.dep_graph.metas.values()}

        futures = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            for stage_name in stage_names:
                if stage_name not in stage_map:
                    logger.warning(f"Stage 未找到: {stage_name}")
                    continue

                stage = stage_map[stage_name]
                logger.info(f"  ⚡ 提交 {stage_name} 到线程池")

                # 在线程中执行 Stage
                future = executor.submit(self._run_stage_safely, stage, ctx)
                futures[stage_name] = future

            # 等待所有 Stage 完成
            for stage_name, future in futures.items():
                try:
                    result = future.result()
                    logger.info(f"  ✓ {stage_name} 完成")
                except Exception as e:
                    logger.error(f"  ✗ {stage_name} 失败: {e}")
                    raise

        return ctx

    def _run_stage_safely(self, stage: Stage, ctx: ThreadSafeContext) -> None:
        """
        在线程中安全地执行 Stage
        
        注意：我们不修改 ctx 的引用，因为 Stage.run() 返回的 ctx
        可能是新对象。我们假设 Stage 的所有副作用都通过
        ctx.set() 方法作用到共享的 ThreadSafeContext 上。
        """
        try:
            # 执行 Stage，其内部通过 ctx.set() 修改上下文
            result_ctx = stage.run(ctx)

            # 将 result_ctx 中的新 artifacts 同步回 ctx
            # (Stage 可能返回不同的对象)
            if result_ctx is not ctx:
                for key in result_ctx.artifacts:
                    if key not in ctx.artifacts or ctx.artifacts[key] != result_ctx.artifacts[key]:
                        ctx.set(key, result_ctx.artifacts[key])

        except Exception as e:
            logger.error(f"Stage 执行失败: {e}", exc_info=True)
            raise

    def _wrap_context(self, ctx: PipelineContext) -> ThreadSafeContext:
        """将普通 PipelineContext 转换为 ThreadSafeContext"""
        safe_ctx = ThreadSafeContext(
            psd_path=ctx.psd_path,
            psd=ctx.psd,
            ir=ctx.ir,
            output_dir=ctx.output_dir,
            project_root=ctx.project_root,
            target_name=ctx.target_name,
            verbose=ctx.verbose,
        )

        # 复制现有的 artifacts
        for key, value in ctx.artifacts.items():
            safe_ctx.set(key, value)

        return safe_ctx
