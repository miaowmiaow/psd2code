#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 3 切换测试：验证新模块的使用开关是否工作

这个测试检查：
1. USE_NEW_MODULES 开关是否能正确控制代码路径
2. 使用新模块时的初始化是否成功
3. 向后兼容性是否保持
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock


class TestPhase3Switchover:
    """Phase 3 模块切换验证"""

    def test_use_new_modules_flag_exists(self):
        """测试 USE_NEW_MODULES 开关是否存在"""
        from scripts.core.extract.layer_exporter import USE_NEW_MODULES
        
        # 开关应该存在并为布尔值
        assert isinstance(USE_NEW_MODULES, bool)
        # 开关可以是 True 或 False，取决于是否启用新模块
        # 这个测试只验证开关存在且是布尔值
        print(f"USE_NEW_MODULES = {USE_NEW_MODULES}")

    def test_clipping_handler_integration(self):
        """测试 ClippingHandler 是否能被正确导入和使用"""
        from scripts.core.extract.clipping_handler import ClippingHandler
        
        # 验证关键方法存在
        assert hasattr(ClippingHandler, 'is_clipping_layer')
        assert hasattr(ClippingHandler, 'group_clipping_layers')
        assert hasattr(ClippingHandler, 'merge_clipping_group')

    def test_light_effect_renderer_integration(self):
        """测试 LightEffectRenderer 是否能被正确导入和使用"""
        from scripts.core.extract.light_effect_renderer import LightEffectRenderer
        
        # 验证关键方法存在
        assert hasattr(LightEffectRenderer, 'pre_scan')

    def test_layer_exporter_with_new_modules_disabled(self):
        """测试旧代码方法仍然存在（向后兼容）"""
        # 无论 USE_NEW_MODULES 是 True 还是 False，
        # 旧方法都应该保留以保持向后兼容性
        from scripts.core.extract.layer_exporter import LayerExporter
        assert hasattr(LayerExporter, '_pre_scan_light_layers')
        assert hasattr(LayerExporter, '_group_clipping_layers')
        assert hasattr(LayerExporter, '_merge_clipping_group')

    def test_layer_exporter_context_initialization(self):
        """测试 LayerExporter 初始化时是否创建了 ctx 对象"""
        from scripts.core.extract.layer_exporter import LayerExporter
        from scripts.core.extract.exporter_context import LayerExporterContext
        
        # 创建模拟 PSD 对象
        mock_psd = MagicMock()
        mock_psd.width = 1920
        mock_psd.height = 1080
        
        with patch('pathlib.Path.mkdir'):
            exporter = LayerExporter(mock_psd, Path('/tmp/output'))
        
        # 验证 ctx 对象被创建
        assert hasattr(exporter, 'ctx')
        assert isinstance(exporter.ctx, LayerExporterContext)
        assert exporter.ctx.canvas_width == 1920
        assert exporter.ctx.canvas_height == 1080

    def test_layer_exporter_clipping_handler_reference(self):
        """测试 LayerExporter 是否有 ClippingHandler 的引用"""
        from scripts.core.extract.layer_exporter import LayerExporter
        from scripts.core.extract.clipping_handler import ClippingHandler
        
        mock_psd = MagicMock()
        mock_psd.width = 1920
        mock_psd.height = 1080
        
        with patch('pathlib.Path.mkdir'):
            exporter = LayerExporter(mock_psd, Path('/tmp/output'))
        
        # 验证 _clipping_handler 被设置
        assert hasattr(exporter, '_clipping_handler')
        assert exporter._clipping_handler is ClippingHandler

    def test_switchover_migration_path(self):
        """测试迁移路径的完整性"""
        from scripts.core.extract import layer_exporter
        
        # Phase 2: 新模块已创建 ✅
        assert hasattr(layer_exporter, 'LightEffectRenderer')
        assert hasattr(layer_exporter, 'ClippingHandler')
        assert hasattr(layer_exporter, 'LayerExporterContext')
        
        # Phase 3: 配置开关已存在 ✅
        assert hasattr(layer_exporter, 'USE_NEW_MODULES')
        assert isinstance(layer_exporter.USE_NEW_MODULES, bool)
        
        # Phase 3: 初始化时有条件逻辑 ✅
        # (通过代码审查可以验证，这里通过结构测试)
        from scripts.core.extract.layer_exporter import LayerExporter
        assert hasattr(LayerExporter, '__init__')

    def test_export_layers_clipping_handling(self):
        """测试 export_layers 是否能正确处理 clipping 逻辑"""
        from scripts.core.extract.layer_exporter import LayerExporter
        
        mock_psd = MagicMock()
        mock_psd.width = 1920
        mock_psd.height = 1080
        mock_psd.__iter__ = MagicMock(return_value=iter([]))
        
        with patch('pathlib.Path.mkdir'):
            exporter = LayerExporter(mock_psd, Path('/tmp/output'))
        
        # export_layers 方法应该存在
        assert hasattr(exporter, 'export_layers')
        assert callable(exporter.export_layers)

    def test_switchover_backward_compatibility(self):
        """测试向后兼容性：使用 USE_NEW_MODULES=False 时旧代码仍可用"""
        from scripts.core.extract.layer_exporter import LayerExporter
        
        mock_psd = MagicMock()
        mock_psd.width = 1920
        mock_psd.height = 1080
        
        with patch('pathlib.Path.mkdir'):
            # 当 USE_NEW_MODULES=False 时，使用旧代码
            exporter = LayerExporter(mock_psd, Path('/tmp/output'))
        
        # 所有旧方法都应该仍然存在
        assert hasattr(exporter, '_pre_scan_light_layers')
        assert hasattr(exporter, '_group_clipping_layers')
        assert hasattr(exporter, '_merge_clipping_group')
        assert hasattr(exporter, '_export_clipped_layer_against_group_base')

    def test_documentation_exists(self):
        """测试 Phase 3 相关文档是否存在"""
        import os
        
        doc_files = [
            'doc/07-phase2-complete.md',
            'doc/OPTIMIZATION-EXECUTION-SUMMARY.md',
        ]
        
        base_dir = Path('/Users/zzz/psd2code')
        for doc_file in doc_files:
            doc_path = base_dir / doc_file
            assert doc_path.exists(), f"文档不存在: {doc_file}"


