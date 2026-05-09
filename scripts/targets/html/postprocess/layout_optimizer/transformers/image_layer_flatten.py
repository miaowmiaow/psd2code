"""图层扁平化（统一通道，2026-04-30 重构）

设计动机
========

历史上「合并图片以减少 DOM/CSS/PNG 请求」分散在四个独立函数里
（_try_merge_siblings / _try_collapse_into_parent /
 _try_absorb_into_relative_parent / _try_merge_parent_bg_with_single_child），
每个分支各有触发条件、白名单、护栏，新增一种场景就要改多处，维护噩梦。

本模块用**单一递归函数**替代上述四个分支：
对每个候选容器，把"容器自身的 background-image（如有）+ 全部直接 image
子的 background-image"视为一个图层栈，按 z 序合成为单张 PNG，写回容器
自己的 background-image，删除所有子 div + 子 CSS 规则。**容器本身一律保留**
（不消除 DOM 层级），从而：

  - 不破坏外层布局（容器在父 flex/grid 中的 width/height/margin 不变）
  - 不破坏虚拟 wrapper 的语义（v-stack/v-row/v-col 仍然作为 flex 子项）
  - 任意"父背景 + 单子叠加"场景天然支持
  - 任意"无背景父 + N 子叠加"场景天然支持
  - 新增规则只需改一处：``_can_flatten_container`` / ``_PARENT_BLOCKING_PROPS``

判定（全部 AND）
================
容器侧：
  1. 容器是 ``layer-group`` 或 ``data-virtual ∈ {stack,row,col,grid-row}``
  2. 容器没有"无法烧进 PNG 的装饰字段"（_PARENT_BLOCKING_PROPS：
     border-radius / overflow:hidden / box-shadow / clip-path / filter / transform 等）
  3. 容器自身的 background-image：缺失 OR 单一本地 PNG（参与合成）

子侧（每个直接子 div 都要满足）：
  - data-type == "image" 且无内部 div（叶子）
  - position:absolute + 完整 left/top/width/height (px)
  - background-image 是单一本地 PNG
  - opacity ≈ 1.0、mix-blend-mode 缺省/normal

层数 / 几何护栏：
  - 总层数（容器自身 bg + 子层数）≥ 2
  - envelope 面积 ≤ canvas × ``max_area_ratio``（默认 0.5）
  - 子之间 L∞ 距离 ≤ ``max_neighbor_gap_px`` 邻接图连通

DOM/CSS 替换
============
  - 调 ``compose_layers`` 落盘 ``images/flat-<md5>.png``
  - 容器 CSS 写入 background-image / -position / -size / -repeat
  - 容器原 background-* 字段被覆盖；其他字段（width/height/margin/flex-*/...）保留
  - 删除所有子 div + 它们的 CSS 规则

接入点
======
``LayoutOptimizer`` 在 ``dom_restructure.restructure_dom()`` 之后、
``sibling_group_detector.run()`` 之前调用 ``ImageLayerFlatten.run()``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class FlattenConfig:
    """图层扁平化配置（默认值开箱即用）"""
    enabled: bool = True
    # 总层数（容器自身 bg + 子层数）≥ 该值才考虑合成
    min_total_layers: int = 2
    # 合成 envelope 面积占画布的最大比例（防止合成全屏图导致一张超大 PNG）
    max_area_ratio: float = 0.5
    # 子之间 L∞ 距离 ≤ 该值才视为邻接（邻接图必须连通）
    max_neighbor_gap_px: int = 10
    # 透传给 compose_layers 的画布上限
    max_canvas_px: int = 8192


# ---------------------------------------------------------------------------
# 不可吸收的容器 CSS 字段
# 任意字段出现在容器 CSS 中（且值为非中性值）时，禁止扁平化。
# 理由：这些字段必须保留在最终 div 上以正确渲染；如果合并子 PNG，
#   - border-radius / overflow:hidden / clip-path：会裁掉子的 PNG 范围
#   - box-shadow：阴影位置依赖容器 bbox
#   - filter / transform：会改变最终视觉，烧进 PNG 后再叠 filter 等于双重作用
# 后续要放宽某条规则，只需把它从这里移除（并在 _replace_container_with_merged
# 中把字段保留下来）。
# ---------------------------------------------------------------------------
_PARENT_BLOCKING_PROPS: Set[str] = {
    'border-radius',
    'border',
    'border-top', 'border-bottom', 'border-left', 'border-right',
    'box-shadow',
    'clip-path',
    'filter',
    'backdrop-filter',
    'transform',
    'mask', 'mask-image',
}

# ``overflow`` 只在值为 hidden / clip / scroll / auto 时阻断
_PARENT_BLOCKING_OVERFLOW_VALUES: Set[str] = {'hidden', 'clip', 'scroll', 'auto'}

# 解析 ``url("images/xxx.png")``
_URL_RE = re.compile(
    r"""^\s*url\(\s*(?:"([^"]+)"|'([^']+)'|([^)]+?))\s*\)\s*$"""
)


