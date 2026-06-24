"""主优化器协调类"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
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
from .transformers.css_post_clean import CssPostClean, CssPostCleanConfig


class LayoutOptimizer:
    """
    布局优化器 - 协调各个子模块完成 PSD → HTML 的布局优化

    优化流程：
        1. DOM 重构：根据空间包含关系调整父子结构
        1.2 图层扁平化：把容器自身 bg + 全部 image 子合成为容器自己的单背景
        1.5 同质兄弟分组：识别"平铺的同质卡片"包成 v-list（flex-wrap）
        2. Flex 布局：识别横向/垂直排列模式并应用 flex
        2.5 单子 wrapper 折叠
        3. CSS 去冗余：精简 z-index + 合并属性等价的选择器

        注：被完全遮挡图层剔除（OccludedLayerPruner）已迁移为独立 Stage
        ``PrunePreOptimizeStage``，跑在 LayoutOptimizer **之前**：基于
        index.html 静态产物做"基于像素 + 几何遮挡"的剔除，传入本优化器的
        是"已剔除后的可见图层 DOM"。这样 DOMRestructure / FlexApplier 看
        到的子节点集合从一开始就是最终视觉子节点集合，envelope/对齐推断
        从源头一致，不需要 flex 子保护、阈值也无需极保守。
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
        strict: bool = False,
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
            strict: 严格模式。为 True 时，任何 transformer 步骤失败都会
                直接抛出异常（适用于 CI / debug）。为 False（默认）时保持
                "尽力而为"容错——失败步骤记录到 ``stats['_failures']`` 并跳过。
        """
        self.soup = BeautifulSoup(html_content, 'html.parser')
        self.css_rules = css_rules
        self.global_header = global_header
        self.strict = strict
        self.pretty_config = pretty_config or CssPrettyConfig()
        self.repeat_unify_config = repeat_unify_config or RepeatUnifyConfig()
        self.semantic_rename_config = semantic_rename_config or SemanticRenameConfig()
        self.virtual_wrapper_rename_config = (
            virtual_wrapper_rename_config or VirtualWrapperRenameConfig()
        )
        self.position_relaxer_config = position_relaxer_config or PositionRelaxerConfig()
        self.images_dir = images_dir
        self.flatten_config = flatten_config or FlattenConfig()
        self.stats: Dict[str, Any] = {
            'backgrounds_merged': 0,
            'classes_merged': 0,
            'flex_applied': 0,
            'positions_removed': 0,
            'dom_restructured': 0,
            'sibling_lists_created': 0,
            'sibling_items_wrapped': 0,
            'z_index_pruned': 0,
            'z_index_filled': 0,
            'css_rules_merged': 0,
            'classes_unified': 0,
            'elements_unified': 0,
            'repeat_groups_unified': 0,
            'semantic_class_renamed': 0,
            '_failures': [],
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
        self.css_post_clean = CssPostClean(
            self.soup, self.css_rules, self.stats
        )

    # ------------------------------------------------------------------
    # Internal: run a single transformer step with strict / tolerant mode
    # ------------------------------------------------------------------

    def _run_step(self, step_name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """执行单个 transformer 步骤。

        - ``strict=True``（CI / debug）：异常直接向上传播。
        - ``strict=False``（默认）：捕获异常，记录到
          ``stats['_failures']``，打印警告后继续。
        """
        try:
            fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            if self.strict:
                raise
            self.stats['_failures'].append({
                'step': step_name,
                'error': str(e),
            })
            print(f"⚠️  {step_name}失败: {e}")
            import traceback
            traceback.print_exc()

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
        self._run_step("DOM 重构", self.dom_restructure.restructure_dom)

        # 步骤 1.2：图层扁平化（统一通道，2026-04-30 重构）
        # 把"容器自身 background-image（如有）+ 全部直接 image 子的
        # background-image"合成为容器自己的单一背景，删除子 div + CSS。
        # 必须在 DOM 重构之后（容器结构已稳定），在 sibling_group_detector
        # 之前（避免它给装饰组算同质列表）。
        self._run_step("图层扁平化", self.image_layer_flatten.run)

        # 步骤 1.5：同质兄弟分组（识别平铺的同质卡片，包成 v-list）
        # 必须在 DOM 重构之后运行（拿到稳定的 DOM 父子关系），
        # 在 flex_applier 之前运行（让生成的 v-list 不被再次分析）。
        self._run_step("同质兄弟分组", self.sibling_group_detector.run)

        # 步骤 1.6（已迁移）：被完全遮挡图层剔除现在是独立 Stage
        # （PrunePreOptimizeStage）跑在本优化器**之前**，传入的 html_content
        # 已是"剔除后的可见图层 DOM"。

        # 步骤 2：应用 Flex 布局
        self._run_step("Flex 布局应用", self.flex_applier.apply_flex_layouts)

        # 步骤 2.5：单子 wrapper 折叠（P3 - 2026-04-30）
        # 必须在 flex_applier 之后（让 grid_row 类 wrapper 已稳定），
        # 在 css_dedup 之前（让被折叠 wrapper 的 CSS 规则不进入合并组）。
        self._run_step("单子 wrapper 折叠", self.wrapper_collapse.run)

        # 步骤 2.7（已迁移）：被完全遮挡图层剔除已迁移到 LayoutOptimizer
        # 之前的独立 Stage（PrunePreOptimizeStage）。

        # 步骤 3：CSS 去冗余（z-index 精简 + 等价规则合并）
        # 必须在所有 DOM/CSS 调整之后运行，保证看到的是最终态。
        self._run_step("CSS 去冗余", self.css_dedup.run)

        # 步骤 3.3：位置噪声宽容合并（同 base + 非位置签名相同 → 归一到代表样式）
        # 必须在 CssDedup 之后（看到 z-index 已删的最终态），在 RepeatClassUnifier
        # 之前（让本 transformer 写入的合并组被 RepeatClassUnifier 消费）。
        # ⚠️ 这是链路里**唯一**会引入亚像素视觉差异的步骤（margin 偏差归一），
        # 用 N→1 样式复用换设计稿生产噪声容忍。
        self._run_step("位置噪声宽容合并", self.position_relaxer.run)

        # 步骤 3.5：重复元素抽取（≥3 个等价 hash 类 → 单一语义类，HTML 复用）
        # 必须在 CssDedup 之后（消费 _css_merge_groups），CssPretty 之前
        # （让 CssPretty 不再为已合并的组渲染合并块）。
        self._run_step("重复元素抽取", self.repeat_unifier.run)

        # 步骤 3.7：语义类去后缀（``.nickname__37`` → ``.nickname``；
        # 同名冲突用 ``-2 / -3 / ...`` 区分）。
        # 必须在 repeat_unifier 之后（让 RepeatClassUnifier 产出的裸 ``.<base>``
        # 占位，本 transformer 在剩余 __N 类上继续分配 -2/-3），
        # 在 CssPretty 之前（让最终输出文件直接用新名；也能同步更新 merge_groups
        # 里残留的 __N 选择器）。
        # 旁路产出 ``stats['_class_alias_map']``，供 LayoutOptimizeStage 写出
        # ``class_alias_map.json``（旧 __N 类名 → 新精简类名）。
        self._run_step("语义类去后缀", self.semantic_renamer.run)

        # 步骤 3.8：虚拟 wrapper 命名语义化（``.v-stack-7`` → ``.<prefix>-stack``）。
        # 必须在 semantic_renamer 之后（后者已经把 ``.<base>__N`` 改成干净的
        # ``.<base>``，便于当作语义前缀），在 CssPretty 之前（让输出直接用新名）。
        # 同样旁路更新 ``stats['_class_alias_map']``。
        self._run_step("虚拟 wrapper 命名", self.virtual_wrapper_renamer.run)

        # 步骤 3.9：结构感知 CSS 后处理清理（CssPostClean）
        # 必须在所有改名/合并完成后（3.7/3.8 之后）、CssPretty 之前运行。
        # 需要读 DOM 判断父子关系，清理 flex 子项上的无效三件套：
        #   - position:relative（无偏移 + 无绝对定位子元素）
        #   - z-index（PSD 全局导出序号，flex 布局下无叠序意义）
        #   - flex-shrink:0（已有固定 width，不会被压缩）
        # 以及清理 v-stack/v-row 等 wrapper 上的 z-index:0 噪声。
        self._run_step("CSS 后处理清理", self.css_post_clean.run)

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
            except Exception as e:  # noqa: BLE001
                if self.strict:
                    raise
                self.stats['_failures'].append({
                    'step': 'CSS 美化',
                    'error': str(e),
                })
                print(f"⚠️  CSS 美化失败（降级到 dict_to_css）: {e}")
                import traceback
                traceback.print_exc()
                pretty_css = ""
        self.stats['_pretty_css'] = pretty_css

        html_output = str(self.soup)

        # ------------------------------------------------------------------
        # 统计摘要
        # ------------------------------------------------------------------
        print(f"\n✅ 优化完成！")
        print(f"   - DOM 重构: {self.stats['dom_restructured']} 个")
        print(f"   - v-list 创建: {self.stats['sibling_lists_created']} 个 "
              f"(包裹 {self.stats['sibling_items_wrapped']} 个节点)")
        print(f"   - 应用 flex: {self.stats['flex_applied']} 个")
        if self.stats.get('wrappers_collapsed'):
            print(f"   - 单子 wrapper 折叠: {self.stats['wrappers_collapsed']} 个")
        print(f"   - z-index 精简: {self.stats['z_index_pruned']} 处")
        if self.stats.get('z_index_filled'):
            print(f"   - z-index 兜底补全 (混合状态): {self.stats['z_index_filled']} 处")
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
        post_triple = self.stats.get('post_clean_flex_triple_removed', 0)
        post_zero_z = self.stats.get('post_clean_zero_z_removed', 0)
        if post_triple or post_zero_z:
            print(
                f"   - CSS 后处理清理: flex三件套 {post_triple} 属性, "
                f"z-index:0 {post_zero_z} 条"
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

        # 失败汇总：让用户/CI 一眼看到哪些步骤被跳过
        failures = self.stats['_failures']
        if failures:
            names = ", ".join(f['step'] for f in failures)
            print(f"\n⚠️  {len(failures)} 个 transformer 失败: {names}")

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
    strict: bool = False,
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
        strict: 严格模式（CI / debug）。为 True 时 transformer 异常直接抛出。

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
        strict=strict,
    )
    return optimizer.optimize()
