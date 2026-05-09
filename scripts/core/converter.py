#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PSD2HTML 转换协调器
协调 LayerExporter + HTMLGenerator + LayoutOptimizer 完成 PSD → HTML 转换
"""

import os
import shutil
import sys
from pathlib import Path
from typing import Any

from psd_tools import PSDImage  # type: ignore[import-untyped]

# 确保可以导入 scripts 下的模块
scripts_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

from common.css_utils import dict_to_css, parse_css_to_dict, extract_global_css_header  # noqa: E402
from common.utils import reset_image_counter, reset_filename_registry  # noqa: E402
from config import __version__  # noqa: E402
from targets.html.codegen.html_generator import HTMLGenerator  # noqa: E402
from targets.html.postprocess.layout_optimizer import optimize_layout  # noqa: E402
from core.extract.layer_exporter import LayerExporter  # noqa: E402


class PSDToHTMLConverter:
    """PSD → HTML 转换器"""

    def __init__(self, psd_path: str, smart_merge: bool = True):
        """
        Args:
            psd_path: PSD 文件路径
            smart_merge: 是否启用「智能合图」。False 时全链路 4 类合图都关闭：
                (1) LayerExporter._can_merge_group / _can_merge_group_non_text
                (2) LayerExporter._merge_background_layers（画布底部连续背景）
                (3) LayoutOptimizer 步骤 1.2 ImageLayerFlatten
                (4) LayoutOptimizer DOMRestructure 多 url 内联合成 +
                    background_flatten 文本兜底
                对应新入口 CLI 的 ``--no-smart-merge``。
        """
        self.psd_path: str = psd_path
        self.psd: PSDImage = PSDImage.open(psd_path)  # type: ignore[misc]
        self.output_dir: Path = Path('.')
        self.layers_tree: list[dict[str, Any]] = []
        self.layer_exporter: LayerExporter | None = None
        self.html_generator: HTMLGenerator | None = None
        self.smart_merge: bool = smart_merge

    # -------------------------------------------------------------------------
    # 主入口
    # -------------------------------------------------------------------------
    def export(self, output_base: str = 'output') -> str:
        """
        执行完整转换流程。

        Args:
            output_base: 输出根目录

        Returns:
            生成的 HTML 文件路径（优化版优先）
        """
        # 1. 准备输出目录
        self._prepare_output_dir(output_base)

        # 2. 重置全局状态
        reset_image_counter()
        reset_filename_registry()

        # 3. 初始化模块
        self._init_modules()

        # 4. 打印头部信息
        self._print_header()

        # 5. 导出图层
        print("正在导出图层...")
        assert self.layer_exporter is not None
        self.layers_tree = self.layer_exporter.export_layers(self.psd)
        self.layer_exporter.verify_export()

        # 6. 生成 HTML / metadata / README
        html_path = self._generate_outputs()

        # 7. 应用布局优化
        html_path = self._apply_layout_optimization(html_path) or html_path

        # 8. 打印统计
        self._print_footer()

        return html_path

    # -------------------------------------------------------------------------
    # 流程步骤
    # -------------------------------------------------------------------------
    def _prepare_output_dir(self, output_base: str) -> None:
        """清空并创建输出目录"""
        psd_name = Path(self.psd_path).stem
        self.output_dir = Path(output_base) / psd_name
        if self.output_dir.exists():
            shutil.rmtree(self.output_dir)
            print(f"🗑️  已清除旧输出目录: {self.output_dir}")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _init_modules(self) -> None:
        """初始化导出器和生成器"""
        psd_name = Path(self.psd_path).stem
        self.layer_exporter = LayerExporter(
            self.psd, self.output_dir, smart_merge=self.smart_merge
        )
        self.html_generator = HTMLGenerator(
            self.psd.width, self.psd.height, self.output_dir, psd_name
        )

    def _generate_outputs(self) -> str:
        """生成 HTML、metadata.json 和 README.md"""
        assert self.html_generator is not None
        assert self.layer_exporter is not None

        print("\n正在生成 HTML...")
        html_path = self.html_generator.generate_html(self.layers_tree)

        self.html_generator.generate_metadata(
            self.layers_tree,
            self.layer_exporter.exported_count,
            self.layer_exporter.skipped_count,
        )
        self.html_generator.generate_readme(
            self.layer_exporter.exported_count,
            self.layer_exporter.skipped_count,
        )
        return html_path

    def _apply_layout_optimization(self, html_path: str) -> str | None:
        """
        应用布局优化，生成 index_optimized.html / style_optimized.css。

        Returns:
            优化版 HTML 路径，若失败返回 None
        """
        css_path = str(self.output_dir / 'style.css')
        if not (Path(html_path).exists() and Path(css_path).exists()):
            return None

        try:
            print("\n🎨 应用智能布局优化...")

            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            with open(css_path, 'r', encoding='utf-8') as f:
                css_content = f.read()

            css_rules = parse_css_to_dict(css_content)
            css_header = extract_global_css_header(css_content)
            # smart_merge=False：关闭 LayoutOptimizer 链路两项合图
            #  - DOMRestructure 多 url 背景内联合成（由 images_dir=None 跳过）
            #  - ImageLayerFlatten（步骤 1.2；由 FlattenConfig(enabled=False) 跳过）
            from targets.html.postprocess.layout_optimizer.transformers.image_layer_flatten import (  # type: ignore
                FlattenConfig,
            )
            html_optimized, css_optimized, stats = optimize_layout(
                html_content, css_rules, global_header=css_header,
                images_dir=(Path(html_path).parent / 'images') if self.smart_merge else None,
                flatten_config=FlattenConfig(enabled=self.smart_merge),
            )

            # 写出优化版文件
            html_optimized_path = html_path.replace('.html', '_optimized.html')
            css_optimized_path = css_path.replace('.css', '_optimized.css')

            # 修正 HTML 中的 CSS 引用
            html_optimized = html_optimized.replace(
                'href="style.css"', 'href="style_optimized.css"'
            )

            # 剥离 dev metadata（data-name / data-type / id="layer-*"）→ layer_map.json
            from targets.html.postprocess.strip_dev_metadata import (  # type: ignore
                strip_and_collect,
                write_layer_map,
            )
            html_optimized, layer_map = strip_and_collect(html_optimized)
            map_path = Path(html_optimized_path).parent / 'layer_map.json'
            write_layer_map(layer_map, map_path)

            with open(html_optimized_path, 'w', encoding='utf-8') as f:
                f.write(html_optimized)

            # CssPretty 优先：开发者友好的排版（DOM 序 + 属性分段 + 合并组多行）。
            # 失败时降级到 dict_to_css（机械字典渲染）。
            pretty_css = stats.get('_pretty_css') or ''
            if pretty_css:
                css_text = pretty_css
            else:
                merge_groups = stats.get('_css_merge_groups') or None
                css_text = dict_to_css(
                    css_optimized, header=css_header, merge_groups=merge_groups
                )

            # 多层 url() 背景合成（CSS 文本最终态后处理）
            # smart_merge=False 时跳过，保持多 url 背景原样
            if self.smart_merge:
                try:
                    from targets.html.postprocess.background_flatten import (  # type: ignore
                        flatten_multi_url_backgrounds,
                    )
                    images_dir = Path(css_optimized_path).parent / 'images'
                    css_text, bg_stats = flatten_multi_url_backgrounds(
                        css_text, images_dir
                    )
                    stats['bg_flatten'] = bg_stats
                    if bg_stats.get('rules_flattened'):
                        print(
                            f"   - 背景合成: {bg_stats['rules_flattened']} 规则 "
                            f"(折叠 {bg_stats['layers_collapsed']} 层, "
                            f"节省 {bg_stats['bytes_saved'] / 1024:.1f} KB)"
                        )
                except Exception as e:
                    print(f"⚠️  背景合成失败（保留多层 CSS）: {e}")
                    import traceback
                    traceback.print_exc()

            with open(css_optimized_path, 'w', encoding='utf-8') as f:
                f.write(css_text)

            print(f"✅ 布局优化完成！")
            print(f"   原始版本: {html_path}")
            print(f"   优化版本: {html_optimized_path}")
            print(f"   元数据映射: {map_path}")
            print(
                f"   统计: DOM 重构 {stats['dom_restructured']} 个, "
                f"flex 应用 {stats['flex_applied']} 个"
            )

            # 三向映射 + 图片索引（class ↔ image ↔ PSD layer），失败不阻断流程
            try:
                from targets.html.postprocess.mapping_report import (  # type: ignore
                    write_mapping_reports,
                )
                mapping_path, image_index_path = write_mapping_reports(
                    Path(html_optimized_path).parent
                )
                if mapping_path:
                    print(f"   映射报告: {mapping_path}")
                if image_index_path:
                    print(f"   图片索引: {image_index_path}")
            except Exception as e:  # noqa: BLE001
                print(f"⚠️  mapping report 生成失败: {e}")

            return html_optimized_path

        except Exception as e:
            print(f"⚠️  布局优化失败（保留原始版本）: {e}")
            import traceback
            traceback.print_exc()
            return None

    # -------------------------------------------------------------------------
    # 打印工具
    # -------------------------------------------------------------------------
    def _print_header(self) -> None:
        print(f"\n{'=' * 60}")
        print(f"PSD2HTML 转换器 v{__version__}")
        print(f"{'=' * 60}")
        print(f"源文件: {self.psd_path}")
        print(f"画布尺寸: {self.psd.width} x {self.psd.height}")
        print(f"输出目录: {self.output_dir}")
        print(f"{'=' * 60}\n")

    def _print_footer(self) -> None:
        if not self.layer_exporter:
            return
        print(f"\n{'=' * 60}")
        print(f"✅ 导出完成！")
        print(f"{'=' * 60}")
        print(f"✅ 成功: {self.layer_exporter.exported_count} 个图层")
        print(f"🚫 跳过: {self.layer_exporter.skipped_count} 个图层")
        print(f"📂 输出: {self.output_dir}")
        print(f"{'=' * 60}\n")
