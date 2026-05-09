"""主优化器协调类"""

from pathlib import Path
from typing import Dict, Optional, Tuple
from bs4 import BeautifulSoup

from .transformers.dom_restructure import DOMRestructure
from .transformers.flex_applier import FlexApplier
from .transformers.sibling_group_detector import SiblingGroupDetector
from .transformers.css_dedup import CssDedup
from .transformers.css_pretty import CssPretty, CssPrettyConfig
from .transformers.image_layer_flatten import (
    FlattenConfig,
    ImageLayerFlatten,
)
from .transformers.position_noise_relaxer import (
    PositionNoiseRelaxer,
    PositionRelaxerConfig,
)
from .transformers.repeat_class_unifier import RepeatClassUnifier, RepeatUnifyConfig
from .transformers.semantic_class_rename import (
    SemanticClassRename,
    SemanticRenameConfig,
)
from .transformers.virtual_wrapper_rename import (
    VirtualWrapperRename,
    VirtualWrapperRenameConfig,
)
from .transformers.wrapper_collapse import WrapperCollapse


class LayoutOptimizer:
    """
    布局优化器 - 协调各个子模块完成 PSD → HTML 的布局优化

    优化流程：
        1. DOM 重构：根据空间包含关系调整父子结构
        1.2 图层扁平化：把容器自身 bg + 全部 image 子合成为容器自己的单背景
        1.5 同质兄弟分组：识别"平铺的同质卡片"包成 v-list（flex-wrap）
        2. Flex 布局：识别横向/垂直排列模式并应用 flex
        3. CSS 去冗余：精简 z-index + 合并属性等价的选择器
        3.3 位置噪声宽容合并：同 base + 非位置签名相同 → 归一到代表样式
                            （把 ``nickname-2..10`` 这种"只 margin 抖动"的列表项
                            合并为单一类，**牺牲 ≤8px 位置精度换样式复用**）
        3.5 重复元素抽取：把 ≥3 个等价 hash 类合并为单一语义类（HTML 复用）
        3.7 语义类去后缀：``.nickname__37`` → ``.nickname``（同名冲突用 -2/-3）
        3.8 虚拟 wrapper 命名：``.v-stack-7`` → ``.<语义>-stack``
        4. CSS 美化：按 DOM 顺序排序 + 属性分段 + 合并组多行（CssPretty）
    """

    def __init__(
        self,
        html_content: str,
        css_rules: Dict[str, Dict[str, str]],
        global_header: str = "",
        pretty_config: Optional[CssPrettyConfig] = None,
        repeat_unify_config: Optional[RepeatUnifyConfig] = None,
        semantic_rename_config: Optional[SemanticRenameConfig] = None,
        virtual_wrapper_rename_config: Optional[VirtualWrapperRenameConfig] = None,
        position_relaxer_config: Optional[PositionRelaxerConfig] = None,
        images_dir: Optional[Path] = None,
        flatten_config: Optional[FlattenConfig] = None,
    ):
        """
        Args:
            html_content: 原始 HTML 内容
            css_rules: CSS 规则字典 {'.classname': {'property': 'value'}}
            global_header: extract_global_css_header 产出的全局头（* / body / @media / #canvas 等）
            pretty_config: CssPretty 配置；None 则用默认（全开）
            repeat_unify_config: RepeatClassUnifier 配置；None 则用默认
            semantic_rename_config: SemanticClassRename 配置；None 则用默认（全开）
            virtual_wrapper_rename_config: VirtualWrapperRename 配置；None 则用默认（全开）
            position_relaxer_config: PositionNoiseRelaxer 配置；None 则用默认（全开）。
                ⚠️ 这是 LayoutOptimizer 链路里**唯一**会引入视觉差异的步骤
                （亚像素级 margin 偏差），用样式复用换设计稿生产噪声的容忍。
                需 100% 像素一致时设 enabled=False。
            images_dir: 物理 ``images/`` 目录。透传给 DOMRestructure，让"多
                层背景吸收"在写出 CSS 之前直接合成为单张 PNG。None 则跳过
                合成，由下游 ``background_flatten`` 文本兜底处理。
                同时透传给 ImageLayerFlatten（步骤 1.2），让"图层扁平化"
                也能落盘合成图。
            flatten_config: ImageLayerFlatten 配置；None 则用默认（启用）
        """
        self.soup = BeautifulSoup(html_content, 'html.parser')
        self.css_rules = css_rules
        self.global_header = global_header
        self.pretty_config = pretty_config or CssPrettyConfig()
        self.repeat_unify_config = repeat_unify_config or RepeatUnifyConfig()
        self.semantic_rename_config = semantic_rename_config or SemanticRenameConfig()
        self.virtual_wrapper_rename_config = (
            virtual_wrapper_rename_config or VirtualWrapperRenameConfig()
        )
        self.position_relaxer_config = position_relaxer_config or PositionRelaxerConfig()
        self.images_dir = images_dir
        self.flatten_config = flatten_config or FlattenConfig()
        self.stats = {
            'backgrounds_merged': 0,
            'classes_merged': 0,
            'flex_applied': 0,
            'positions_removed': 0,
            'dom_restructured': 0,
            'sibling_lists_created': 0,
            'sibling_items_wrapped': 0,
            'z_index_pruned': 0,
            'css_rules_merged': 0,
            'classes_unified': 0,
            'elements_unified': 0,
            'repeat_groups_unified': 0,
            'semantic_class_renamed': 0,
        }

        # 初始化各个子模块
        self.dom_restructure = DOMRestructure(
            self.soup, self.css_rules, self.stats, images_dir=self.images_dir,
        )
        self.image_layer_flatten = ImageLayerFlatten(
            self.soup, self.css_rules, self.stats,
            images_dir=self.images_dir,
            config=self.flatten_config,
        )
        self.sibling_group_detector = SiblingGroupDetector(
            self.soup, self.css_rules, self.stats
        )
        self.flex_applier = FlexApplier(self.soup, self.css_rules, self.stats)
        self.wrapper_collapse = WrapperCollapse(self.soup, self.css_rules, self.stats)
        self.css_dedup = CssDedup(self.soup, self.css_rules, self.stats)
        self.position_relaxer = PositionNoiseRelaxer(
            self.soup, self.css_rules, self.stats, self.position_relaxer_config
        )
        self.repeat_unifier = RepeatClassUnifier(
            self.soup, self.css_rules, self.stats, self.repeat_unify_config
        )
        self.semantic_renamer = SemanticClassRename(
            self.soup, self.css_rules, self.stats, self.semantic_rename_config
        )
        self.virtual_wrapper_renamer = VirtualWrapperRename(
            self.soup, self.css_rules, self.stats, self.virtual_wrapper_rename_config
        )

    def optimize(self) -> Tuple[str, Dict[str, Dict[str, str]], Dict]:
        """
        执行所有优化步骤

        Returns:
            (优化后 HTML, 优化后 CSS dict, 统计信息)

        说明：
            - 美化后的 CSS 字符串放在 ``stats['_pretty_css']``，调用方应优先
              用它写盘；为空（CssPretty 未开启或失败）则降级到 dict_to_css。
        """
        print("\n🎨 开始布局优化...")

        # 步骤 1：DOM 重构（根据包含关系调整父子结构）
        try:
            self.dom_restructure.restructure_dom()
        except Exception as e:
            print(f"⚠️  DOM 重构失败: {e}")
            import traceback
            traceback.print_exc()

        # 步骤 1.2：图层扁平化（统一通道，2026-04-30 重构）
        # 把"容器自身 background-image（如有）+ 全部直接 image 子的
        # background-image"合成为容器自己的单一背景，删除子 div + CSS。
        # 必须在 DOM 重构之后（容器结构已稳定），在 sibling_group_detector
        # 之前（避免它给装饰组算同质列表）。
        try:
            self.image_layer_flatten.run()
        except Exception as e:
            print(f"⚠️  图层扁平化失败: {e}")
            import traceback
            traceback.print_exc()

        # 步骤 1.5：同质兄弟分组（识别平铺的同质卡片，包成 v-list）
        # 必须在 DOM 重构之后运行（拿到稳定的 DOM 父子关系），
        # 在 flex_applier 之前运行（让生成的 v-list 不被再次分析）。
        try:
            self.sibling_group_detector.run()
        except Exception as e:
            print(f"⚠️  同质兄弟分组失败: {e}")
            import traceback
            traceback.print_exc()

        # 步骤 2：应用 Flex 布局
        try:
            self.flex_applier.apply_flex_layouts()
        except Exception as e:
            print(f"⚠️  Flex 布局应用失败: {e}")

        # 步骤 2.5：单子 wrapper 折叠（P3 - 2026-04-30）
        # 必须在 flex_applier 之后（让 grid_row 类 wrapper 已稳定），
        # 在 css_dedup 之前（让被折叠 wrapper 的 CSS 规则不进入合并组）。
        try:
            self.wrapper_collapse.run()
        except Exception as e:
            print(f"⚠️  单子 wrapper 折叠失败: {e}")
            import traceback
            traceback.print_exc()

        # 步骤 3：CSS 去冗余（z-index 精简 + 等价规则合并）
        # 必须在所有 DOM/CSS 调整之后运行，保证看到的是最终态。
        try:
            self.css_dedup.run()
        except Exception as e:
            print(f"⚠️  CSS 去冗余失败: {e}")
            import traceback
            traceback.print_exc()

        # 步骤 3.3：位置噪声宽容合并（同 base + 非位置签名相同 → 归一到代表样式）
        # 必须在 CssDedup 之后（看到 z-index 已删的最终态），在 RepeatClassUnifier
        # 之前（让本 transformer 写入的合并组被 RepeatClassUnifier 消费）。
        # ⚠️ 这是链路里**唯一**会引入亚像素视觉差异的步骤（margin 偏差归一），
        # 用 N→1 样式复用换设计稿生产噪声容忍。
        try:
            self.position_relaxer.run()
        except Exception as e:
            print(f"⚠️  位置噪声宽容合并失败: {e}")
            import traceback
            traceback.print_exc()

        # 步骤 3.5：重复元素抽取（≥3 个等价 hash 类 → 单一语义类，HTML 复用）
        # 必须在 CssDedup 之后（消费 _css_merge_groups），CssPretty 之前
        # （让 CssPretty 不再为已合并的组渲染合并块）。
        try:
            self.repeat_unifier.run()
        except Exception as e:
            print(f"⚠️  重复元素抽取失败: {e}")
            import traceback
            traceback.print_exc()

        # 步骤 3.7：语义类去后缀（``.nickname__37`` → ``.nickname``；
        # 同名冲突用 ``-2 / -3 / ...`` 区分）。
        # 必须在 repeat_unifier 之后（让 RepeatClassUnifier 产出的裸 ``.<base>``
        # 占位，本 transformer 在剩余 __N 类上继续分配 -2/-3），
        # 在 CssPretty 之前（让最终输出文件直接用新名；也能同步更新 merge_groups
        # 里残留的 __N 选择器）。
        # 旁路产出 ``stats['_class_alias_map']``，供 LayoutOptimizeStage 写出
        # ``class_alias_map.json``（旧 __N 类名 → 新精简类名）。
        try:
            self.semantic_renamer.run()
        except Exception as e:
            print(f"⚠️  语义类去后缀失败: {e}")
            import traceback
            traceback.print_exc()

        # 步骤 3.8：虚拟 wrapper 命名语义化（``.v-stack-7`` → ``.<prefix>-stack``）。
        # 必须在 semantic_renamer 之后（后者已经把 ``.<base>__N`` 改成干净的
        # ``.<base>``，便于当作语义前缀），在 CssPretty 之前（让输出直接用新名）。
        # 同样旁路更新 ``stats['_class_alias_map']``。
        try:
            self.virtual_wrapper_renamer.run()
        except Exception as e:
            print(f"⚠️  虚拟 wrapper 命名失败: {e}")
            import traceback
            traceback.print_exc()

        # 步骤 4：CSS 美化（按 DOM 顺序排序 + 属性分段 + 合并组多行）
        # 失败时不阻断，调用方自动降级到 dict_to_css。
        # 注：流水线产生的"工艺标记属性"（data-virtual / data-bg-absorbed /
        # data-i18n-key）以及图层元数据（data-name / data-type / id="layer-N|group-N"）
        # 由调用方在 LayoutOptimizer 之后统一通过 strip_dev_metadata 清理，
        # 这里不做处理（CssPretty 只读 class 与 DOM 顺序，不关心 data-* 属性）。
        pretty_css = ""
        if self.pretty_config.enabled:
            try:
                pretty = CssPretty(
                    soup=self.soup,
                    css_rules=self.css_rules,
                    merge_groups=self.stats.get('_css_merge_groups') or [],
                    global_header=self.global_header,
                    config=self.pretty_config,
                )
                pretty_css = pretty.render()
            except Exception as e:
                print(f"⚠️  CSS 美化失败（降级到 dict_to_css）: {e}")
                import traceback
                traceback.print_exc()
                pretty_css = ""
        self.stats['_pretty_css'] = pretty_css

        html_output = str(self.soup)

        print(f"\n✅ 优化完成！")
        print(f"   - DOM 重构: {self.stats['dom_restructured']} 个")
        print(f"   - v-list 创建: {self.stats['sibling_lists_created']} 个 "
              f"(包裹 {self.stats['sibling_items_wrapped']} 个节点)")
        print(f"   - 应用 flex: {self.stats['flex_applied']} 个")
        if self.stats.get('wrappers_collapsed'):
            print(f"   - 单子 wrapper 折叠: {self.stats['wrappers_collapsed']} 个")
        print(f"   - z-index 精简: {self.stats['z_index_pruned']} 处")
        print(f"   - CSS 等价规则合并: 节省 {self.stats['css_rules_merged']} 条")
        if self.stats.get('position_relaxed_groups'):
            print(
                f"   - 位置噪声归一: {self.stats['position_relaxed_groups']} 组 "
                f"(覆盖 {self.stats['position_relaxed_classes']} 个类)"
            )
        if self.stats.get('repeat_groups_unified'):
            print(
                f"   - 重复元素抽取: {self.stats['repeat_groups_unified']} 组 "
                f"→ 删除 {self.stats['classes_unified']} 个 hash 类、"
                f"复用到 {self.stats['elements_unified']} 个元素"
            )
        if self.stats.get('virtual_wrapper_renamed'):
            print(
                f"   - 虚拟 wrapper 命名: 重写 "
                f"{self.stats['virtual_wrapper_renamed']} 个类名"
            )
        if pretty_css:
            print(f"   - CSS 美化: 已生成（DOM 序 + 属性分段 + 合并组多行）")
        bg_inline = self.stats.get('bg_inline_flatten')
        if bg_inline and bg_inline.get('rules_flattened'):
            print(
                f"   - 背景内联合成: {bg_inline['rules_flattened']} 规则 "
                f"(折叠 {bg_inline['layers_collapsed']} 层, "
                f"节省 {bg_inline['bytes_saved'] / 1024:.1f} KB)"
            )
        if self.stats.get('image_layer_containers_flattened'):
            print(
                f"   - 图层扁平化: {self.stats['image_layer_containers_flattened']} 个容器 "
                f"(共合并 {self.stats['image_layer_layers_collapsed']} 层, "
                f"节省 {self.stats['image_layer_bytes_saved'] / 1024:.1f} KB)"
            )

        return html_output, self.css_rules, self.stats


