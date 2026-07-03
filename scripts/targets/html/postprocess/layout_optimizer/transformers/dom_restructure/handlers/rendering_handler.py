"""渲染 Handler

DOM 渲染：将布局树写回 DOM。
"""

from typing import Dict, Optional

from ..data_types import BBox, LayoutNode
from .base import DOMHandler


class RenderingHandler(DOMHandler):
    """DOM 渲染 Handler"""

    def render_tree(self, node: LayoutNode, parent_origin: BBox):
        """递归渲染布局节点为 DOM 元素"""
        if node.kind == 'leaf':
            return self._render_leaf(node, parent_origin)
        if node.kind == 'stack':
            return self._render_stack(node, parent_origin)
        return self._render_flex(node, parent_origin)

    def _render_leaf(self, node: LayoutNode, parent_origin: BBox):
        """叶子节点：返回元素"""
        return node.leaf.element

    def _render_stack(self, node: LayoutNode, parent_origin: BBox):
        """叠图容器：创建 wrapper，子元素保留 absolute 但坐标相对 wrapper"""
        wrapper = self._make_wrapper_div('stack')
        self._write_wrapper_css(wrapper, node.bbox, parent_origin, flex_kind=None)

        for child in node.children:
            if child.kind != 'leaf':
                rendered = self.render_tree(child, parent_origin=node.bbox)
                sub_classes = rendered.get('class', [])
                if sub_classes:
                    sub_css_class = f'.{sub_classes[0]}'
                    sub_styles = self.owner.css_rules.setdefault(sub_css_class, {})
                    sub_left = child.bbox.left - node.bbox.left
                    sub_top = child.bbox.top - node.bbox.top
                    sub_styles['position'] = 'absolute'
                    sub_styles['left'] = f'{int(round(sub_left))}px'
                    sub_styles['top'] = f'{int(round(sub_top))}px'
                    for k in ('margin', 'margin-left', 'margin-top',
                              'margin-right', 'margin-bottom'):
                        sub_styles.pop(k, None)
                wrapper.append(rendered)
                continue

            leaf = child.leaf
            new_left = leaf.bbox.left - node.bbox.left
            new_top = leaf.bbox.top - node.bbox.top

            styles = self.owner.css_rules.setdefault(leaf.css_class, {})
            styles['position'] = 'absolute'
            styles['left'] = f'{int(round(new_left))}px'
            styles['top'] = f'{int(round(new_top))}px'
            for k in ('margin', 'margin-left', 'margin-top', 'margin-right', 'margin-bottom'):
                styles.pop(k, None)

            wrapper.append(leaf.element)

        self.owner.stats['dom_restructured'] += 1
        return wrapper

    def _render_flex(self, node: LayoutNode, parent_origin: BBox):
        """row / col 容器：创建 wrapper，子元素用 margin 表达偏移"""
        wrapper = self._make_wrapper_div(node.kind)
        self._write_wrapper_css(wrapper, node.bbox, parent_origin, flex_kind=node.kind)

        if node.kind == 'row':
            sorted_children = sorted(node.children, key=lambda c: (c.bbox.left, c.bbox.top))
        else:
            sorted_children = sorted(node.children, key=lambda c: (c.bbox.top, c.bbox.left))

        prev_bbox: Optional[BBox] = None
        for child in sorted_children:
            if child.kind == 'leaf':
                child_elem = child.leaf.element
                child_css_class = child.leaf.css_class
            else:
                child_elem = self.render_tree(child, parent_origin=node.bbox)
                virtual_class = child_elem.get('class', [])
                child_css_class = f".{virtual_class[0]}" if virtual_class else None

            child_position = 'relative' if child.kind == 'stack' else 'static'

            self.apply_flex_child_margins(
                child_css_class,
                child_bbox=child.bbox,
                parent_bbox=node.bbox,
                prev_bbox=prev_bbox,
                flex_kind=node.kind,
                child_position=child_position,
            )

            wrapper.append(child_elem)
            prev_bbox = child.bbox

        self.owner.stats['dom_restructured'] += 1
        return wrapper

    def apply_flex_child_margins(
        self,
        child_css_class: Optional[str],
        child_bbox: BBox,
        parent_bbox: BBox,
        prev_bbox: Optional[BBox],
        flex_kind: str,
        child_position: str = 'static',
    ):
        """子元素在 row/col 容器中，用 margin 表达偏移，移除 left/top"""
        if not child_css_class:
            return
        styles = self.owner.css_rules.setdefault(child_css_class, {})

        for k in ('left', 'top', 'right', 'bottom'):
            styles.pop(k, None)

        styles.pop('position', None)
        if child_position == 'static':
            has_z_index = styles.get('z-index') not in (None, 'auto', '')
            if has_z_index:
                styles['position'] = 'relative'
        else:
            styles['position'] = child_position

        for k in ('margin', 'margin-left', 'margin-top', 'margin-right', 'margin-bottom'):
            styles.pop(k, None)

        if flex_kind == 'row':
            origin_left = max(0.0, parent_bbox.left)
            origin_top = max(0.0, parent_bbox.top)
            if prev_bbox is None:
                main_gap = child_bbox.left - origin_left
            else:
                main_gap = child_bbox.left - prev_bbox.right
            cross_offset = child_bbox.top - origin_top

            if abs(main_gap) > 0.5:
                styles['margin-left'] = f'{int(round(main_gap))}px'
            if abs(cross_offset) > 0.5:
                styles['margin-top'] = f'{int(round(cross_offset))}px'
        else:  # col
            origin_left = max(0.0, parent_bbox.left)
            origin_top = max(0.0, parent_bbox.top)
            if prev_bbox is None:
                main_gap = child_bbox.top - origin_top
            else:
                main_gap = child_bbox.top - prev_bbox.bottom
            cross_offset = child_bbox.left - origin_left

            if abs(main_gap) > 0.5:
                styles['margin-top'] = f'{int(round(main_gap))}px'
            if abs(cross_offset) > 0.5:
                styles['margin-left'] = f'{int(round(cross_offset))}px'

        styles['flex-shrink'] = '0'

    def apply_flex_to_existing_container(self, group_elem, tree: LayoutNode):
        """把树的根节点（row / col）flex 样式应用到已有的 group 容器上"""
        classes = group_elem.get('class', [])
        if not classes:
            return
        css_class = f".{classes[0]}"
        styles = self.owner.css_rules.setdefault(css_class, {})

        if tree.kind == 'stack':
            return

        styles['display'] = 'flex'
        styles['flex-direction'] = 'row' if tree.kind == 'row' else 'column'
        styles['align-items'] = 'flex-start'

        pad_left = int(round(tree.bbox.left))
        pad_top = int(round(tree.bbox.top))
        if pad_left > 0 or pad_top > 0:
            styles['box-sizing'] = 'border-box'
            if pad_left > 0:
                styles['padding-left'] = f'{pad_left}px'
            if pad_top > 0:
                styles['padding-top'] = f'{pad_top}px'

        marker = 'v-row' if tree.kind == 'row' else 'v-col'
        if marker not in classes:
            classes.append(marker)
            group_elem['class'] = classes

    def apply_stack_to_existing_container(self, group_elem, tree: LayoutNode):
        """把 stack 根节点应用到已有 group 容器上"""
        classes = group_elem.get('class', [])
        if not classes:
            return
        css_class = f".{classes[0]}"
        styles = self.owner.css_rules.setdefault(css_class, {})

        for k in ('display', 'flex-direction', 'align-items',
                  'justify-content', 'gap'):
            if styles.get(k, '').startswith('flex') or k in (
                'flex-direction', 'align-items', 'justify-content', 'gap'):
                styles.pop(k, None)

        current_pos = (styles.get('position') or '').strip().lower()
        if current_pos not in ('absolute', 'fixed', 'relative', 'sticky'):
            styles['position'] = 'relative'

        marker = 'v-stack'
        if marker not in classes:
            classes.append(marker)
            group_elem['class'] = classes

    def _make_wrapper_div(self, kind: str):
        """创建一个虚拟容器 <div>，注册对应 CSS 类"""
        vid = self.owner._next_virtual_id(kind)
        marker = f'v-{kind}'
        div = self.owner.soup.new_tag('div')
        div['class'] = [vid, marker]
        div['data-virtual'] = kind
        self.owner.css_rules[f'.{vid}'] = {}
        return div

    def _write_wrapper_css(
        self,
        wrapper,
        self_bbox: BBox,
        parent_origin: BBox,
        flex_kind: Optional[str],
    ):
        """给虚拟容器写 CSS"""
        cls = wrapper.get('class', [])
        if not cls:
            return
        css_class = f'.{cls[0]}'
        styles = self.owner.css_rules.setdefault(css_class, {})

        styles['width'] = f'{int(round(self_bbox.width))}px'
        styles['height'] = f'{int(round(self_bbox.height))}px'
        styles['box-sizing'] = 'border-box'

        if flex_kind == 'row':
            styles['display'] = 'flex'
            styles['flex-direction'] = 'row'
            styles['align-items'] = 'flex-start'
        elif flex_kind == 'col':
            styles['display'] = 'flex'
            styles['flex-direction'] = 'column'
            styles['align-items'] = 'flex-start'
        else:
            styles['position'] = 'relative'
