"""Flex布局应用器"""

from typing import Dict
from ..analyzers.layout_analyzer import LayoutAnalyzer


class FlexApplier:
    """
    Flex布局应用器
    
    分析子元素布局特征，自动应用flex-col或flex-row布局
    """
    
    def __init__(self, soup, css_rules: Dict[str, Dict[str, str]], stats: Dict):
        """
        初始化Flex布局应用器
        
        Args:
            soup: BeautifulSoup对象
            css_rules: CSS规则字典
            stats: 统计信息字典
        """
        self.soup = soup
        self.css_rules = css_rules
        self.stats = stats
        self.layout_analyzer = LayoutAnalyzer(css_rules)
    
    def apply_flex_layouts(self):
        """应用flex布局（基于趋势元素检测）

        注意：DOM 重构已经为识别成功的 row/col 容器产出了 flex-ready 结构
        （display:flex + 子元素 margin），并把 v-stack/v-list 等 wrapper
        升级为 positioned 容器。此处跳过这些容器以避免重复处理。
        """
        print("\n🔧 步骤3：应用flex布局...")
        
        for elem in self.soup.find_all('div'):
            classes = elem.get('class', [])
            elem_id = elem.get('id')

            css_selector = None
            if classes:
                css_selector = f'.{classes[0]}'
            elif elem_id:
                css_selector = f'#{elem_id}'
            else:
                continue
            
            # 跳过 DOM 重构已经处理过的 flex 容器
            if 'v-row' in classes or 'v-col' in classes:
                continue
            # 跳过虚拟 stack 容器（其 absolute 子元素已经相对 wrapper）
            if 'v-stack' in classes:
                continue
            # 跳过虚拟 list 容器（同质兄弟分组器已经写入 flex-wrap）
            if 'v-list' in classes:
                continue

            css = self.css_rules.get(css_selector, {})
            
            # 如果 DOM 重构已经把此容器设为 flex（并标注了 v-row/v-col），跳过；
            # 但为了兼容性，再检查一次 display:flex 标记
            if css.get('display') == 'flex':
                continue

            # 检查是否是容器
            children = list(elem.find_all(recursive=False))
            if len(children) < 1:
                continue
            
            # 🔑 核心：分析子元素的布局特征
            layout_analysis = self.layout_analyzer.analyze_children_layout(children)
            
            layout_type = layout_analysis['layout_type']
            vertical_changes = layout_analysis['vertical_changes']
            horizontal_changes = layout_analysis['horizontal_changes']
            all_children = layout_analysis['all_children']
            decor_classes = layout_analysis.get('decor_classes', set())
            
            # ⚠️ 修复（2026-06-23）：根容器特殊处理
            # 当标准趋势检测返回 'none' 时，对于 id 选择器（如 #canvas）的根容器，
            # 应该再次尝试"大背景 + 堆积元素"的识别。这类容器常见于移动页面（750px 宽度）
            # 结构：一个大背景 + 若干绝对定位子元素排成竖列。
            if (layout_type == 'none' and elem_id and 
                elem_id.lower() in ('canvas', 'root', 'page') and
                len([c for c in all_children if c['class'] not in decor_classes]) >= 2):
                # 对根容器降低装饰剥离的阈值，允许大背景被识别为装饰
                orig_decor = set(decor_classes)
                decor_classes = self._identify_root_container_bg(all_children, decor_classes)
                content_children = [c for c in all_children if c['class'] not in decor_classes]
                
                print(f"   🔍 根容器 #{elem_id} 特殊处理：{len(decor_classes)-len(orig_decor)} 个新装饰")
                
                if len(content_children) >= 2:
                    # 再次尝试趋势检测（只在内容子集上）
                    try:
                        v_changes, h_changes = self._count_layout_changes(content_children)
                        print(f"      → 内容子集变化: v={v_changes}, h={h_changes}")
                        if v_changes >= 2 and v_changes > h_changes:
                            layout_type = 'vertical'
                            print(f"      → 判定为 vertical ✅")
                        elif h_changes >= 2 and h_changes > v_changes:
                            layout_type = 'horizontal'
                            print(f"      → 判定为 horizontal ✅")
                    except Exception as e:
                        print(f"      ❌ 重新检测失败: {e}")
                        pass  # 如果重新检测失败，保留 'none'

            elem_name = elem.get('data-name', '') or (f'#{elem_id}' if elem_id else css_selector)

            # V10 装饰剥离：打印被分到 bg/decor 的 class（便于排查）
            if decor_classes and layout_type != 'none':
                print(f"   🎭 {elem_name}: 装饰剥离 {len(decor_classes)} 个 → "
                      f"{sorted(decor_classes)}")

            # 应用对应的flex布局
            if layout_type == 'vertical':
                self._apply_vertical_layout(elem, css, all_children, elem_name, vertical_changes, horizontal_changes, decor_classes)
            elif layout_type == 'horizontal':
                self._apply_horizontal_layout(elem, css, all_children, elem_name, horizontal_changes, vertical_changes, decor_classes)
            elif layout_type == 'none':
                # 即使没有应用flex布局，也需要处理非趋势元素
                self._handle_non_flex_container(elem, css, all_children, elem_name)

    def _reorder_trend_children_for_flow(self, elem, trend_children):
        """在根级容器下同步趋势子节点的 DOM 顺序（最小扰动）。

        背景：layout_analyzer 会按几何排序识别趋势链，但 DOM 原始顺序可能
        与几何顺序不一致。若直接按趋势链计算 margin，不重排 DOM 会导致
        flex 流渲染顺序与计算顺序错位。
        """
        if not trend_children:
            return
        # 仅对带 id 的根级容器启用（例如 #canvas），避免影响一般业务容器。
        elem_id = elem.get('id')
        if not elem_id:
            return

        trend_elems = [c.get('element') for c in trend_children if c.get('element') is not None]
        if len(trend_elems) < 2:
            return

        # 最小扰动策略：仅重排“趋势元素占据的位置槽位”，非趋势元素保持原相对顺序。
        direct_children = list(elem.find_all(recursive=False))
        trend_set = set(trend_elems)
        trend_slots = [i for i, ch in enumerate(direct_children) if ch in trend_set]
        if len(trend_slots) < 2:
            return

        # 构建目标序列：非趋势节点保持原位，趋势槽位按 trend_elems 顺序填充。
        target_children = list(direct_children)
        pending = list(trend_elems)
        for slot in trend_slots:
            target_children[slot] = pending.pop(0)

        # 清空直接子节点并按目标序列重插。
        for ch in direct_children:
            ch.extract()
        for ch in target_children:
            elem.append(ch)
    
    def _apply_vertical_layout(self, elem, css, all_children, elem_name, vertical_changes, horizontal_changes, decor_classes=None):
        """应用垂直布局（flex-col）"""
        decor_classes = decor_classes or set()
        self.stats['flex_applied'] += 1
        print(f"   ✓ flex-col: {elem_name} (垂直变化:{vertical_changes}, 横向变化:{horizontal_changes})")
        
        # 步骤1：遍历子元素，记录趋势元素和非趋势元素
        # decor 子元素一律保留 absolute（无论 is_trend 怎么标记），不参与 flex 流
        trend_children = [c for c in all_children
                          if c['is_trend'] and c['class'] not in decor_classes]
        non_trend_children = [c for c in all_children
                              if not c['is_trend'] or c['class'] in decor_classes]
        
        print(f"      → 趋势元素: {len(trend_children)}个, 非趋势元素: {len(non_trend_children)}个")
        
        # 步骤2：存在趋势元素 → 添加flex布局，转换趋势元素为margin
        if trend_children:
            self._reorder_trend_children_for_flow(elem, trend_children)

            # 为父容器添加flex布局（如果没有）
            if css.get('display') != 'flex':
                css['display'] = 'flex'
                css['flex-direction'] = 'column'
            
            # 找到第一个趋势元素（用于保留初始top偏移）
            first_trend_idx = all_children.index(trend_children[0])
            
            # 处理每个趋势元素
            for i, child_info in enumerate(trend_children):
                child_css = self.css_rules.get(f'.{child_info["class"]}', {})
                idx = all_children.index(child_info)
                
                # 转换 left → margin-left
                if child_info['left'] > 0:
                    child_css['margin-left'] = f"{child_info['left']}px"
                
                # 转换 top → margin-top
                # 如果已经有 margin-top（来自 DOM 重构），则保留它
                if 'margin-top' not in child_css:
                    if idx == first_trend_idx:
                        # 第一个趋势元素：使用原始top作为margin-top
                        if child_info['top'] > 0:
                            child_css['margin-top'] = f"{child_info['top']}px"
                    else:
                        # 后续趋势元素：计算与前一个元素的间距
                        prev_child = trend_children[i - 1]
                        prev_bottom = prev_child['top'] + prev_child['height']
                        gap = child_info['top'] - prev_bottom
                        if gap > 0:
                            child_css['margin-top'] = f"{gap}px"
                
                # 移除 position、top、left
                # 例外 1：子元素本身是 stack wrapper（含 v-stack class）→ 它内部
                # 有 absolute 子节点，必须保留 position:relative 作为 containing
                # block，否则内部子节点会脱离 wrapper 跑到外层 positioned 祖先
                # （典型场景：dom_restructure 把某 group 升级为 v-stack 容器后，
                # 该 group 又作为父 group 的趋势子元素被 flex 化）
                # 例外 2：子元素带 z-index（非 None / 非 auto）→ 必须显式写
                # position:relative 让 z-index 必然建立 stacking context。
                # 否则同容器内"static + z-index"与"relative + z-index"混存时，
                # 不同浏览器对 flex item stacking 的实现差异会导致视觉层级异常
                # （典型场景：抽奖活动页面 .bg-section-2 flex column 内，
                # .group-2-2 是 relative+z=33、.huodongshi/.ninhao/.btn-receive
                # 是 static+z=29/30/31，导致 .group-2-2 的 .alade 头像装饰
                # 偶发被 huodongshi 等遮挡）
                is_stack_wrapper = 'v-stack' in (child_info.get('classes') or [])
                has_z_index = child_css.get('z-index') not in (None, 'auto', '')
                needs_relative = is_stack_wrapper or has_z_index
                if 'position' in child_css:
                    if needs_relative:
                        child_css['position'] = 'relative'
                    else:
                        del child_css['position']
                        self.stats['positions_removed'] += 1
                elif needs_relative:
                    child_css['position'] = 'relative'
                if 'top' in child_css:
                    del child_css['top']
                if 'left' in child_css:
                    del child_css['left']

                # 禁止 flex-shrink（见 dom_restructure._apply_flex_child_margins
                # 的同名注释）：父容器高度有限时，避免子元素按比例压缩
                child_css['flex-shrink'] = '0'
        
        # 步骤3：存在非趋势元素 → 确保父容器有定位上下文（relative / absolute）
        # 说明：非趋势子元素的 left/top 已经是**相对父容器**的坐标（由 extract 阶段
        # 的 rel_left/rel_top 产出），此处 **不需要再做任何坐标换算**。
        # 只要父容器具备定位上下文，这些子元素以 absolute 悬浮即可。
        if non_trend_children:
            if 'position' not in css:
                css['position'] = 'relative'
                print(f"      → 添加 relative（包含{len(non_trend_children)}个非趋势元素）")
            else:
                print(f"      → 已有 position={css['position']}（包含{len(non_trend_children)}个非趋势元素）")

            # 只补齐 position: absolute，保持原 top/left 不动
            for child_info in non_trend_children:
                child_css = self.css_rules.get(f'.{child_info["class"]}', {})
                if child_css.get('position') != 'absolute':
                    child_css['position'] = 'absolute'

    def _apply_horizontal_layout(self, elem, css, all_children, elem_name, horizontal_changes, vertical_changes, decor_classes=None):
        """应用横向布局（flex-row）"""
        decor_classes = decor_classes or set()
        self.stats['flex_applied'] += 1
        print(f"   ✓ flex-row: {elem_name} (横向变化:{horizontal_changes}, 垂直变化:{vertical_changes})")
        
        # 步骤1：遍历子元素，记录趋势元素和非趋势元素
        # decor 子元素一律保留 absolute（无论 is_trend 怎么标记），不参与 flex 流
        trend_children = [c for c in all_children
                          if c['is_trend'] and c['class'] not in decor_classes]
        non_trend_children = [c for c in all_children
                              if not c['is_trend'] or c['class'] in decor_classes]
        
        print(f"      → 趋势元素: {len(trend_children)}个, 非趋势元素: {len(non_trend_children)}个")
        
        # 步骤2：存在趋势元素 → 添加flex布局，转换趋势元素为margin
        if trend_children:
            self._reorder_trend_children_for_flow(elem, trend_children)

            # 为父容器添加flex布局（如果没有）
            if css.get('display') != 'flex':
                css['display'] = 'flex'
                css['flex-direction'] = 'row'
            
            # 处理每个趋势元素
            for child_info in trend_children:
                child_css = self.css_rules.get(f'.{child_info["class"]}', {})
                trend_idx = trend_children.index(child_info)
                
                # 横向间距：计算 margin-left
                if trend_idx == 0:
                    if child_info['left'] > 0:
                        child_css['margin-left'] = f"{child_info['left']}px"
                else:
                    prev_child = trend_children[trend_idx - 1]
                    gap = child_info['left'] - (prev_child['left'] + prev_child['width'])
                    if gap > 0:
                        child_css['margin-left'] = f"{gap}px"
                
                # 垂直对齐：转换 top 为 margin-top
                if child_info['top'] > 0:
                    child_css['margin-top'] = f"{child_info['top']}px"
                
                # 移除 position、top、left
                # 例外：与 _apply_vertical_layout 同名注释一致
                # - v-stack wrapper 子保留 relative 作 containing block
                # - 子元素带 z-index 显式写 relative 让 stacking context 必然生效
                is_stack_wrapper = 'v-stack' in (child_info.get('classes') or [])
                has_z_index = child_css.get('z-index') not in (None, 'auto', '')
                needs_relative = is_stack_wrapper or has_z_index
                if 'position' in child_css:
                    if needs_relative:
                        child_css['position'] = 'relative'
                    else:
                        del child_css['position']
                        self.stats['positions_removed'] += 1
                elif needs_relative:
                    child_css['position'] = 'relative'
                if 'top' in child_css:
                    del child_css['top']
                if 'left' in child_css:
                    del child_css['left']

                # 禁止 flex-shrink（见 _apply_vertical_layout 同名注释）
                child_css['flex-shrink'] = '0'
        
        # 步骤3：存在非趋势元素 → 确保父容器有定位上下文（同 vertical 版本，无需换算坐标）
        if non_trend_children:
            if 'position' not in css:
                css['position'] = 'relative'
                print(f"      → 添加 relative（包含{len(non_trend_children)}个非趋势元素）")
            else:
                print(f"      → 已有 position={css['position']}（包含{len(non_trend_children)}个非趋势元素）")

            for child_info in non_trend_children:
                child_css = self.css_rules.get(f'.{child_info["class"]}', {})
                if child_css.get('position') != 'absolute':
                    child_css['position'] = 'absolute'

    def _handle_non_flex_container(self, elem, css, all_children, elem_name):
        """
        处理非flex布局的容器（layout_type == 'none'）
        
        即使不应用flex布局，也需要确保：
        - 如果存在使用 position: absolute 的子元素
        - 父容器必须添加 position: relative
        """
        # 检查是否有子元素使用了 position: absolute
        has_absolute_children = False
        
        for child_info in all_children:
            child_css = self.css_rules.get(f'.{child_info["class"]}', {})
            if child_css.get('position') == 'absolute':
                has_absolute_children = True
                break
        
        # 如果有absolute子元素，确保父容器有position
        if has_absolute_children:
            if 'position' not in css:
                css['position'] = 'relative'
                print(f"   ✓ 添加 relative: {elem_name}（容器包含absolute子元素）")

    def _identify_root_container_bg(self, all_children, existing_decor):
        """根容器特殊处理：识别覆盖层和背景。
        
        对于 #canvas 这类根容器，应该剥离：
        1. 空白元素（宽度或高度为0）
        2. top=0 的元素（通常是背景或顶部栏）
        
        目的：把"主要内容区域"分离出来，形成清晰的趋势链。
        """
        if not all_children:
            return existing_decor
        
        decor = set(existing_decor)
        
        # 剥离逻辑（简化版，针对移动页面结构）
        for child in all_children:
            # 规则1：空白元素（宽=0 或 高=0）
            if child.get('width', 0) == 0 or child.get('height', 0) == 0:
                decor.add(child['class'])
                continue
            
            # 规则2：顶部覆盖层（top=0）
            # 这通常是背景、顶部栏、头部等浮层
            if child.get('top', 0) == 0:
                decor.add(child['class'])
                continue
        
        return decor
    
    def _count_layout_changes(self, children):
        """改进的趋势检测：计算向下/向右的趋势链长度。
        
        不同于简单计数（可能计出 v=2,h=2），此方法模拟V13算法：
        - 计算两个元素在交叉轴的投影重叠比例
        - 只有重叠 >= 0.5 才认为同向
        """
        if len(children) < 2:
            return 0, 0
        
        sorted_children = sorted(children, key=lambda x: (x['top'], x['left']))
        
        TREND_AXIS_OVERLAP_RATIO = 0.5
        
        def axis_overlap_ratio(a, b, axis='x'):
            """两元素在指定轴上的投影重叠比例"""
            if axis == 'x':
                a_left, a_right = a['left'], a['left'] + a['width']
                b_left, b_right = b['left'], b['left'] + b['width']
                min_w = min(a['width'], b['width'])
            else:
                a_left, a_right = a['top'], a['top'] + a['height']
                b_left, b_right = b['top'], b['top'] + b['height']
                min_w = min(a['height'], b['height'])
            
            overlap = max(0, min(a_right, b_right) - max(a_left, b_left))
            return overlap / min_w if min_w > 0 else 0
        
        # 构建趋势链
        v_chain = [sorted_children[0]]
        h_chain = [sorted_children[0]]
        
        for i in range(1, len(sorted_children)):
            curr = sorted_children[i]
            
            # 尝试加入竖链
            last_v = v_chain[-1]
            if (curr['top'] >= last_v['top'] + last_v['height'] and
                axis_overlap_ratio(last_v, curr, axis='x') >= TREND_AXIS_OVERLAP_RATIO):
                v_chain.append(curr)
            
            # 尝试加入横链
            last_h = h_chain[-1]
            if (curr['left'] >= last_h['left'] + last_h['width'] and
                axis_overlap_ratio(last_h, curr, axis='y') >= TREND_AXIS_OVERLAP_RATIO):
                h_chain.append(curr)
        
        v_changes = len(v_chain) - 1
        h_changes = len(h_chain) - 1
        
        return v_changes, h_changes