# ---------------------------------------------------------------------------
# 内部数据结构
# ---------------------------------------------------------------------------

@dataclass
class _ImageChild:
    """一个合规的 image 叶子子节点的结构化信息"""
    element: Any  # bs4 Tag
    css_class: str   # ".classname"
    class_name: str  # "classname"
    left: int
    top: int
    width: int
    height: int
    png_path: Path
    z_index: int


@dataclass
class _ContainerBg:
    """容器自身的 background 信息（合成时作为最底层）"""
    png_path: Path
    pos_x: int   # background-position X（默认 0）
    pos_y: int   # background-position Y（默认 0）
    size_w: int  # 实际绘制宽
    size_h: int  # 实际绘制高


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class ImageLayerFlatten:
    """图层扁平化主入口

    用法::

        flat = ImageLayerFlatten(soup, css_rules, stats, images_dir=images_dir)
        flat.run()

    Args:
        soup: BeautifulSoup root
        css_rules: ``{".classname": {"prop": "value"}}``
        stats: 优化器统计字典
        images_dir: 物理 ``images/`` 目录；为 None 或不存在则跳过整个 pass
        config: 配置阈值；None 则用默认
    """

    def __init__(
        self,
        soup: Any,
        css_rules: Dict[str, Dict[str, str]],
        stats: Dict[str, Any],
        images_dir: Optional[Path] = None,
        config: Optional[FlattenConfig] = None,
    ):
        self.soup = soup
        self.css_rules = css_rules
        self.stats = stats
        self.images_dir = images_dir
        self.config = config or FlattenConfig()
        # 统一统计字段（旧分支细分指标全部废弃）
        self.stats.setdefault('image_layer_containers_flattened', 0)
        self.stats.setdefault('image_layer_layers_collapsed', 0)
        self.stats.setdefault('image_layer_bytes_saved', 0)

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def run(self) -> None:
        if not self.config.enabled:
            return
        if self.images_dir is None:
            return
        images_dir = Path(self.images_dir)
        if not images_dir.is_dir():
            return

        print("\n📦 步骤1.2：图层扁平化（统一通道）...")

        canvas_area = self._read_canvas_area()
        if canvas_area is None or canvas_area <= 0:
            print("   ⚠️  未读到 #canvas 尺寸，跳过")
            return

        # **后序遍历**：先尝试扁平化最深的容器，再向上尝试。
        # 这样深层合并产物（变成单 div + 容器自身有 bg）会被外层再次发现，
        # 实现「子图合并 → 父再吸收为背景」的链式简化。
        flattened_count = 0
        layers_collapsed = 0
        bytes_saved = 0

        # 多轮扫描直到稳定（一次内层合并可能让外层从"含子组"变成"叶子容器"）
        max_rounds = 5
        for round_no in range(max_rounds):
            round_hits = 0
            for container in self._collect_candidate_containers():
                try:
                    result = self._try_flatten_container(container, canvas_area)
                except Exception as exc:  # noqa: BLE001
                    name = container.get('data-name', 'unknown')
                    print(f"   ⚠️  {name} 扁平化失败: {exc}")
                    import traceback
                    traceback.print_exc()
                    continue
                if result is None:
                    continue
                round_hits += 1
                flattened_count += 1
                layers_collapsed += result['n']
                bytes_saved += result['bytes_saved']
            if round_hits == 0:
                break

        self.stats['image_layer_containers_flattened'] += flattened_count
        self.stats['image_layer_layers_collapsed'] += layers_collapsed
        self.stats['image_layer_bytes_saved'] += bytes_saved

        if flattened_count > 0:
            print(
                f"   ✓ 扁平化 {flattened_count} 个容器（共合并 {layers_collapsed} 层，"
                f"节省 {bytes_saved / 1024:.1f} KB）"
            )

    # ------------------------------------------------------------------
    # 容器收集
    # ------------------------------------------------------------------

    def _collect_candidate_containers(self) -> List[Any]:
        """收集 layer-group + 虚拟 wrapper（去重）

        后序遍历：BeautifulSoup 默认是先序，这里反转列表 + 用 id 去重，
        把出现位置较深的 div 先返回，从而实现"先合并叶子容器、再合并父容器"。
        """
        result: List[Any] = []
        result.extend(self.soup.find_all(
            'div', class_=lambda x: x and 'layer-group' in str(x)
        ))
        result.extend(self.soup.find_all('div', attrs={'data-virtual': True}))
        seen: Set[int] = set()
        unique: List[Any] = []
        for elem in result:
            key = id(elem)
            if key in seen:
                continue
            seen.add(key)
            unique.append(elem)
        # 反向（后序近似）：先深层、后浅层；同层间顺序无关紧要
        return list(reversed(unique))

    # ------------------------------------------------------------------
    # 单容器扁平化
    # ------------------------------------------------------------------

    def _try_flatten_container(
        self, container: Any, canvas_area: float,
    ) -> Optional[Dict[str, Any]]:
        """尝试扁平化单个容器；返回 None 表示不命中"""
        # 容器必须是 layer-group 或虚拟 wrapper
        classes = container.get('class') or []
        is_layer_group = 'layer-group' in classes
        is_virtual_wrapper = bool(container.get('data-virtual'))
        if not (is_layer_group or is_virtual_wrapper):
            return None
        if not classes:
            return None

        container_css_class = f'.{classes[0]}'
        container_styles = self.css_rules.get(container_css_class)
        if container_styles is None:
            return None

        # 容器没有"不可吸收"装饰字段
        if not self._can_flatten_container(container_styles):
            return None

        # 解析容器自身的 background（可能为 None = 无背景）
        container_bg = self._parse_container_background(container_styles)
        if container_bg == 'invalid':  # 有 bg 但不合规（多 url / 复杂 size）
            return None

        # 解析所有直接 div 子 → 必须全部是合规 image 叶子
        children = [
            c for c in container.find_all(recursive=False)
            if getattr(c, 'name', None) == 'div'
        ]
        if not children:
            return None

        parsed: List[_ImageChild] = []
        for child in children:
            info = self._parse_image_child(child)
            if info is None:
                return None  # 任一子不合规，整组放弃
            parsed.append(info)

        # 总层数 >= min_total_layers
        bg_count = 1 if isinstance(container_bg, _ContainerBg) else 0
        total_layers = bg_count + len(parsed)
        if total_layers < self.config.min_total_layers:
            return None

        # 几何护栏：envelope 面积
        env_w, env_h, _origin_x, _origin_y = self._envelope(parsed, container_bg)
        if env_w <= 0 or env_h <= 0:
            return None
        if (env_w * env_h) > canvas_area * self.config.max_area_ratio:
            return None

        # 几何护栏：子之间邻接图连通（仅约束子，容器 bg 通常覆盖整个范围）
        if len(parsed) >= 2 and not self._are_neighbors_connected(parsed):
            return None

        # ── 合成 PNG ──
        from ...background_compose import (  # type: ignore
            ComposeLayer,
            compose_layers,
            estimate_bytes_saved,
        )

        layers: List[ComposeLayer] = []
        # 容器 bg 永远在最底层（视觉最下）
        if isinstance(container_bg, _ContainerBg):
            layers.append(ComposeLayer(
                png_path=container_bg.png_path,
                pos_x=container_bg.pos_x,
                pos_y=container_bg.pos_y,
                size_w=container_bg.size_w,
                size_h=container_bg.size_h,
            ))
        # 子按 z-index 升序（z 小在底）
        ordered = sorted(parsed, key=lambda c: c.z_index)
        for c in ordered:
            layers.append(ComposeLayer(
                png_path=c.png_path,
                pos_x=c.left,
                pos_y=c.top,
                size_w=c.width,
                size_h=c.height,
            ))

        result = compose_layers(
            layers,
            Path(self.images_dir) if self.images_dir is not None else Path('.'),
            max_canvas_px=self.config.max_canvas_px,
        )
        if result is None:
            return None

        src_paths = [L.png_path for L in layers]
        bytes_saved = estimate_bytes_saved(src_paths, result)

        # ── DOM/CSS 替换 ──
        self._replace_container_with_merged(
            container=container,
            container_styles=container_styles,
            parsed=parsed,
            compose_result=result,
            had_self_bg=isinstance(container_bg, _ContainerBg),
        )

        return {
            'n': total_layers,
            'bytes_saved': bytes_saved,
        }

    # ------------------------------------------------------------------
    # 容器是否可扁平化（装饰字段检查）
    # ------------------------------------------------------------------

    def _can_flatten_container(
        self, container_styles: Dict[str, str],
    ) -> bool:
        """容器没有「不可烧进 PNG」的装饰字段
        
        新增规则只需改 ``_PARENT_BLOCKING_PROPS`` / 这里。
        """
        for prop, value in container_styles.items():
            value_norm = str(value or '').strip().lower()
            if prop in _PARENT_BLOCKING_PROPS:
                if value_norm and value_norm not in ('none', '0', '0px'):
                    return False
            elif prop == 'overflow':
                if value_norm in _PARENT_BLOCKING_OVERFLOW_VALUES:
                    return False
            elif prop == 'opacity':
                # opacity != 1 会改变最终视觉
                try:
                    if abs(float(value_norm or '1') - 1.0) > 1e-3:
                        return False
                except ValueError:
                    return False
            elif prop == 'mix-blend-mode':
                if value_norm and value_norm != 'normal':
                    return False
        return True

    # ------------------------------------------------------------------
    # 容器自身 background 解析
    # ------------------------------------------------------------------

    def _parse_container_background(
        self, container_styles: Dict[str, str],
    ) -> Optional[Any]:
        """解析容器自身的 background；返回：
            - ``None`` = 容器无 background-image（不参与合成）
            - ``_ContainerBg`` = 合规单层本地 PNG，参与合成
            - 字符串 ``'invalid'`` = 容器有 background-image 但不合规
              （多 url / cover/contain / 复杂尺寸），整体放弃扁平化
        """
        bg = container_styles.get('background-image', '')
        if not bg:
            return None
        if ',' in bg:
            return 'invalid'
        png = self._parse_url_to_local_png(bg)
        if png is None:
            return 'invalid'

        # 容器宽高（用于解析 background-size 默认值）
        cont_w = self._parse_px(container_styles.get('width'))
        cont_h = self._parse_px(container_styles.get('height'))
        if cont_w is None or cont_h is None:
            return 'invalid'

        # 解析 background-position
        pos = (container_styles.get('background-position') or '').strip().lower()
        pos_x = 0
        pos_y = 0
        if pos and pos not in (
            '0 0', '0px 0px', 'left top', '0% 0%', '0 0px', '0px 0',
        ):
            parsed_pos = self._parse_two_px(pos)
            if parsed_pos is None:
                return 'invalid'
            pos_x, pos_y = parsed_pos

        # 解析 background-size
        size = (container_styles.get('background-size') or '').strip().lower()
        if not size or size in ('auto', 'auto auto'):
            # 默认 = 图原始尺寸
            png_w, png_h = self._read_png_size(png)
            if png_w is None or png_h is None:
                return 'invalid'
            size_w, size_h = png_w, png_h
        elif size in ('cover', 'contain', '100% 100%'):
            return 'invalid'
        else:
            parsed_size = self._parse_two_px(size)
            if parsed_size is None:
                return 'invalid'
            size_w, size_h = parsed_size

        # 解析 background-repeat（必须 no-repeat 或缺省）
        repeat = (container_styles.get('background-repeat') or 'no-repeat').strip().lower()
        if repeat not in ('no-repeat', ''):
            return 'invalid'

        return _ContainerBg(
            png_path=png,
            pos_x=pos_x,
            pos_y=pos_y,
            size_w=int(round(size_w)),
            size_h=int(round(size_h)),
        )

    # ------------------------------------------------------------------
    # 单个 image 子节点解析
    # ------------------------------------------------------------------

    def _parse_image_child(self, child) -> Optional[_ImageChild]:
        """把一个 child div 解析为 _ImageChild；不合规返回 None"""
        if child.get('data-type') != 'image':
            return None
        # 必须是叶子（无内部 div）
        if any(getattr(g, 'name', None) == 'div'
               for g in child.find_all(recursive=False)):
            return None

        classes = child.get('class') or []
        if not classes:
            return None
        css_class = f'.{classes[0]}'
        styles = self.css_rules.get(css_class)
        if not styles:
            return None

        if styles.get('position', '').strip().lower() != 'absolute':
            return None
        left = self._parse_px(styles.get('left'))
        top = self._parse_px(styles.get('top'))
        width = self._parse_px(styles.get('width'))
        height = self._parse_px(styles.get('height'))
        if left is None or top is None or width is None or height is None:
            return None
        if width <= 0 or height <= 0:
            return None

        bg = styles.get('background-image', '')
        if not bg:
            return None
        if ',' in bg:
            return None
        png_path = self._parse_url_to_local_png(bg)
        if png_path is None:
            return None

        # 不可吸收装饰字段（同 _can_flatten_container 但更严，
        # 因为子的装饰会被烧进 PNG 后丢失）
        for prop, value in styles.items():
            value_norm = str(value or '').strip().lower()
            if prop in _PARENT_BLOCKING_PROPS:
                if value_norm and value_norm not in ('none', '0', '0px'):
                    return None
            elif prop == 'overflow':
                if value_norm in _PARENT_BLOCKING_OVERFLOW_VALUES:
                    return None

        opacity = styles.get('opacity', '1').strip()
        try:
            if abs(float(opacity) - 1.0) > 1e-3:
                return None
        except ValueError:
            return None
        blend = styles.get('mix-blend-mode', 'normal').strip().lower()
        if blend != 'normal':
            return None

        z_index = self._parse_int(styles.get('z-index')) or 0

        return _ImageChild(
            element=child,
            css_class=css_class,
            class_name=classes[0],
            left=int(round(left)),
            top=int(round(top)),
            width=int(round(width)),
            height=int(round(height)),
            png_path=png_path,
            z_index=z_index,
        )

    # ------------------------------------------------------------------
    # 几何 / 邻接判定
    # ------------------------------------------------------------------

    @staticmethod
    def _envelope(
        children: List[_ImageChild],
        container_bg: Optional[Any],
    ) -> Tuple[int, int, int, int]:
        """计算合成画布范围（含容器自身 bg）"""
        xs: List[int] = []
        ys: List[int] = []
        rxs: List[int] = []
        rys: List[int] = []
        for c in children:
            xs.append(c.left)
            ys.append(c.top)
            rxs.append(c.left + c.width)
            rys.append(c.top + c.height)
        if isinstance(container_bg, _ContainerBg):
            xs.append(container_bg.pos_x)
            ys.append(container_bg.pos_y)
            rxs.append(container_bg.pos_x + container_bg.size_w)
            rys.append(container_bg.pos_y + container_bg.size_h)
        if not xs:
            return (0, 0, 0, 0)
        min_x, min_y = min(xs), min(ys)
        max_x, max_y = max(rxs), max(rys)
        return (max_x - min_x, max_y - min_y, min_x, min_y)

    def _are_neighbors_connected(
        self, children: List[_ImageChild],
    ) -> bool:
        """子之间 L∞ 距离 ≤ 阈值的邻接图必须连通"""
        n = len(children)
        if n <= 1:
            return True
        threshold = self.config.max_neighbor_gap_px
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(n):
            for j in range(i + 1, n):
                if self._bbox_distance(children[i], children[j]) <= threshold:
                    union(i, j)
        root = find(0)
        return all(find(k) == root for k in range(n))

    @staticmethod
    def _bbox_distance(a: _ImageChild, b: _ImageChild) -> int:
        a_right = a.left + a.width
        a_bottom = a.top + a.height
        b_right = b.left + b.width
        b_bottom = b.top + b.height
        dx = max(0, max(a.left, b.left) - min(a_right, b_right))
        dy = max(0, max(a.top, b.top) - min(a_bottom, b_bottom))
        return max(dx, dy)

    # ------------------------------------------------------------------
    # DOM/CSS 替换：合成图写到容器 background，删除所有子
    # ------------------------------------------------------------------

    def _replace_container_with_merged(
        self,
        container: Any,
        container_styles: Dict[str, str],
        parsed: List[_ImageChild],
        compose_result,
        had_self_bg: bool,
    ) -> None:
        """把合成图写到容器自身 background，删除所有子 div + 子 CSS。

        坐标系约定：
            - parsed 里的 left/top 都是子相对容器的坐标（CSS extract 阶段产物）
            - 容器自身 background 的 pos/size 也都是相对容器自己的坐标
            - compose_layers 用全部 layer.pos 算了 envelope 并返回 origin_x/y
              （= envelope 左上角在容器坐标系下的偏移）
            - 默认 ``background-position = origin_x/y``（合成图左上对齐到此）

        溢出处理（合成图超出容器 bbox）
        ================================
        当某个子元素的坐标 < 0 或 > 容器尺寸时（典型：外发光/装饰点缀在
        PSD 组边界外，但本就应可视），直接写 ``background-position: -10px -10px``
        会让合成图对应边的像素被浏览器**裁掉**（负坐标 = 图起点移到容器左上
        之外）。正确做法是把溢出外化到：
            - 左/上溢出 → 叠加到容器 margin-left/-top（保持净视觉位置不变）
            - 右/下溢出 → 扩大容器 width/height（让合成图完整落地）
            - background-position 归到非负坐标（残余偏移）

        仅当容器没有受外部布局约束的尺寸（flex column 下 cross-axis 默认不
        stretch；margin 叠加不影响 flex 主轴分布）时才触发；否则退回原"直接
        写负 background-position"路径（护栏在 ``_can_expand_container``）。
        """
        # 容器尺寸（用于决定是否需要 background-size + 检测溢出）
        cont_w = self._parse_px(container_styles.get('width'))
        cont_h = self._parse_px(container_styles.get('height'))

        # 写入新 background-*；其它字段全部保留
        container_styles['background-image'] = f'url("{compose_result.rel_url}")'
        container_styles['background-repeat'] = 'no-repeat'

        bg_pos_x = compose_result.origin_x
        bg_pos_y = compose_result.origin_y
        canvas_w = compose_result.canvas_w
        canvas_h = compose_result.canvas_h

        # ── 溢出检测 & 外化 ──
        neg_x = min(0, bg_pos_x)                 # ≤ 0（左溢出量，负值）
        neg_y = min(0, bg_pos_y)                 # ≤ 0（上溢出量，负值）
        overflow_r = 0                           # 右溢出量（正值 px）
        overflow_b = 0                           # 下溢出量（正值 px）
        if cont_w is not None:
            overflow_r = max(0, (bg_pos_x + canvas_w) - int(round(cont_w)))
        if cont_h is not None:
            overflow_b = max(0, (bg_pos_y + canvas_h) - int(round(cont_h)))
        needs_expand = (neg_x < 0 or neg_y < 0 or overflow_r > 0 or overflow_b > 0)

        if needs_expand and self._can_expand_container(container_styles):
            # 1) 左/上溢出外化到 margin（叠加既有 margin 值）
            if neg_x < 0:
                existing_ml = self._parse_px(container_styles.get('margin-left')) or 0
                container_styles['margin-left'] = f'{int(round(existing_ml + neg_x))}px'
            if neg_y < 0:
                existing_mt = self._parse_px(container_styles.get('margin-top')) or 0
                container_styles['margin-top'] = f'{int(round(existing_mt + neg_y))}px'

            # 2) 扩大容器 width/height，吸收左+右、上+下总溢出
            if cont_w is not None:
                new_w = int(round(cont_w)) + (-neg_x) + overflow_r
                container_styles['width'] = f'{new_w}px'
                cont_w = float(new_w)
            if cont_h is not None:
                new_h = int(round(cont_h)) + (-neg_y) + overflow_b
                container_styles['height'] = f'{new_h}px'
                cont_h = float(new_h)

            # 2.1) 如果容器是 flex 子项，锁定 flex-shrink: 0 防止扩大的尺寸
            #      被父 flex 容器压缩。并且把父 flex 容器在主轴方向的固定
            #      size 一并扩大，避免"子项 basis-sum > 父 size"时父 shrink
            #      其它兄弟（从而让本容器的 margin-top/left 在主轴上对不上
            #      PSD 原位置）。
            #
            #      典型链路：父 flex column height=232；子 v-stack-1(174)+
            #      title-sub(108) basis-sum=282 → 已经在 shrink 8px；若
            #      title-sub 扩到 118（+10），basis-sum=292，本容器锁
            #      shrink 后 v-stack-1 独自承受 10+8=18px，其 height 从
            #      174 缩到 156 → title-sub 顶边偏上 18px。必须把父 height
            #      也 +10 让 shrink 需求归零。
            parent_flex_axis = self._flex_parent_axis(container)
            if parent_flex_axis is not None:
                container_styles['flex-shrink'] = '0'
                delta_w = (-neg_x) + overflow_r
                delta_h = (-neg_y) + overflow_b
                self._expand_flex_parent(
                    container, parent_flex_axis, delta_w, delta_h,
                )

            # 3) background-position 归到非负（合成图完整显示）
            bg_pos_x = 0
            bg_pos_y = 0

        # ── 写 background-position ──
        if bg_pos_x == 0 and bg_pos_y == 0:
            container_styles.pop('background-position', None)
        else:
            container_styles['background-position'] = (
                f'{bg_pos_x}px {bg_pos_y}px'
            )

        # 当合成图尺寸 ≠ 容器尺寸时显式锁尺寸；否则可省略
        same_w = (cont_w is not None
                  and canvas_w == int(round(cont_w)))
        same_h = (cont_h is not None
                  and canvas_h == int(round(cont_h)))
        if same_w and same_h:
            container_styles.pop('background-size', None)
        else:
            container_styles['background-size'] = (
                f'{canvas_w}px {canvas_h}px'
            )

        # 删除所有子 div + CSS
        for c in parsed:
            self.css_rules.pop(c.css_class, None)
            c.element.decompose()

    # ------------------------------------------------------------------
    # 溢出扩展护栏
    # ------------------------------------------------------------------

    def _can_expand_container(
        self, container_styles: Dict[str, str],
    ) -> bool:
        """容器是否可被安全扩大 width/height + 叠加负 margin。

        **不允许**的情况（任一命中则退回原路径）：
            - 容器自身是 flex 容器（改 width 会影响子项 stretch 计算）
              ——虽然子已被全部清空，但保守拒绝
            - 容器已有 right/bottom 定位（与 width 联动产生歧义）
            - flex-basis 非 auto（flex 父按 basis 分配空间，改 width 无效）

        **允许**的情况：
            - absolute 定位容器（margin 叠加等同于平移，不影响兄弟）
            - 非 absolute 的 flex 子项（margin 叠加仅影响自身和后续兄弟间隙）
            - layer-group 装饰容器（通常只有 left/top/width/height/margin）
        """
        # flex 容器本身被扩大会影响「子项如何在其内部排布」——子项已被我们
        # 全部删除，但以防外部（例如 css_pretty 期望）依赖这个维度，保守拒绝
        display = str(container_styles.get('display', '')).strip().lower()
        if display in ('flex', 'inline-flex', 'grid', 'inline-grid'):
            return False

        # right/bottom 定位时 width 与定位联动，改 width 会改变实际渲染位置
        if container_styles.get('right') is not None:
            return False
        if container_styles.get('bottom') is not None:
            return False

        # flex-basis 非 auto 时 width 不再决定主轴尺寸
        basis = str(container_styles.get('flex-basis', 'auto')).strip().lower()
        if basis and basis not in ('auto', '0', '0px'):
            return False

        return True

    def _flex_parent_axis(self, container: Any) -> Optional[str]:
        """判断容器的父是否是 flex/grid，若是返回主轴方向。

        返回值：
            - 'row'    : 父 display=flex 且 flex-direction=row / row-reverse /
                         缺省（CSS 默认值）
            - 'column' : 父 display=flex 且 flex-direction=column / column-reverse
            - 'grid'   : 父是 grid 容器（处理方式简化：全部当"主轴不确定"
                         的约束来源，扩大只在 flex 路径精细补偿；grid 路径
                         仅负责 flex-shrink 等同义的不压缩语义——实际 grid
                         子项不被 shrink，这里仍然返回以便调用方锁 flex-shrink）
            - None     : 非 flex/grid 子项
        """
        parent = getattr(container, 'parent', None)
        if parent is None:
            return None
        parent_classes = parent.get('class') or []
        for cls in parent_classes:
            parent_css = self.css_rules.get(f'.{cls}')
            if parent_css is None:
                parent_css = self.css_rules.get(cls)
            if parent_css is None:
                continue
            display = str(parent_css.get('display', '')).strip().lower()
            if display in ('grid', 'inline-grid'):
                return 'grid'
            if display in ('flex', 'inline-flex'):
                direction = str(
                    parent_css.get('flex-direction', 'row')
                ).strip().lower()
                if direction in ('column', 'column-reverse'):
                    return 'column'
                return 'row'
        return None

    def _expand_flex_parent(
        self,
        container: Any,
        axis: str,
        delta_w: int,
        delta_h: int,
    ) -> None:
        """把父 flex 容器在主轴方向的 size 扩大，避免 basis-sum > 父 size
        触发 flex-shrink 污染兄弟。

        仅扩主轴方向（column → height；row → width）。cross-axis 上父通常
        已足够或 align-items 处理。grid 场景不动（grid 子项本就不 shrink）。
        """
        if axis == 'grid':
            return
        parent = getattr(container, 'parent', None)
        if parent is None:
            return
        parent_classes = parent.get('class') or []
        # 取"首个有 CSS 规则"的类作为修改目标（与 _flex_parent_axis 一致）
        target_key: Optional[str] = None
        parent_css: Optional[Dict[str, str]] = None
        for cls in parent_classes:
            key = f'.{cls}'
            if key in self.css_rules:
                target_key = key
                parent_css = self.css_rules[key]
                break
            if cls in self.css_rules:
                target_key = cls
                parent_css = self.css_rules[cls]
                break
        if parent_css is None or target_key is None:
            return

        if axis == 'column':
            delta = delta_h
            prop = 'height'
        else:  # 'row'
            delta = delta_w
            prop = 'width'
        if delta <= 0:
            return

        current = self._parse_px(parent_css.get(prop))
        if current is None:
            return  # 父主轴尺寸 auto → flex 自动撑开，不用干预
        new_val = int(round(current)) + int(delta)
        parent_css[prop] = f'{new_val}px'

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    def _read_canvas_area(self) -> Optional[float]:
        canvas = self.css_rules.get('#canvas')
        if not canvas:
            return None
        w = self._parse_px(canvas.get('width'))
        h = self._parse_px(canvas.get('height'))
        if w is None or h is None:
            return None
        return w * h

    def _parse_url_to_local_png(self, value: str) -> Optional[Path]:
        m = _URL_RE.match(value or '')
        if not m:
            return None
        rel = (m.group(1) or m.group(2) or m.group(3) or '').strip()
        if not rel.lower().endswith('.png'):
            return None
        if '://' in rel or rel.startswith('data:'):
            return None
        if '..' in rel.split('/'):
            return None
        if self.images_dir is None:
            return None
        html_dir = Path(self.images_dir).parent
        p = html_dir / rel
        return p if p.exists() else None

    @staticmethod
    def _parse_px(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        if s.endswith('px'):
            s = s[:-2].strip()
        try:
            return float(s)
        except ValueError:
            return None

    @staticmethod
    def _parse_int(value: Optional[str]) -> Optional[int]:
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        try:
            return int(round(float(s)))
        except ValueError:
            return None

    @staticmethod
    def _parse_two_px(value: str) -> Optional[Tuple[int, int]]:
        """``Npx Mpx`` / ``N M`` → (int, int)；不合规返回 None"""
        parts = value.replace(',', ' ').split()
        if len(parts) != 2:
            return None
        out: List[int] = []
        for p in parts:
            p = p.strip()
            if p.endswith('px'):
                p = p[:-2].strip()
            try:
                out.append(int(round(float(p))))
            except ValueError:
                return None
        return (out[0], out[1])

    @staticmethod
    def _read_png_size(png_path: Path) -> Tuple[Optional[int], Optional[int]]:
        try:
            from PIL import Image  # type: ignore
        except ImportError:
            return (None, None)
        try:
            with Image.open(png_path) as im:
                return (int(im.size[0]), int(im.size[1]))
        except Exception:
            return (None, None)
