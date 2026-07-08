"""DOM 重构转换器 - 主类

通过 Mixin 继承组合以下模块的功能：
- ``BackgroundMixin``: 背景剥离 / 吸收 / 合并
- ``TallDecorMixin``: 高瘦跨行装饰剥离
- ``ClusteringMixin``: 空间聚类（行列切分 + 叠图判定）
- ``RenderingMixin``: DOM 渲染（wrapper / flex margin / stack）
- ``ReclassifyMixin``: Stack → Col 反向升级 + 容器背景吸收 pass

核心思路：
    1. 对每个 group 的直接子元素做空间聚类（行/列切分）
    2. 识别叠图组（stack）保留 absolute
    3. row/col 容器产出 flex-ready 布局
    4. stack 容器内子元素保留 absolute

产出的 DOM 层次已经是"一维整齐"的 flex 结构，下游 SiblingGroupDetector
做平铺同质卡片识别，再由 FlexApplier 做剩余非 v-row/v-col 容器的 flex 推断。
"""

from pathlib import Path
from typing import Dict, List, Optional

from ...analyzers.layout_analyzer import LayoutAnalyzer
from ...utils.css_parser import CSSParser
from .data_types import BBox, LeafInfo, LayoutNode, ClusterConfig
from .handlers import (
    BackgroundHandler,
    TallDecorHandler,
    ClusteringHandler,
    RenderingHandler,
    ReclassifyHandler,
)
# 保留原 Mixin 导入以维持向后兼容
from .background import BackgroundMixin
from .tall_decor import TallDecorMixin
from .clustering import ClusteringMixin
from .rendering import RenderingMixin
from .reclassify import ReclassifyMixin