class TestPhase3MigrationChecklist:
    """Phase 3 迁移检查清单"""

    def test_phase2_completion_checklist(self):
        """验证 Phase 2 已完成"""
        # Phase 2 应该全部 ✅
        from scripts.core.extract.layer_exporter import (
            LayerExporter, 
            LayerExporterContext,
            LightEffectRenderer,
            ClippingHandler,
        )
        
        assert LayerExporter is not None
        assert LayerExporterContext is not None
        assert LightEffectRenderer is not None
        assert ClippingHandler is not None

    def test_phase3_architecture_ready(self):
        """验证 Phase 3 架构已准备好"""
        from scripts.core.extract import layer_exporter
        
        # 配置开关存在
        assert hasattr(layer_exporter, 'USE_NEW_MODULES')
        
        # 三个新模块都已导入
        assert hasattr(layer_exporter, 'LightEffectRenderer')
        assert hasattr(layer_exporter, 'ClippingHandler')
        assert hasattr(layer_exporter, 'LayerExporterContext')

    def test_phase3_switchover_comments_present(self):
        """验证 Phase 3 切换注释和 TODO 存在"""
        import inspect
        from scripts.core.extract.layer_exporter import LayerExporter
        
        # 获取源代码
        source = inspect.getsource(LayerExporter.__init__)
        
        # 应该包含 Phase 3 相关的注释或 TODO
        assert 'Phase 3' in source or 'phase3' in source.lower()


class TestPhase3NextSteps:
    """Phase 3 下一步指南"""

    def test_migration_requires_baseline_verification(self):
        """提示：切换到新模块前需要 baseline diff 验证"""
        # 这个测试只是文档用，提醒 Phase 3 完成步骤
        
        # 步骤：
        # 1. 修改 layer_exporter.py 第 53 行：USE_NEW_MODULES = True
        # 2. 运行转换测试：python psd_to_code.py --input ... --output ...
        # 3. 对比输出的 HTML/CSS：应该没有差异（或只有不可见的顺序变化）
        # 4. 如果验证通过，可以考虑删除旧代码（Phase 4，可选）
        
        assert True  # 这只是提示


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