def optimize_layout(
    html_content: str,
    css_rules: Dict[str, Dict[str, str]],
    global_header: str = "",
    pretty_config: Optional[CssPrettyConfig] = None,
    repeat_unify_config: Optional[RepeatUnifyConfig] = None,
    semantic_rename_config: Optional[SemanticRenameConfig] = None,
    virtual_wrapper_rename_config: Optional[VirtualWrapperRenameConfig] = None,
    position_relaxer_config: Optional[PositionRelaxerConfig] = None,
    images_dir: Optional[Path] = None,
    flatten_config: Optional[FlattenConfig] = None,
) -> Tuple[str, Dict[str, Dict[str, str]], Dict]:
    """
    布局优化入口函数

    Args:
        html_content: HTML 内容
        css_rules: CSS 规则字典
        global_header: extract_global_css_header 产出（含 * / body / @media / #canvas 等）
        pretty_config: CssPretty 配置；None 则用默认
        repeat_unify_config: RepeatClassUnifier 配置；None 则用默认（全开）
        semantic_rename_config: SemanticClassRename 配置；None 则用默认（全开）
        virtual_wrapper_rename_config: VirtualWrapperRename 配置；None 则用默认（全开）
        position_relaxer_config: PositionNoiseRelaxer 配置；None 则用默认（全开）
        images_dir: 物理 ``images/`` 目录，启用"多层背景吸收时直接合成单图"
            以及"图层扁平化"
        flatten_config: ImageLayerFlatten 配置；None 则用默认（启用）

    Returns:
        (优化后 HTML, 优化后 CSS dict, 统计信息)
    """
    optimizer = LayoutOptimizer(
        html_content,
        css_rules,
        global_header=global_header,
        pretty_config=pretty_config,
        repeat_unify_config=repeat_unify_config,
        semantic_rename_config=semantic_rename_config,
        virtual_wrapper_rename_config=virtual_wrapper_rename_config,
        position_relaxer_config=position_relaxer_config,
        images_dir=images_dir,
        flatten_config=flatten_config,
    )
    return optimizer.optimize()
