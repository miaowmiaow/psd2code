"""Stack → Col 反向升级

背景吸收后重新评估 v-stack 容器：若剩余子元素呈现"完美单列多行列表"，
升级为 v-col（带 display:flex; flex-direction:column）。

典型场景：dom_restructure 早期判 stack 的容器，背景吸收后底框被吸走，
剩下的实际是真列布局。
"""

from typing import Dict, List, Optional

from .data_types import BBox, LeafInfo


class ReclassifyMixin:
    """Stack → Col 反向升级 Mixin

    使用者须提供：
    - ``self.config`` (ClusterConfig)
    - ``self.css_rules``
    - ``self.parser`` (CSSParser)
    - ``self.stats``
    - ``self._split_by_rows()``（来自 ClusteringMixin）
    - ``self._apply_flex_child_margins()``（来自 RenderingMixin）
    """

    def _absorb_container_backgrounds_pass(self):
        """遍历所有容器，对每个容器的直接子 image leaf 做统一背景吸收"""
        stats_absorbed = 0
        absorbed_containers: List = []

        containers = self._collect_bg_absorb_target_containers()

        for container in containers:
            absorbed = self._try_absorb_container_bg(container)
            if absorbed:
                stats_absorbed += len(absorbed)
                container['data-bg-absorbed'] = '1'
                absorbed_containers.append(container)

        if stats_absorbed > 0:
            print(f"    🌐 容器背景吸收 pass: 共吸收 {stats_absorbed} 个 image leaf 为容器 background")

        if self.config.enable_stack_to_col_reclassify and absorbed_containers:
            self._reclassify_stacks_after_bg_absorption(absorbed_containers)

        for el in self.soup.find_all(attrs={'data-no-bg-absorb': True}):
            del el['data-no-bg-absorb']

    def _collect_bg_absorb_target_containers(self) -> List:
        """采集所有可能作为"背景吸收目标"的容器元素"""
        result = []
        result.extend(
            self.soup.find_all(
                'div',
                class_=lambda x: x and 'layer-group' in str(x),
            )
        )
        result.extend(
            self.soup.find_all('div', attrs={'data-virtual': True})
        )
        seen = set()
        unique = []
        for elem in result:
            key = id(elem)
            if key in seen:
                continue
            seen.add(key)
            unique.append(elem)
        return unique

    def _try_absorb_container_bg(self, container) -> List[LeafInfo]:
        """对单个容器尝试吸收其直接子中的全覆盖背景 image leaf"""
        classes = container.get('class', [])
        if not classes:
            return []
        container_css_class = f'.{classes[0]}'
        container_styles = self.css_rules.get(container_css_class)
        if not container_styles:
            return []
        try:
            cw = self.parser.parse_px(container_styles.get('width', '0'))
            ch = self.parser.parse_px(container_styles.get('height', '0'))
        except (ValueError, AttributeError):
            return []
        if cw <= 0 or ch <= 0:
            return []

        container_bbox = BBox(0.0, 0.0, cw, ch)

        children_info = self._collect_direct_children_info(container)
        if not children_info:
            return []

        cover_ratio = self.config.container_bg_cover_ratio
        overflow_tol = self.config.container_bg_overflow_tolerance_px

        candidates: List = []
        for info in children_info:
            if info['kind'] != 'leaf':
                continue
            leaf = info['leaf']
            styles = info['styles']
            if leaf.element.get('data-no-bg-absorb'):
                continue
            if not self._is_absorbable_bg_leaf(leaf, styles):
                continue

            lbw = leaf.bbox.width
            lbh = leaf.bbox.height
            if lbw / cw < cover_ratio or lbh / ch < cover_ratio:
                continue
            if (leaf.bbox.left < -overflow_tol or
                    leaf.bbox.top < -overflow_tol or
                    leaf.bbox.right > cw + overflow_tol or
                    leaf.bbox.bottom > ch + overflow_tol):
                continue
            
            # 修复：排除有子元素的背景层（否则删除时会丢失子元素）
            if leaf.element:
                has_children = any(
                    getattr(c, 'name', None) == 'div'
                    for c in leaf.element.find_all(recursive=False)
                )
                if has_children:
                    # 有子元素，不吸收，保留原有结构
                    continue
            
            candidates.append((leaf, styles))

        if not candidates:
            return []

        candidate_leaves = {id(c[0]) for c in candidates}
        non_candidate_z_list: List[int] = []
        for info in children_info:
            if info['kind'] == 'leaf' and id(info['leaf']) in candidate_leaves:
                continue
            z = info.get('z_index')
            if z is not None:
                non_candidate_z_list.append(z)

        candidate_z_list: List[int] = []
        for leaf, styles in candidates:
            try:
                z = int(float(styles.get('z-index', '0')))
            except (ValueError, TypeError):
                z = 0
            candidate_z_list.append(z)
        max_candidate_z = max(candidate_z_list) if candidate_z_list else 0

        if non_candidate_z_list and max_candidate_z > min(non_candidate_z_list):
            return []

        absorbed = self._merge_bg_candidates_into_container_css(
            container_elem=container,
            container_bbox=container_bbox,
            candidates=candidates,
        )
        if not absorbed:
            return []

        for leaf in absorbed:
            # 修复：如果有子元素，先提升到父级再删除
            if leaf.element:
                children_divs = [
                    c for c in leaf.element.find_all(recursive=False)
                    if getattr(c, 'name', None) == 'div'
                ]
                if children_divs:
                    # 逆序提升以保持原有顺序
                    for child in reversed(children_divs):
                        child.extract()
                        leaf.element.insert_after(child)
            
            leaf.element.extract()
            # ✅ 核心修复：只删除装饰性背景的 CSS，保留内容元素的 CSS
            if leaf.data_type in ('image', 'layer-group'):
                self.css_rules.pop(leaf.css_class, None)

        return absorbed

    def _collect_direct_children_info(self, container) -> List[Dict]:
        """采集 container 的直接子元素信息"""
        result: List[Dict] = []
        for child in list(container.find_all(recursive=False)):
            classes = child.get('class', [])
            if not classes:
                continue
            css_class = f'.{classes[0]}'
            styles = self.css_rules.get(css_class)
            if styles is None:
                continue

            z_raw = styles.get('z-index')
            try:
                z_index = int(float(z_raw)) if z_raw is not None else None
            except (ValueError, TypeError):
                z_index = None

            is_layer_group = 'layer-group' in classes
            is_wrapper = child.get('data-virtual') is not None
            if is_layer_group or is_wrapper:
                result.append({
                    'kind': 'group' if is_layer_group else 'wrapper',
                    'leaf': None,
                    'styles': styles,
                    'z_index': z_index,
                })
                continue

            try:
                left = self.parser.parse_px(styles.get('left', '0'))
                top = self.parser.parse_px(styles.get('top', '0'))
                width = self.parser.parse_px(styles.get('width', '0'))
                height = self.parser.parse_px(styles.get('height', '0'))
            except (ValueError, AttributeError):
                continue

            leaf = LeafInfo(
                element=child,
                css_class=css_class,
                name=child.get('data-name', ''),
                data_type=child.get('data-type', ''),
                bbox=BBox(left, top, left + width, top + height),
            )
            result.append({
                'kind': 'leaf',
                'leaf': leaf,
                'styles': styles,
                'z_index': z_index,
            })
        return result

    # ------------------------------------------------------------------
    # Stack → Col 反向升级
    # ------------------------------------------------------------------

    def _reclassify_stacks_after_bg_absorption(self, absorbed_containers: List):
        """背景吸收后，重新评估 v-stack 容器。"""
        upgraded = 0
        for container in absorbed_containers:
            if self._try_reclassify_stack_to_col(container):
                upgraded += 1
        if upgraded > 0:
            print(f"    🔄 Stack→Col 反向升级 pass: 升级 {upgraded} 个 v-stack 为 v-col")

    def _try_reclassify_stack_to_col(self, container) -> bool:
        """对单个 container 尝试 stack → col 升级。"""
        if container.get('data-virtual') != 'stack':
            return False
        classes = container.get('class', [])
        if not classes:
            return False

        container_css_class = f'.{classes[0]}'
        container_styles = self.css_rules.get(container_css_class)
        if not container_styles:
            return False
        try:
            cw = self.parser.parse_px(container_styles.get('width', '0'))
            ch = self.parser.parse_px(container_styles.get('height', '0'))
        except (ValueError, AttributeError):
            return False
        if cw <= 0 or ch <= 0:
            return False

        children_data = self._collect_reclassify_children(container)
        if len(children_data) < self.config.reclassify_min_rows:
            return False

        leaves_for_split = [
            LeafInfo(
                element=cd['element'],
                css_class=cd['css_class'],
                name='',
                data_type='',
                bbox=cd['bbox'],
            )
            for cd in children_data
        ]

        leaves_for_split.sort(key=lambda l: (l.bbox.top, l.bbox.left))

        rows = self._split_by_rows(leaves_for_split)
        if len(rows) != len(leaves_for_split):
            return False
        if len(rows) < self.config.reclassify_min_rows:
            return False

        # 横向覆盖率校验
        if len(rows) == 2:
            x_ratio_threshold = self.config.reclassify_n2_min_x_overlap
        else:
            x_ratio_threshold = self.config.reclassify_x_overlap_ratio
        for i in range(len(rows) - 1):
            a = rows[i][0].bbox
            b = rows[i + 1][0].bbox
            overlap_x = max(0.0, min(a.right, b.right) - max(a.left, b.left))
            min_w = min(a.width, b.width)
            if min_w <= 0:
                return False
            if overlap_x / min_w < x_ratio_threshold:
                return False

        # gap 均匀度校验
        gaps: List[float] = []
        for i in range(len(rows) - 1):
            gap = rows[i + 1][0].bbox.top - rows[i][0].bbox.bottom
            gaps.append(gap)

        if len(rows) == 2 and gaps:
            if gaps[0] > self.config.reclassify_n2_max_gap_px:
                return False
        elif gaps:
            mean_gap = sum(gaps) / len(gaps)
            if abs(mean_gap) > 0.5:
                variance = sum((g - mean_gap) ** 2 for g in gaps) / len(gaps)
                std_gap = variance ** 0.5
                cv = std_gap / abs(mean_gap)
                if cv > self.config.reclassify_gap_cv_max:
                    return False

        self._upgrade_stack_container_to_col(
            container=container,
            container_css_class=container_css_class,
            container_styles=container_styles,
            container_bbox=BBox(0.0, 0.0, cw, ch),
            ordered_children=children_data,
            rows=rows,
        )
        return True

    def _collect_reclassify_children(self, container) -> List[Dict]:
        """采集 container 的直接子，每项含 element / css_class / styles / bbox"""
        result: List[Dict] = []
        for child in list(container.find_all(recursive=False)):
            classes = child.get('class', [])
            if not classes:
                return []
            css_class = f'.{classes[0]}'
            styles = self.css_rules.get(css_class)
            if styles is None:
                return []
            try:
                left = self.parser.parse_px(styles.get('left', '0'))
                top = self.parser.parse_px(styles.get('top', '0'))
                width = self.parser.parse_px(styles.get('width', '0'))
                height = self.parser.parse_px(styles.get('height', '0'))
            except (ValueError, AttributeError):
                return []
            if width <= 0 or height <= 0:
                return []
            result.append({
                'element': child,
                'css_class': css_class,
                'styles': styles,
                'bbox': BBox(left, top, left + width, top + height),
                'is_wrapper_or_group': (
                    child.get('data-virtual') is not None
                    or 'layer-group' in classes
                ),
            })
        return result

    def _upgrade_stack_container_to_col(
        self,
        container,
        container_css_class: str,
        container_styles: Dict[str, str],
        container_bbox: BBox,
        ordered_children: List[Dict],
        rows: List[List[LeafInfo]],
    ):
        """把 v-stack 容器及其子元素改写为 v-col 形态"""
        old_classes = list(container.get('class', []))
        new_classes = []
        for c in old_classes:
            if c == 'v-stack':
                new_classes.append('v-col')
            else:
                new_classes.append(c)
        container['class'] = new_classes
        container['data-virtual'] = 'col'

        container_styles['display'] = 'flex'
        container_styles['flex-direction'] = 'column'
        container_styles['align-items'] = 'flex-start'

        elem_to_bbox: Dict[int, BBox] = {
            id(cd['element']): cd['bbox'] for cd in ordered_children
        }
        elem_to_iswrapper: Dict[int, bool] = {
            id(cd['element']): cd['is_wrapper_or_group'] for cd in ordered_children
        }

        sorted_leaves = [r[0] for r in rows]

        for leaf in sorted_leaves:
            leaf.element.extract()
        for leaf in sorted_leaves:
            container.append(leaf.element)

        prev_bbox: Optional[BBox] = None
        for leaf in sorted_leaves:
            child_bbox = elem_to_bbox[id(leaf.element)]
            is_wrapper_or_group = elem_to_iswrapper[id(leaf.element)]
            child_position = 'static'
            if is_wrapper_or_group and leaf.element.get('data-virtual') == 'stack':
                child_position = 'relative'
            self._apply_flex_child_margins(
                child_css_class=leaf.css_class,
                child_bbox=child_bbox,
                parent_bbox=container_bbox,
                prev_bbox=prev_bbox,
                flex_kind='col',
                child_position=child_position,
            )
            prev_bbox = child_bbox

        self.stats['dom_restructured'] = self.stats.get('dom_restructured', 0) + 1