class DOMRestructure:
    """DOM 重构转换器

    入口：``restructure_dom()``

    对每个 ``layer-group`` 递归做空间聚类，并把结果写回 soup 和 css_rules。
    使用 Handler 组合模式替代 Mixin 继承。
    """

    def __init__(
        self,
        soup,
        css_rules: Dict[str, Dict[str, str]],
        stats: Dict,
        images_dir: Optional[Path] = None,
    ):
        """
        Args:
            images_dir: 物理 ``images/`` 目录。若提供，则在
                ``_merge_bg_candidates_into_container_css`` 决定写出多 url 背景
                时，会尝试把所有候选层离线合成为单张 PNG，CSS 仅写一层 url。
                None（或目录不存在）则跳过合成，按原"多 url"逻辑写出，由
                下游 ``background_flatten`` 兜底处理。
        """
        self.soup = soup
        self.css_rules = css_rules
        self.stats = stats
        self.images_dir = images_dir
        self.parser = CSSParser()
        self.config = ClusterConfig()
        self._virtual_seq = 0

        # 初始化 Handler 组合
        self.background = BackgroundHandler(self)
        self.tall_decor = TallDecorHandler(self)
        self.clustering = ClusteringHandler(self)
        self.rendering = RenderingHandler(self)
        self.reclassify = ReclassifyHandler(self)

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def restructure_dom(self):
        print("  📦 步骤0: DOM重构（空间聚类 + Flex-Ready 产出）...")

        all_groups = self._collect_all_groups()

        for group in all_groups:
            try:
                self._restructure_group(group)
            except Exception as exc:  # noqa: BLE001
                name = group.get('data-name', 'unknown')
                print(f"    ⚠️  {name} 处理失败: {exc}")
                import traceback
                traceback.print_exc()

        if self.config.enable_container_bg_absorb_pass:
            self.reclassify.absorb_container_backgrounds_pass()

    def _safe_extract(self, elem) -> bool:
        """安全删除元素：如果有子元素则保留，否则删除
        
        避免删除容器时连同其子元素一起删除的问题。
        
        返回值：True 表示真的删除了，False 表示因为有子元素而保留了
        """
        if not elem:
            return True
        
        # 找出所有直接子 div
        children = [c for c in elem.find_all(recursive=False) if c.name == 'div']
        
        if children:
            # 有子元素：保留容器，不删除
            # （这是有内容的容器，不应该被删除）
            return False
        
        # 无子元素：正常删除
        elem.extract()
        return True

    # ------------------------------------------------------------------
    # 收集所有 group
    # ------------------------------------------------------------------

    def _collect_all_groups(self) -> List:
        """按 DOM 文档顺序收集可参与聚类的容器。

        默认包含：
        - ``layer-group``

        额外包含：
        - ``data-type=image`` 且存在直接子 ``div`` 的 image 容器

        说明：仅放开"有直接子节点"的 image，避免把普通图片叶子层误当容器。
        """
        candidates = self.soup.find_all('div')
        result: List = []
        for elem in candidates:
            classes = elem.get('class', [])
            is_layer_group = 'layer-group' in classes

            is_image_container = (
                elem.get('data-type') == 'image'
                and any(
                    getattr(c, 'name', None) == 'div'
                    for c in elem.find_all(recursive=False)
                )
            )

            if is_layer_group or is_image_container:
                result.append(elem)

        return result

    # ------------------------------------------------------------------
    # 处理单个 group
    # ------------------------------------------------------------------

    def _can_flex_applier_handle(self, group) -> bool:
        """探测 group 是否能被 FlexApplier 识别为 vertical / horizontal flex。"""
        children = list(group.find_all(recursive=False))
        if len(children) < 3:
            return False
        try:
            analyzer = LayoutAnalyzer(self.css_rules)
            result = analyzer.analyze_children_layout(children)
        except Exception:
            return False
        return result.get('layout_type') in ('vertical', 'horizontal')

    def _restructure_group(self, group):
        leaves = self._collect_leaves(group)
        if len(leaves) < self.config.min_children_to_cluster:
            return

        # 保护：若子层明显越出父容器边界（常见于标题贴片/角标负偏移），
        # 强行 row/col 化会丢失负偏移语义，导致整体下沉/错位。
        # 该类组保持 absolute 更稳妥。
        fallback_bbox = self._envelope([l.bbox for l in leaves])
        container_bbox = self._container_css_bbox(group, fallback=fallback_bbox)
        if self._has_significant_overflow(leaves, container_bbox):
            name = group.get('data-name', 'unknown')
            print(f"    ⏭ {name}: 子层越界明显，保持 absolute")
            return

        tree = self._build_tree(leaves)

        if tree.kind == 'leaf':
            return

        # 纯图片装饰组优先保守：避免把重叠装饰误改成 flex 后出现层级错乱。
        if tree.kind in ('row', 'col'):
            image_only = all((l.data_type or '') == 'image' for l in leaves)
            if image_only and not self._can_flex_applier_handle(group):
                name = group.get('data-name', 'unknown')
                print(f"    ⏭ {name}: 纯图片装饰组，保持 absolute")
                return

        if tree.kind == 'stack':
            has_flex_subtree = any(c.kind in ('row', 'col') for c in tree.children)
            if not has_flex_subtree:
                name = group.get('data-name', 'unknown')
                if self._can_flex_applier_handle(group):
                    print(f"    ⏭ {name}: 叠图组判定撤销，转交 FlexApplier")
                    return
                print(f"    ⊙ {name}: 识别为叠图组 ({len(leaves)} 个图层)，保持 absolute")
                return

            name = group.get('data-name', 'unknown')

            absorbed_leaves = self.background.absorb_normal_backgrounds(group, tree)

            if absorbed_leaves:
                tree.children = [
                    c for c in tree.children
                    if not (c.kind == 'leaf' and c.leaf in absorbed_leaves)
                ]
                for leaf in absorbed_leaves:
                    # 只有真的删除了才删除 CSS 规则
                    if self._safe_extract(leaf.element):
                        # ✅ 核心修复：只删除装饰性背景的 CSS，保留内容元素（text）的 CSS
                        # 理由：text 等内容元素的 CSS 规则会被后续转换器使用，
                        # 无条件删除会导致 font-size、color 等属性丢失
                        if leaf.data_type in ('image', 'layer-group'):
                            self.css_rules.pop(leaf.css_class, None)
                    
                    # 删除孤立的全局 class 规则
                    # 当某个具体类（如 .img__50）被删除时，需要检查是否有相关的全局类
                    # （如 .img）需要被一起删除，以防止 BackgroundLayerAbsorptionStage
                    # 后续错误地识别和吸收孤立元素
                    self._cleanup_orphaned_class_rules(leaf.css_class)

            if (len(tree.children) == 1 and
                    tree.children[0].kind in ('row', 'col')):
                fg = tree.children[0]
                remaining_leaves = [
                    l for l in leaves if l not in absorbed_leaves
                ]
                summary = self._summarize_tree(fg)
                absorbed_info = (
                    f"（吸收 {len(absorbed_leaves)} 个背景）"
                    if absorbed_leaves else ""
                )
                print(f"    ✓ {name}: 背景吸收 → flex {summary}{absorbed_info}")

                for leaf in remaining_leaves:
                    # 删除时检查是否有 CSS 需要清理
                    if self._safe_extract(leaf.element):
                        # ✅ 核心修复：remaining_leaves 中的元素不是被吸收的背景，
                        # 而是参与 flex 布局的元素。这些元素的 CSS 应该被保留，
                        # 以便 flex 转换器能够正确处理它们的样式。
                        # 不删除任何 CSS 规则。
                        pass

                self.rendering.apply_flex_to_existing_container(group, fg)

                root_kind = fg.kind
                if root_kind == 'row':
                    sorted_root_children = sorted(
                        fg.children, key=lambda c: (c.bbox.left, c.bbox.top))
                else:
                    sorted_root_children = sorted(
                        fg.children, key=lambda c: (c.bbox.top, c.bbox.left))

                prev_bbox: Optional[BBox] = None
                for child_tree in sorted_root_children:
                    if child_tree.kind == 'leaf':
                        child_elem = child_tree.leaf.element
                        child_css_class = child_tree.leaf.css_class
                    else:
                        child_elem = self.rendering.render_tree(
                            child_tree, parent_origin=fg.bbox)
                        virtual_class = child_elem.get('class', [])
                        child_css_class = (
                            f".{virtual_class[0]}" if virtual_class else None
                        )
                    child_position = (
                        'relative' if child_tree.kind == 'stack' else 'static'
                    )
                    self.rendering.apply_flex_child_margins(
                        child_css_class,
                        child_bbox=child_tree.bbox,
                        parent_bbox=fg.bbox,
                        prev_bbox=prev_bbox,
                        flex_kind=root_kind,
                        child_position=child_position,
                    )
                    group.append(child_elem)
                    prev_bbox = child_tree.bbox
                return

            summary = self._summarize_tree(tree)
            absorbed_info = (
                f"（吸收 {len(absorbed_leaves)} 个背景）"
                if absorbed_leaves else ""
            )
            print(f"    ✓ {name}: 背景剥离 → {summary}{absorbed_info}")

            remaining_leaves = [
                l for l in leaves if l not in absorbed_leaves
            ]
            for leaf in remaining_leaves:
                # 删除时检查是否有 CSS 需要清理
                if self._safe_extract(leaf.element):
                    # ✅ 核心修复：remaining_leaves 中的元素不是被吸收的背景，
                    # 而是参与 stack 布局的元素。这些元素的 CSS 应该被保留，
                    # 以便后续转换器能够正确处理它们的样式。
                    # 不删除任何 CSS 规则。
                    pass

            self.rendering.apply_stack_to_existing_container(group, tree)

            for child_tree in tree.children:
                if child_tree.kind == 'leaf':
                    leaf = child_tree.leaf
                    styles = self.css_rules.setdefault(leaf.css_class, {})
                    styles['position'] = 'absolute'
                    # stack 写回已有容器时，子层坐标应直接相对容器原点，
                    # 不能再减去 tree.bbox（否则会依赖容器 padding 才能复位）。
                    styles['left'] = f'{int(round(leaf.bbox.left))}px'
                    styles['top'] = f'{int(round(leaf.bbox.top))}px'
                    for k in ('margin', 'margin-left', 'margin-top',
                              'margin-right', 'margin-bottom'):
                        styles.pop(k, None)
                    group.append(leaf.element)
                else:
                    child_elem = self.rendering.render_tree(child_tree, parent_origin=tree.bbox)
                    sub_classes = child_elem.get('class', [])
                    if sub_classes:
                        sub_css_class = f'.{sub_classes[0]}'
                        sub_styles = self.css_rules.setdefault(sub_css_class, {})
                        sub_styles['position'] = 'absolute'
                        # 同上：保持相对 group 原点的真实偏移。
                        sub_styles['left'] = f'{int(round(child_tree.bbox.left))}px'
                        sub_styles['top'] = f'{int(round(child_tree.bbox.top))}px'
                        for k in ('margin', 'margin-left', 'margin-top',
                                  'margin-right', 'margin-bottom'):
                            sub_styles.pop(k, None)
                    group.append(child_elem)
            return

        name = group.get('data-name', 'unknown')
        summary = self._summarize_tree(tree)
        print(f"    ✓ {name}: 聚类为 {summary}")

        for leaf in leaves:
            leaf.element.extract()

        self.rendering.apply_flex_to_existing_container(group, tree)

        root_kind = tree.kind
        if root_kind == 'row':
            sorted_root_children = sorted(tree.children, key=lambda c: (c.bbox.left, c.bbox.top))
        else:
            sorted_root_children = sorted(tree.children, key=lambda c: (c.bbox.top, c.bbox.left))

        prev_bbox: Optional[BBox] = None
        for child_tree in sorted_root_children:
            if child_tree.kind == 'leaf':
                child_elem = child_tree.leaf.element
                child_css_class = child_tree.leaf.css_class
            else:
                child_elem = self.rendering.render_tree(child_tree, parent_origin=tree.bbox)
                virtual_class = child_elem.get('class', [])
                child_css_class = f".{virtual_class[0]}" if virtual_class else None

            child_position = 'relative' if child_tree.kind == 'stack' else 'static'

            self.rendering.apply_flex_child_margins(
                child_css_class,
                child_bbox=child_tree.bbox,
                parent_bbox=tree.bbox,
                prev_bbox=prev_bbox,
                flex_kind=root_kind,
                child_position=child_position,
            )

            group.append(child_elem)
            prev_bbox = child_tree.bbox

    # ------------------------------------------------------------------
    # 叶子收集
    # ------------------------------------------------------------------

    def _collect_leaves(self, group) -> List[LeafInfo]:
        leaves: List[LeafInfo] = []
        for child in list(group.find_all(recursive=False)):
            classes = child.get('class', [])
            if not classes:
                continue
            css_class = f".{classes[0]}"
            if css_class not in self.css_rules:
                continue

            styles = self.css_rules[css_class]
            try:
                left = self.parser.parse_px(styles.get('left', '0'))
                top = self.parser.parse_px(styles.get('top', '0'))
                width = self.parser.parse_px(styles.get('width', '0'))
                height = self.parser.parse_px(styles.get('height', '0'))
            except (ValueError, AttributeError):
                continue

            leaves.append(
                LeafInfo(
                    element=child,
                    css_class=css_class,
                    name=child.get('data-name', ''),
                    data_type=child.get('data-type', ''),
                    bbox=BBox(left, top, left + width, top + height),
                )
            )
        return leaves

    def _container_css_bbox(
        self, container, fallback: "BBox",
    ) -> "BBox":
        """返回容器自身 CSS 坐标系下的 bbox"""
        classes = container.get('class', [])
        if classes:
            styles = self.css_rules.get(f'.{classes[0]}')
            if styles:
                try:
                    cw = self.parser.parse_px(styles.get('width', '0'))
                    ch = self.parser.parse_px(styles.get('height', '0'))
                    if cw > 0 and ch > 0:
                        return BBox(0.0, 0.0, cw, ch)
                except (ValueError, AttributeError):
                    pass
        return fallback

    @staticmethod
    def _has_significant_overflow(
        leaves: List[LeafInfo],
        container_bbox: "BBox",
        tolerance: float = 4.0,
    ) -> bool:
        """判断是否存在明显越界子层。

        仅把超过容忍阈值的越界视作风险，避免 1~2px 误差触发保守分支。
        """
        for leaf in leaves:
            b = leaf.bbox
            if (
                b.left < container_bbox.left - tolerance
                or b.top < container_bbox.top - tolerance
                or b.right > container_bbox.right + tolerance
                or b.bottom > container_bbox.bottom + tolerance
            ):
                return True
        return False

    # ------------------------------------------------------------------
    # 构建布局树
    # ------------------------------------------------------------------

    def _build_tree(self, leaves: List[LeafInfo]) -> LayoutNode:
        if len(leaves) == 1:
            leaf = leaves[0]
            return LayoutNode(kind='leaf', bbox=leaf.bbox, leaf=leaf)

        # 改进：先提取装饰层（在背景还存在的完整集合上），避免背景对中位数的影响
        # 这样装饰层提取的条件更稳定、更一致
        decor_leaves_initial, remaining_initial = self.tall_decor.extract_tall_decor_leaves(leaves)
        
        # 如果有装饰层被提取，基于剩余元素做背景剥离
        work_leaves = remaining_initial if decor_leaves_initial else leaves
        
        bg_leaves, fg_leaves = self.background.extract_leaves(work_leaves)
        if bg_leaves and len(fg_leaves) >= 1:
            fg_tree = self._build_tree_without_bg(fg_leaves)
            # 只要识别出背景层，就优先保留 "stack(bg + fg_tree)" 语义。
            # 之前仅在 fg_tree 为 row/col 时才回包 stack，导致 fg_tree=stack 场景
            # 会退化回全量重聚类，把大背景误卷入 col/row，进而出现前景被底图盖住。
            envelope = self._envelope([l.bbox for l in leaves])
            children = [self.clustering._leaf_to_node(bg) for bg in bg_leaves]
            children.append(fg_tree)
            # 如果初始有装饰层，也加进来
            if decor_leaves_initial:
                children = [self.clustering._leaf_to_node(d) for d in decor_leaves_initial] + children
            return LayoutNode(
                kind='stack',
                bbox=envelope,
                children=children,
            )

        # 如果初始提取的装饰层存在，且剩余前景可以聚类成row/col
        if decor_leaves_initial and len(remaining_initial) >= 2:
            fg_tree = self.clustering.cluster(remaining_initial) if not self.clustering.is_stack_group(
                [l.bbox for l in remaining_initial]) else LayoutNode(
                    kind='stack',
                    bbox=self._envelope([l.bbox for l in remaining_initial]),
                    children=[self.clustering._leaf_to_node(l) for l in remaining_initial],
                )
            if fg_tree.kind in ('row', 'col'):
                envelope = self._envelope([l.bbox for l in leaves])
                children = [self.clustering._leaf_to_node(d) for d in decor_leaves_initial]
                children.append(fg_tree)
                return LayoutNode(
                    kind='stack',
                    bbox=envelope,
                    children=children,
                )

        if self.clustering.is_stack_group([l.bbox for l in leaves]):
            return LayoutNode(
                kind='stack',
                bbox=self._envelope([l.bbox for l in leaves]),
                children=[self.clustering._leaf_to_node(l) for l in leaves],
            )

        return self.clustering.cluster(leaves)

    def _build_tree_without_bg(self, leaves: List[LeafInfo]) -> LayoutNode:
        """构建前景 leaves 的布局树（不再递归做背景剥离，也不再做装饰层提取，因为已在上层做过）"""
        if len(leaves) == 1:
            leaf = leaves[0]
            return LayoutNode(kind='leaf', bbox=leaf.bbox, leaf=leaf)

        if self.clustering.is_stack_group([l.bbox for l in leaves]):
            return LayoutNode(
                kind='stack',
                bbox=self._envelope([l.bbox for l in leaves]),
                children=[self.clustering._leaf_to_node(l) for l in leaves],
            )

        return self.clustering.cluster(leaves)

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    @staticmethod
    def _envelope(bboxes: List[BBox]) -> BBox:
        return BBox(
            left=min(b.left for b in bboxes),
            top=min(b.top for b in bboxes),
            right=max(b.right for b in bboxes),
            bottom=max(b.bottom for b in bboxes),
        )

    def _summarize_tree(self, tree: LayoutNode) -> str:
        def walk(node: LayoutNode) -> str:
            if node.kind == 'leaf':
                return 'leaf'
            tag = {'row': 'R', 'col': 'C', 'stack': 'S'}[node.kind]
            inner = ','.join(walk(c) for c in node.children)
            return f"{tag}[{inner}]"
        return walk(tree)

    def _next_virtual_id(self, kind: str) -> str:
        self._virtual_seq += 1
        return f"v-{kind}-{self._virtual_seq}"
    
    def _cleanup_orphaned_class_rules(self, deleted_css_class: str) -> None:
        """删除孤立的全局 class 规则。
        
        当删除一个具体的 class（如 .img__50）时，检查是否有相关的更短全局类
        （如 .img）没有对应的 HTML 元素在使用。如果是孤立的，也要一起删除，
        以防止后续 BackgroundLayerAbsorptionStage 错误地识别和吸收孤立元素。
        
        例如：
        - 删除 .img__50 时，检查 .img 是否还被其他元素使用
        - 如果 .img 没有对应的 HTML 元素，则删除 .img 规则
        """
        if not deleted_css_class.startswith('.'):
            return
        
        # 提取基础 class 名（去掉 __suffix）
        class_name = deleted_css_class[1:]  # 去掉 '.'
        
        # 查找潜在的全局类（例如从 .img__50 → .img）
        if '__' not in class_name:
            return  # 已经是最短形式，无法进一步简化
        
        base_class = class_name.split('__')[0]  # 取第一个 __ 前的部分
        global_selector = f'.{base_class}'
        
        # 检查是否存在全局类规则
        if global_selector not in self.css_rules:
            return
        
        # 检查 HTML 中是否还有元素使用这个全局类
        global_class_used = False
        for elem in self.soup.find_all(class_=True):
            classes = elem.get('class', [])
            if base_class in classes:
                global_class_used = True
                break
        
        # 如果全局类没有被任何元素使用，则删除该 CSS 规则
        if not global_class_used:
            self.css_rules.pop(global_selector, None)
