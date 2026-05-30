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
from .background import BackgroundMixin
from .tall_decor import TallDecorMixin
from .clustering import ClusteringMixin
from .rendering import RenderingMixin
from .reclassify import ReclassifyMixin


class DOMRestructure(
    BackgroundMixin,
    TallDecorMixin,
    ClusteringMixin,
    RenderingMixin,
    ReclassifyMixin,
):
    """DOM 重构转换器

    入口：``restructure_dom()``

    对每个 ``layer-group`` 递归做空间聚类，并把结果写回 soup 和 css_rules。
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
            self._absorb_container_backgrounds_pass()

    # ------------------------------------------------------------------
    # 收集所有 group
    # ------------------------------------------------------------------

    def _collect_all_groups(self) -> List:
        """按 DOM 文档顺序收集所有 layer-group 元素"""
        return self.soup.find_all(
            'div',
            class_=lambda x: x and 'layer-group' in str(x),
        )

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

        tree = self._build_tree(leaves)

        if tree.kind == 'leaf':
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

            absorbed_leaves = self._absorb_normal_backgrounds(group, tree)

            if absorbed_leaves:
                tree.children = [
                    c for c in tree.children
                    if not (c.kind == 'leaf' and c.leaf in absorbed_leaves)
                ]
                for leaf in absorbed_leaves:
                    leaf.element.extract()
                    self.css_rules.pop(leaf.css_class, None)

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
                    leaf.element.extract()

                self._apply_flex_to_existing_container(group, fg)

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
                        child_elem = self._render_tree(
                            child_tree, parent_origin=fg.bbox)
                        virtual_class = child_elem.get('class', [])
                        child_css_class = (
                            f".{virtual_class[0]}" if virtual_class else None
                        )
                    child_position = (
                        'relative' if child_tree.kind == 'stack' else 'static'
                    )
                    self._apply_flex_child_margins(
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
                leaf.element.extract()

            self._apply_stack_to_existing_container(group, tree)

            for child_tree in tree.children:
                if child_tree.kind == 'leaf':
                    leaf = child_tree.leaf
                    styles = self.css_rules.setdefault(leaf.css_class, {})
                    styles['position'] = 'absolute'
                    styles['left'] = f'{int(round(leaf.bbox.left - tree.bbox.left))}px'
                    styles['top'] = f'{int(round(leaf.bbox.top - tree.bbox.top))}px'
                    for k in ('margin', 'margin-left', 'margin-top',
                              'margin-right', 'margin-bottom'):
                        styles.pop(k, None)
                    group.append(leaf.element)
                else:
                    child_elem = self._render_tree(child_tree, parent_origin=tree.bbox)
                    sub_classes = child_elem.get('class', [])
                    if sub_classes:
                        sub_css_class = f'.{sub_classes[0]}'
                        sub_styles = self.css_rules.setdefault(sub_css_class, {})
                        sub_styles['position'] = 'absolute'
                        sub_styles['left'] = f'{int(round(child_tree.bbox.left - tree.bbox.left))}px'
                        sub_styles['top'] = f'{int(round(child_tree.bbox.top - tree.bbox.top))}px'
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

        self._apply_flex_to_existing_container(group, tree)

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
                child_elem = self._render_tree(child_tree, parent_origin=tree.bbox)
                virtual_class = child_elem.get('class', [])
                child_css_class = f".{virtual_class[0]}" if virtual_class else None

            child_position = 'relative' if child_tree.kind == 'stack' else 'static'

            self._apply_flex_child_margins(
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

    # ------------------------------------------------------------------
    # 构建布局树
    # ------------------------------------------------------------------

    def _build_tree(self, leaves: List[LeafInfo]) -> LayoutNode:
        if len(leaves) == 1:
            leaf = leaves[0]
            return LayoutNode(kind='leaf', bbox=leaf.bbox, leaf=leaf)

        bg_leaves, fg_leaves = self._extract_background_leaves(leaves)
        if bg_leaves and len(fg_leaves) >= 1:
            fg_tree = self._build_tree_without_bg(fg_leaves)
            if fg_tree.kind in ('row', 'col'):
                envelope = self._envelope([l.bbox for l in leaves])
                children = [self._leaf_to_node(bg) for bg in bg_leaves]
                children.append(fg_tree)
                return LayoutNode(
                    kind='stack',
                    bbox=envelope,
                    children=children,
                )

        decor_leaves, fg_leaves2 = self._extract_tall_decor_leaves(leaves)
        if decor_leaves and len(fg_leaves2) >= 2:
            fg_tree = self._build_tree_without_bg(fg_leaves2)
            if fg_tree.kind in ('row', 'col'):
                envelope = self._envelope([l.bbox for l in leaves])
                children = [self._leaf_to_node(d) for d in decor_leaves]
                children.append(fg_tree)
                return LayoutNode(
                    kind='stack',
                    bbox=envelope,
                    children=children,
                )

        if self._is_stack_group([l.bbox for l in leaves]):
            return LayoutNode(
                kind='stack',
                bbox=self._envelope([l.bbox for l in leaves]),
                children=[self._leaf_to_node(l) for l in leaves],
            )

        return self._cluster(leaves)

    def _build_tree_without_bg(self, leaves: List[LeafInfo]) -> LayoutNode:
        """构建前景 leaves 的布局树（不再递归做背景剥离）"""
        if len(leaves) == 1:
            leaf = leaves[0]
            return LayoutNode(kind='leaf', bbox=leaf.bbox, leaf=leaf)

        decor_leaves, fg_leaves = self._extract_tall_decor_leaves(leaves)
        if decor_leaves and len(fg_leaves) >= 2:
            inner_tree = self._cluster(fg_leaves) if not self._is_stack_group(
                [l.bbox for l in fg_leaves]) else LayoutNode(
                    kind='stack',
                    bbox=self._envelope([l.bbox for l in fg_leaves]),
                    children=[self._leaf_to_node(l) for l in fg_leaves],
                )
            if inner_tree.kind in ('row', 'col'):
                envelope = self._envelope([l.bbox for l in leaves])
                children = [self._leaf_to_node(d) for d in decor_leaves]
                children.append(inner_tree)
                return LayoutNode(
                    kind='stack',
                    bbox=envelope,
                    children=children,
                )

        if self._is_stack_group([l.bbox for l in leaves]):
            return LayoutNode(
                kind='stack',
                bbox=self._envelope([l.bbox for l in leaves]),
                children=[self._leaf_to_node(l) for l in leaves],
            )

        return self._cluster(leaves)

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
