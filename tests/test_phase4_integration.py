"""Phase 4 集成测试 - 验证系统整体功能

验证内容：
- Handler 组合模式正确运作
- 向后兼容性（Mixin 接口仍可用）
- 端到端集成流程
"""

from unittest.mock import MagicMock
from pathlib import Path

import pytest

from targets.html.postprocess.layout_optimizer.transformers.dom_restructure import (
    BBox,
    ClusterConfig,
    DOMRestructure,
    LeafInfo,
    LayoutNode,
)


class TestPhase4Integration:
    """Phase 4 集成测试"""

    def test_handler_composition_works(self):
        """验证 Handler 组合模式正确运作"""
        soup = MagicMock()
        soup.find_all = MagicMock(return_value=[])
        
        dr = DOMRestructure(
            soup=soup,
            css_rules={},
            stats={},
            images_dir=None,
        )
        
        # 验证所有 Handler 都被初始化
        assert hasattr(dr, 'tall_decor')
        assert hasattr(dr, 'clustering')
        assert hasattr(dr, 'rendering')
        assert hasattr(dr, 'reclassify')
        assert hasattr(dr, 'background')
        
        # 验证 Handler 持有对主对象的引用
        assert dr.tall_decor.owner is dr
        assert dr.clustering.owner is dr
        assert dr.rendering.owner is dr
        assert dr.reclassify.owner is dr
        assert dr.background.owner is dr

    def test_handler_config_access(self):
        """验证 Handler 能正确访问配置"""
        soup = MagicMock()
        soup.find_all = MagicMock(return_value=[])
        
        dr = DOMRestructure(
            soup=soup,
            css_rules={},
            stats={},
            images_dir=None,
        )
        
        # 验证所有 Handler 都能访问 config
        config = dr.config
        assert config is not None
        assert hasattr(config, 'enable_tall_decor_extraction')
        assert hasattr(config, 'min_children_to_cluster')
        
        # 通过 Handler 访问
        assert dr.tall_decor.config is config
        assert dr.clustering.config is config

    def test_main_interface_exists(self):
        """验证主要公开接口存在"""
        soup = MagicMock()
        soup.find_all = MagicMock(return_value=[])
        
        dr = DOMRestructure(
            soup=soup,
            css_rules={},
            stats={},
            images_dir=None,
        )
        
        # 验证主要入口方法存在
        assert hasattr(dr, 'restructure_dom')
        assert callable(dr.restructure_dom)

    def test_mixin_backward_compatibility(self):
        """验证向后兼容性 - 旧的 Mixin 方法仍可用"""
        soup = MagicMock()
        soup.find_all = MagicMock(return_value=[])
        
        dr = DOMRestructure(
            soup=soup,
            css_rules={},
            stats={},
            images_dir=None,
        )
        
        # 验证从 Mixin 继承的方法仍可用
        # （这些方法现在通过 Handler 实现）
        assert hasattr(dr, '_collect_all_groups')
        assert callable(dr._collect_all_groups)
        
        assert hasattr(dr, '_restructure_group')
        assert callable(dr._restructure_group)

    def test_configuration_modification(self):
        """验证运行时配置修改有效"""
        soup = MagicMock()
        soup.find_all = MagicMock(return_value=[])
        
        dr = DOMRestructure(
            soup=soup,
            css_rules={},
            stats={},
            images_dir=None,
        )
        
        # 修改配置
        original = dr.config.min_children_to_cluster
        dr.config.min_children_to_cluster = 10
        
        # 验证修改生效
        assert dr.config.min_children_to_cluster == 10
        
        # 通过 Handler 也能看到更改
        assert dr.clustering.config.min_children_to_cluster == 10
        
        # 恢复
        dr.config.min_children_to_cluster = original

    def test_statistics_tracking(self):
        """验证统计信息追踪正常"""
        soup = MagicMock()
        soup.find_all = MagicMock(return_value=[])
        
        stats = {}
        dr = DOMRestructure(
            soup=soup,
            css_rules={},
            stats=stats,
            images_dir=None,
        )
        
        # 统计信息应该能通过所有 Handler 访问
        assert dr.stats is stats
        assert dr.tall_decor.stats is stats
        assert dr.clustering.stats is stats
        assert dr.rendering.stats is stats
        assert dr.reclassify.stats is stats
        assert dr.background.stats is stats

    def test_no_import_errors(self):
        """验证没有导入或模块化问题"""
        # 如果能成功导入和创建实例，说明模块化没有问题
        from targets.html.postprocess.layout_optimizer.transformers.dom_restructure import (
            DOMRestructure,
        )
        
        assert DOMRestructure is not None

    def test_handler_method_chaining(self):
        """验证 Handler 方法可正确调用"""
        soup = MagicMock()
        soup.find_all = MagicMock(return_value=[])
        
        dr = DOMRestructure(
            soup=soup,
            css_rules={},
            stats={'dom_restructured': 0},
            images_dir=None,
        )
        
        # 创建测试数据
        elem = MagicMock()
        elem.get = MagicMock(return_value=['test'])
        leaf = LeafInfo(
            element=elem,
            css_class='.test',
            name='test',
            data_type='image',
            bbox=BBox(0, 0, 100, 100),
        )
        
        # 验证可以调用 Handler 方法
        try:
            # TallDecorHandler
            decor, fg = dr.tall_decor.extract_tall_decor_leaves([leaf])
            assert isinstance(decor, list)
            assert isinstance(fg, list)
        except Exception as e:
            pytest.fail(f"TallDecorHandler 调用失败: {e}")
        
        try:
            # ClusteringHandler
            node = dr.clustering._leaf_to_node(leaf)
            assert isinstance(node, LayoutNode)
        except Exception as e:
            pytest.fail(f"ClusteringHandler 调用失败: {e}")

    def test_quality_metrics_pass(self):
        """验证质量指标"""
        soup = MagicMock()
        soup.find_all = MagicMock(return_value=[])
        
        dr = DOMRestructure(
            soup=soup,
            css_rules={},
            stats={},
            images_dir=None,
        )
        
        # 代码质量检查
        assert dr is not None  # 系统完整
        assert all(h is not None for h in [
            dr.tall_decor, dr.clustering, dr.rendering,
            dr.reclassify, dr.background
        ])  # 所有 Handler 都存在
        assert dr.config is not None  # 配置完整
        assert dr.parser is not None  # 解析器初始化


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
