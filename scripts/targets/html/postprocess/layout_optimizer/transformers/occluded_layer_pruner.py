"""被完全遮挡的图层剔除（2026-05-27 新增；2026-05-27 重构为独立 Stage）

设计动机
========

PSD 转出的 HTML/CSS 偶尔会保留**视觉上根本看不见**的图层 —— 典型是
设计师留在 PSD 里的「占位/草稿」图层（一张极小的纯色 PNG），上面又叠了
最终版的大图把它整个盖住，运行时永远看不到。

这类小占位图删了能省 DOM/CSS，是"低风险高 ROI"的清理。

执行时机：LayoutOptimizer 之前（独立 Stage）
=============================================

本 transformer **必须在 LayoutOptimizer 之前**跑，原因：
- LayoutOptimizer 的 DOMRestructure / FlexApplier 等会基于"子节点集合"
  推断 envelope/对齐/flex 流。如果剔除发生在 LayoutOptimizer 之内或之后，
  下游 transformer 看到的子节点集合就和"未优化版 index.html"不一致，
  导致兄弟节点位置偏移（实测 4% 像素差异）。
- 反之，如果剔除在 LayoutOptimizer **之前**完成，DOMRestructure 看到的
  从一开始就是"剔除后的可见图层集合"，envelope/对齐/flex 流推断与最终
  浏览器视觉天然一致。
- 因此**不再需要** ``_is_flex_child`` 闸门（剔除时还没有 flex），也**不再
  需要**极保守的 ``self_opaque_threshold = 0.0001``——剔除之后下游
  布局推断会用剔除后的子节点集合，没有"flex 流重算"问题。

为何严格限制使用范围
====================

最初版本把判据写成"X 自身有像素的所有采样点都被 z>X 的图层并集覆盖
→ 删除"。在抽奖活动页面 PSD 上误删了 ``img-7__16``（750×1696 海滩主
视觉大图，1.8MB），原因：上面叠加的 100+ 个小卡片/按钮/文字图层在 4px
降采样下**恰好把海滩所有采样点都打到了**，但真实视觉里卡片之间还有
大量缝隙能让海滩透出。误删后下层 PSD 残留的占位素材（"奖品名称""累计
领取..."等）暴露出来。

为避免再次误删主视觉大图，本 transformer 用**保守白名单 + 强单层
覆盖**两道闸门：

1. **小 PNG 闸门**（``max_png_kb``，默认 80KB）：只对小文件做几何剔除
   - 主视觉大图普遍 > 100KB（海滩/桌面/卡片底图等）
   - 占位草稿图普遍 < 50KB（纯色或简单图形）

2. **强单层覆盖闸门**：要求**单个 above 图层**就能 100% 覆盖 X
   （不能靠多个图层并集"凑出来"）。多图层并集在 4px 降采样下常常出现
   伪阳性。

3. **自身极少不透明像素**（``self_opaque_threshold``，默认 0.5%）：
   - **0.5% 是 LayoutOptimizer 之前剔除时的安全值**（之后剔除则需 0.01%
     才安全，因为flex 流重算放大误差）
   - 典型场景：PSD 调整层 / 被 mask 完全吃掉的图层 / 主体早被另一组
     替换但占位 PNG 留下的细微噪点

判定算法
========

对每个候选 X（image leaf）：

A. **自身全透明 / 极少像素**（最安全路径）：
   - X 自己 PNG ``alpha >= full_alpha`` 的采样点占比 < ``self_opaque_threshold``
   - → 直接删除

B. **小 PNG + 单层完整遮挡**（几何遮挡路径）：
   - X 的 PNG 文件 ≤ ``max_png_kb``（防止误删大图）
   - 存在某个 z > X.z 的 above 图层 Y，且 Y 在 X bbox 内的 alpha 投影
     **完整覆盖** X 自身有像素的所有采样点（单层！不是多层并集）
   - → 删除

C. **跨画布超大离屏图层**（PSD 模板残留废图层路径，2026-05-27 新增）：
   - 图层 bbox 至少有一边远超画布外 ``offscreen_overflow_threshold``
   - 图层超出画布之外的"自身面积占比" ≥ ``offscreen_area_ratio``
     （即"大半像素都在画布外"）
   - 图层任一边长 ≥ ``oversized_dim_ratio × max(canvas_w, canvas_h)``
     （即比画布最长边还大显著比例）
   - 不是页面级根背景（z 不是全画布最低）
   - → 直接删除（同时父组 envelope 自然收缩）

   典型场景：设计师从韩文/日文模板复制图层带来的"超大兜底背景"，
   它的视觉中心其实远离画布、PSD 渲染里被画布裁切几乎全无视觉
   贡献，但因为 ``visible=True`` 且 alpha 不全为 0、PNG 也常常很大
   （> 80KB），路径 A/B 都拦不住。专用的路径 C 用"几何离屏 + 绝对
   尺寸"两条强信号闸门稳健剔除。

D. **PNG 透明边紧致裁剪**（带宽/布局优化路径，2026-05-27 新增）：
   - 候选：image leaf + opacity≈1 + blend=normal + bg-position 是
     "0 0/left top"（无偏移采样）+ bg-size 缺省/auto/原始尺寸
     + bg-repeat=no-repeat
   - 计算 PNG 内 alpha > ``trim_alpha_threshold`` 的紧致 bbox
   - 浪费率 ≥ ``trim_min_waste_ratio`` 且原图 ≥ ``trim_min_orig_kb``
     且单边裁掉 ≥ ``trim_min_trim_pixels`` 才动手
   - 物理产出新 PNG（``<basename>-trim-<hash>.png``）替换 url
   - 改写 CSS：``left += dx; top += dy; width = bw; height = bh``
     （视觉位置完全不变；与父容器坐标系平移完全等价）
   - 同时把"裁剪后产生的父容器空白边"通过末尾的
     ``_shrink_parents_to_children_envelope`` 自动消化

   典型场景：PSD 整画布尺寸的角色立绘（750×2536 但角色实际只占
   750×1183，53% 透明）、大装饰元素（92% 透明）。直接给 LayoutOptimizer
   传"紧致 bbox"会让 envelope/grid/trend 推断更贴近视觉真实，
   并显著降低 PNG 总体积。

候选准入条件：
- ``data-type="image"`` + 有 ``background-image`` + 无内部 div
- ``opacity >= full_opacity``（不透明）
- ``mix-blend-mode`` 缺省/normal
- bbox 短边 ≥ ``min_bbox_side``

特殊情况：
- PNG 与 CSS 尺寸不匹配 → 跳过（避免缩放歧义）
- 路径 C 不依赖 PNG 尺寸匹配，仅靠几何 + 文件存在即可判定

接入方式
========

作为独立 Stage（``PrunePreOptimizeStage``）跑在 ``LayoutOptimizeStage``
之前，输入 / 输出都是 ``index.html`` + ``style.css``。本类内部仍可被
当成 transformer 用（``OccludedLayerPruner(soup, css_rules, ...)``），
独立入口见 ``prune_index_html()`` 模块函数。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_URL_RE = re.compile(
    r"""url\(\s*(?:"([^"]+)"|'([^']+)'|([^)]+?))\s*\)"""
)


@dataclass
class OccludedPrunerConfig:
    """被遮挡图层剔除配置"""
    enabled: bool = True
    # 降采样步长（像素）；越小越精确但越慢
    sample_stride: int = 4
    # X 自身 PNG 不透明像素占比 < 该值时，视为"视觉无贡献"直接删除（路径 A）。
    # ⚠️ 阈值与"剔除时机"紧耦合：
    #   * 跑在 LayoutOptimizer **之前**（独立 Stage，当前默认）→ 0.005 (0.5%)
    #     此时下游 DOMRestructure / FlexApplier 看到的就是"剔除后的子节点
    #     集合"，envelope/对齐推断与最终视觉一致，没有"flex 流重算"放大误差。
    #     可以放心删 0.5% 不透明的图层（典型：调整层、被 mask 完全吃掉的层、
    #     极少噪点的占位）。
    #   * 跑在 LayoutOptimizer **之后** → 必须 0.0001 (0.01%)
    #     否则 flex 子被删触发兄弟重算，引入大范围视觉偏移。
    # 0.5% 实测案例（抽奖活动 img-7__16 0.61%）：仍可能含主视觉文字。
    # 极保守的 0.5% 也仍然限制为"只删几乎全空白"，主视觉文字图通常 > 5%。
    # 若发现误删，可调到更小（0.001 = 0.1%）。
    self_opaque_threshold: float = 0.005
    # 路径 A 判定"X 自身有视觉像素"的 alpha 阈值（0~255）。
    # ⚠️ 务必和 full_alpha 解耦：full_alpha 是"完全不透明"用于几何遮挡（B/C）路径
    # 判 Y 是否能盖住 X；路径 A 只是问"X 还有没有视觉贡献"，半透明像素也算。
    # 实测反例（抽奖活动页面-01-520 batai-bfb580.png 102×19 暗红色羽化文字）：
    # 整张 PNG 最高 alpha = 240，按 full_alpha=250 判 → 0% 不透明 → 被路径 A 误删。
    # 默认 10：足够过滤"全空白 / 调整层 / 残留极少噪点"，又能保住所有可见羽化/文字。
    self_visible_alpha: int = 10
    # X bbox 短边 < 该值时跳过（避免误删小图标 / 装饰）
    min_bbox_side: int = 16
    # 视为"完全不透明"的 alpha 阈值（0~255），仅用于几何遮挡路径 B/C
    full_alpha: int = 250
    # 视为"opacity 等于 1"的阈值
    full_opacity: float = 0.99
    # 几何遮挡路径（B）的 PNG 文件大小上限：> 该值的图视为"主视觉大图"，绝不参与
    # 几何剔除。占位草稿图通常 < 50KB，主背景图通常 > 100KB。80KB 是中间值。
    max_png_kb: int = 80
    # 几何遮挡路径（B）要求：必须存在**单个**above 图层完整覆盖 X（不允许多层并集）。
    # 多层并集在 4px 降采样下常出现伪阳性（实际像素间有缝隙但采样点恰好都被打到）。
    require_single_layer_coverage: bool = True

    # ------------------------------------------------------------------
    # 路径 C：跨画布超大离屏图层（2026-05-27 新增）
    # ------------------------------------------------------------------
    # 整体开关（与 enabled 独立，便于分级关闭）
    offscreen_prune_enabled: bool = True
    # 图层 bbox 任一边超出画布外 ≥ 该像素数才算"显著离屏"。
    # 比 0 大一些是为了忽略"碰边但未越界"的合规图层。
    offscreen_overflow_threshold: int = 100
    # 图层超出画布之外的部分占自身面积 ≥ 该比例才剔除。
    # 0.5 = 一半以上像素位于画布外。
    # 设计 trade-off：
    #   * 0.3：激进（容易误删故意溢出的"全屏延展装饰图"如平铺背景）
    #   * 0.5：默认（韩版模板残留废图层场景安全命中）
    #   * 0.7：保守（仅极端"几乎完全离屏"的废图层才剔）
    offscreen_area_ratio: float = 0.5
    # 图层 bbox 长边 ≥ ``oversized_dim_ratio × max(canvas_w, canvas_h)`` 才剔除。
    # 1.2 = 比画布最长边还大 20%。
    # 用途：把"画布内全屏装饰"（边长 = 画布长边）和"废图层"（远超画布）区分开。
    oversized_dim_ratio: float = 1.2
    # 排除"页面级根背景"：z-index 不能是全画布最低（典型 bg__1 有时也会越界
    # 1~2px，但它是真背景，不能误删）。
    protect_lowest_z_background: bool = True

    # ------------------------------------------------------------------
    # 路径 D：PNG 透明边紧致裁剪（2026-05-27 新增）
    # ------------------------------------------------------------------
    # 整体开关
    trim_enabled: bool = True
    # 计算紧致 bbox 时的 alpha 阈值（0~255）。比 full_alpha=250 宽松：
    # 容忍亚像素羽化 / 抗锯齿边缘。低于该值视为透明，不计入紧致 bbox。
    trim_alpha_threshold: int = 5
    # 浪费率门槛：原始 bbox 与紧致 bbox 的面积比 < (1 - 该值) 才裁。
    # 0.15 = 透明区域占原图 ≥ 15% 才动手；< 15% 收益不抵裁剪开销。
    trim_min_waste_ratio: float = 0.15
    # 原图体积门槛：< 该值不裁（小图节省有限，避免无效噪声）
    trim_min_orig_kb: int = 20
    # 单边裁掉像素门槛：min(裁掉的左/上/右/下) 中的最大值 < 该值不裁
    # （单边裁少量像素对布局/带宽都没意义）
    trim_min_trim_pixels: int = 32
    # bg-repeat 必须是 no-repeat 才裁（repeat/round/space 裁了会改变视觉）
    trim_require_no_repeat: bool = True
    # bg-position 必须是 "0 0" / "left top" / 缺省才裁（非零偏移裁了定位会错）
    trim_require_zero_position: bool = True
    # bg-size 必须是缺省 / auto / auto auto / 原始尺寸（W H）才裁
    # （cover/contain/百分比/拉伸尺寸裁了会改变视觉）
    trim_require_natural_size: bool = True
    # PNG 与 CSS 尺寸一致检查：> 该容差跳过（避免缩放歧义）
    trim_size_tolerance_px: int = 1


@dataclass
class _LayerRecord:
    """累积绝对坐标后的图层记录"""
    element: Any              # bs4 Tag
    css_class: str            # 第一个语义类名（不含点）
    selector: str             # ".classname"
    abs_left: int             # 画布坐标系的绝对位置
    abs_top: int
    width: int
    height: int
    z_index: int
    bg_path: Optional[Path]   # 解析到的本地 PNG 路径
    is_image_leaf: bool       # 是 image 叶子（有 bg + 无内部 div）
    opacity_ok: bool          # opacity ≈ 1
    blend_ok: bool            # mix-blend-mode 缺省/normal


class OccludedLayerPruner:
    """被完全遮挡的图层剔除主入口

    Usage::

        pruner = OccludedLayerPruner(soup, css_rules, stats, html_dir=html_dir)
        pruner.run()

    Args:
        soup: BeautifulSoup root
        css_rules: ``{".classname": {"prop": "value"}}``
        stats: 优化器统计字典
        html_dir: HTML 文件所在目录（用于解析 ``url("images/xxx.png")`` 相对路径）
        config: 配置；None 用默认
    """

    def __init__(
        self,
        soup: Any,
        css_rules: Dict[str, Dict[str, str]],
        stats: Dict[str, Any],
        html_dir: Optional[Path] = None,
        config: Optional[OccludedPrunerConfig] = None,
    ):
        self.soup = soup
        self.css_rules = css_rules
        self.stats = stats
        self.html_dir = Path(html_dir) if html_dir else None
        self.config = config or OccludedPrunerConfig()
        self.stats.setdefault('occluded_layers_pruned', 0)
        self.stats.setdefault('occluded_bytes_saved', 0)
        self.stats.setdefault('trimmed_layers', 0)
        self.stats.setdefault('trimmed_bytes_saved', 0)

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def run(self) -> None:
        if not self.config.enabled:
            return
        if self.html_dir is None or not self.html_dir.is_dir():
            return

        # 延迟导入：让 PIL/numpy 缺失时整个 transformer 静默跳过
        try:
            import numpy as np  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            return

        canvas = self.soup.find(id='canvas')
        if canvas is None:
            return

        print("\n👻 被完全遮挡图层剔除（pre-optimize）...")

        # 画布尺寸（路径 C 需要）：从 #canvas CSS 规则读取
        canvas_rule = self.css_rules.get('#canvas', {}) or {}
        canvas_w = self._parse_px(canvas_rule.get('width')) or 0.0
        canvas_h = self._parse_px(canvas_rule.get('height')) or 0.0

        records = self._collect_layers(canvas)
        # 跑在 LayoutOptimizer 之前时，DOM 还没有 v-stack/v-row/v-col 等
        # flex 容器，所以**不需要** _is_flex_child 闸门。
        # （历史上这个闸门是为了防止剔除发生在 LayoutOptimizer 之内/之后时
        # 触发 flex 流重算引入视觉偏移。）
        candidates = [
            r for r in records
            if r.is_image_leaf and r.opacity_ok and r.blend_ok
            and r.width >= self.config.min_bbox_side
            and r.height >= self.config.min_bbox_side
            and r.bg_path is not None
        ]
        if not candidates:
            return

        # 路径 C 的"页面级根背景保护"：找出全画布最低 z（image leaf 中）
        # —— 这层一般是 bg__1 等真实背景，不能被路径 C 误删。
        lowest_z = (
            min((r.z_index for r in candidates), default=None)
            if self.config.protect_lowest_z_background else None
        )

        # 检测前先记录每个 PNG 引用次数（删除时只在引用归 0 时统计字节数节省）
        png_ref_count: Dict[Path, int] = {}
        for r in records:
            if r.bg_path is not None:
                png_ref_count[r.bg_path] = png_ref_count.get(r.bg_path, 0) + 1

        pruned: List[Tuple[_LayerRecord, str]] = []
        # 检测时按 z 升序：先删低层以加速（但本算法对顺序不敏感）
        for X in sorted(candidates, key=lambda r: r.z_index):
            # 路径 C：跨画布超大离屏图层（廉价几何检查，先跑）
            if self.config.offscreen_prune_enabled and canvas_w > 0 and canvas_h > 0:
                hit_c, reason_c = self._is_offscreen_oversized(
                    X, canvas_w, canvas_h, lowest_z,
                )
                if hit_c:
                    pruned.append((X, reason_c))
                    continue

            occluded, reason = self._is_fully_occluded(X, records)
            if occluded:
                pruned.append((X, reason))

        # 收集"被剔除子元素的父容器"：用于后续 envelope 收缩。
        # 必须在 decompose 之前抓 parent，因为 decompose 后 element.parent 变 None。
        affected_parents: Dict[int, Any] = {}
        bytes_saved = 0

        if pruned:
            for rec, _ in pruned:
                parent = rec.element.parent
                if parent is None:
                    continue
                # 只收集 div 父容器（跳过 #canvas、html/body 等）
                if getattr(parent, 'name', None) != 'div':
                    continue
                pid = id(parent)
                if pid not in affected_parents:
                    affected_parents[pid] = parent

            # 物理删除
            for rec, reason in pruned:
                try:
                    rec.element.decompose()
                except Exception:
                    pass
                # 删 CSS 规则
                self.css_rules.pop(rec.selector, None)
                # 引用计数 -1，归 0 时统计字节
                if rec.bg_path is not None:
                    png_ref_count[rec.bg_path] -= 1
                    if png_ref_count[rec.bg_path] <= 0:
                        try:
                            bytes_saved += rec.bg_path.stat().st_size
                        except OSError:
                            pass

            self.stats['occluded_layers_pruned'] += len(pruned)
            self.stats['occluded_bytes_saved'] += bytes_saved

            for rec, reason in pruned:
                png_name = rec.bg_path.name if rec.bg_path else '?'
                print(
                    f"   🗑️  剔除 {rec.css_class:30s} "
                    f"({rec.width}x{rec.height} z={rec.z_index} "
                    f"{png_name})  原因: {reason}"
                )
            print(
                f"   合计: 剔除 {len(pruned)} 个图层 "
                f"(节省 {bytes_saved / 1024:.1f} KB)"
            )

        # 路径 D：透明边紧致裁剪
        # 收集 Pass D 影响的父容器（与 Pass A/B/C 的父容器合并到一起做 envelope 收缩）
        if self.config.trim_enabled:
            # 重新收集 records（Pass A/B/C 可能已删除部分元素）
            trim_records = self._collect_layers(canvas)
            trim_candidates = [
                r for r in trim_records
                if r.is_image_leaf and r.opacity_ok and r.blend_ok
                and r.width >= self.config.min_bbox_side
                and r.height >= self.config.min_bbox_side
                and r.bg_path is not None
            ]
            trim_affected_parents = self._trim_transparent_borders(trim_candidates)
            # 合并到 affected_parents 让 envelope 收缩一次性处理
            for pid, parent in trim_affected_parents.items():
                if pid not in affected_parents:
                    affected_parents[pid] = parent

        # 父容器 envelope 收缩：剔除大废图层后，父 group 的 width/height
        # 可能仍是"含废图层时的 envelope"。本步骤把每个受影响父容器的
        # left/top/width/height 重算到剩余子元素 envelope，并平移子坐标
        # 系保持视觉位置不变。
        if affected_parents:
            self._shrink_parents_to_children_envelope(affected_parents.values())

    # ------------------------------------------------------------------
    # 路径 D：透明边紧致裁剪
    # ------------------------------------------------------------------

    def _trim_transparent_borders(
        self,
        candidates: List[_LayerRecord],
    ) -> Dict[int, Any]:
        """对每个候选 image leaf 检测紧致 alpha bbox，命中阈值则物理裁剪 PNG
        + 改写 CSS 的 ``left/top/width/height``。

        视觉等价性保证：
        - 仅处理 ``background-position: 0 0/left top``（无偏移采样）
        - 仅处理 ``background-repeat: no-repeat``
        - 仅处理 ``background-size: auto/auto auto/缺省/原始尺寸``
        - 由于 background-position 仍为 ``0 0``，CSS 改 ``left+=dx; top+=dy;
          width=bw; height=bh`` 后浏览器渲染的视觉位置 / 像素值与原图严格一致

        Returns:
            ``{id(parent_div): parent_tag}`` 受影响父容器（用于上层合并到
            ``_shrink_parents_to_children_envelope``）
        """
        import hashlib
        import numpy as np
        from PIL import Image

        cfg = self.config
        affected_parents: Dict[int, Any] = {}
        if not candidates:
            return affected_parents

        # PNG 引用计数：扫描**全部 css_rules**统计 url 出现次数（不仅候选）。
        # 这样裁剪后旧 PNG 引用归 0 才能放心删除（避免误删被多 url 背景或
        # 其他 rule 引用的图）。
        # 单条规则可能有多 url（``background-image: url(a), url(b)``）+
        # ``background`` shorthand 与 ``background-image`` 共存等场景，
        # 全部用 _URL_RE 兜底。
        png_ref_count: Dict[Path, int] = {}
        for rule in self.css_rules.values():
            for key in ('background-image', 'background'):
                val = rule.get(key)
                if not val:
                    continue
                for m in _URL_RE.finditer(val):
                    url = (m.group(1) or m.group(2) or m.group(3) or '').strip()
                    p = self._resolve_png(url) if url else None
                    if p is not None:
                        png_ref_count[p] = png_ref_count.get(p, 0) + 1

        trimmed_n = 0
        trimmed_bytes_saved = 0
        # 同一 (png_path, crop_bbox) 的去重：多个图层引用同一图相同裁剪区域时复用
        trim_cache: Dict[Tuple[str, int, int, int, int], Path] = {}
        # 裁剪过程中"等待引用归 0 时删除"的旧 PNG 路径 → 原始体积
        pending_delete: Dict[Path, int] = {}

        print("\n✂️  PNG 透明边紧致裁剪...")

        for X in candidates:
            rule = self.css_rules.get(X.selector)
            if rule is None:
                continue

            # 视觉等价性闸门
            if cfg.trim_require_no_repeat and not self._is_no_repeat(rule):
                continue
            if cfg.trim_require_zero_position and not self._is_zero_position(rule):
                continue

            # 体积门槛
            try:
                orig_size = X.bg_path.stat().st_size
            except OSError:
                continue
            if orig_size < cfg.trim_min_orig_kb * 1024:
                continue

            # 加载 PNG
            try:
                img = Image.open(X.bg_path).convert('RGBA')
            except Exception:
                continue
            arr = np.array(img)
            png_h, png_w = arr.shape[:2]

            # PNG 与 CSS 尺寸一致检查
            if (abs(png_w - X.width) > cfg.trim_size_tolerance_px
                    or abs(png_h - X.height) > cfg.trim_size_tolerance_px):
                continue

            # 视觉等价性闸门：bg-size 必须是缺省/auto/原始尺寸
            if cfg.trim_require_natural_size and not self._is_natural_size(
                rule, png_w, png_h
            ):
                continue

            # 计算紧致 alpha bbox
            alpha = arr[..., 3]
            ys, xs = np.where(alpha > cfg.trim_alpha_threshold)
            if len(ys) == 0:
                # 全透明：交给 Pass A 处理（这里跳过避免重复）
                continue
            x1 = int(xs.min())
            y1 = int(ys.min())
            x2 = int(xs.max()) + 1
            y2 = int(ys.max()) + 1
            bw = x2 - x1
            bh = y2 - y1

            # 浪费率门槛
            orig_area = png_w * png_h
            crop_area = bw * bh
            if orig_area <= 0:
                continue
            waste_ratio = 1.0 - crop_area / orig_area
            if waste_ratio < cfg.trim_min_waste_ratio:
                continue

            # 单边裁掉像素门槛：四条边裁掉的最大值 < 阈值则跳过
            trim_left = x1
            trim_top = y1
            trim_right = png_w - x2
            trim_bottom = png_h - y2
            max_trim_side = max(trim_left, trim_top, trim_right, trim_bottom)
            if max_trim_side < cfg.trim_min_trim_pixels:
                continue

            # 执行裁剪
            cache_key = (str(X.bg_path), x1, y1, x2, y2)
            if cache_key in trim_cache:
                new_path = trim_cache[cache_key]
                new_size = 0
                try:
                    new_size = new_path.stat().st_size
                except OSError:
                    pass
            else:
                # 生成新文件名：<basename>-trim-<hash>.png
                stem = X.bg_path.stem
                suffix_data = f"{stem}-{x1}-{y1}-{x2}-{y2}"
                tag = hashlib.md5(suffix_data.encode('utf-8')).hexdigest()[:6]
                new_name = f"{stem}-trim-{tag}{X.bg_path.suffix}"
                new_path = X.bg_path.parent / new_name
                if not new_path.exists():
                    try:
                        cropped = img.crop((x1, y1, x2, y2))
                        cropped.save(new_path, optimize=True)
                    except Exception as e:
                        print(f"   ⚠️  裁剪失败 {X.bg_path.name}: {e}")
                        continue
                trim_cache[cache_key] = new_path
                try:
                    new_size = new_path.stat().st_size
                except OSError:
                    new_size = 0

            # 改写 CSS：left/top 加上裁剪偏移；width/height 替换为紧致尺寸
            # （背景图位置仍为 0 0，所以这种平移是视觉等价的）
            new_left = int(round((self._parse_px(rule.get('left')) or 0.0) + x1))
            new_top = int(round((self._parse_px(rule.get('top')) or 0.0) + y1))
            rule['left'] = f'{new_left}px'
            rule['top'] = f'{new_top}px'
            rule['width'] = f'{bw}px'
            rule['height'] = f'{bh}px'
            # 替换 background-image / background 中的 url
            self._rewrite_bg_url(rule, X.bg_path.name, new_name=new_path.name)

            # 收集父容器（envelope 收缩）
            parent = X.element.parent
            if parent is not None and getattr(parent, 'name', None) == 'div':
                pid = id(parent)
                if pid not in affected_parents:
                    affected_parents[pid] = parent

            saved = max(0, orig_size - new_size)
            trimmed_n += 1
            # 引用计数 -1（一个 rule 引用从旧 PNG 转移到新 PNG）
            png_ref_count[X.bg_path] -= 1
            png_ref_count[new_path] = png_ref_count.get(new_path, 0) + 1
            # 累计待删除的旧 PNG（实际删除在最后扫一遍）
            if X.bg_path not in pending_delete:
                pending_delete[X.bg_path] = orig_size

            print(
                f"   ✂️  裁剪 {X.css_class:30s} "
                f"{png_w}x{png_h} → {bw}x{bh} "
                f"(浪费 {100*waste_ratio:.0f}%, "
                f"节省 {saved/1024:.1f} KB)  → {new_path.name}"
            )

        # 最终扫一遍 pending_delete：引用归 0 的旧 PNG 物理删除并计入节省字节
        deleted_n = 0
        for old_path, orig_size in pending_delete.items():
            if png_ref_count.get(old_path, 0) <= 0:
                try:
                    old_path.unlink()
                    trimmed_bytes_saved += orig_size
                    deleted_n += 1
                except OSError:
                    pass

        if trimmed_n > 0:
            self.stats['trimmed_layers'] += trimmed_n
            self.stats['trimmed_bytes_saved'] += trimmed_bytes_saved
            print(
                f"   合计: 裁剪 {trimmed_n} 个图层, "
                f"删除旧 PNG {deleted_n} 个 "
                f"(净节省 {trimmed_bytes_saved / 1024:.1f} KB)"
            )
        else:
            print("   无可裁剪图层")

        return affected_parents

    @staticmethod
    def _is_no_repeat(rule: Dict[str, str]) -> bool:
        """``background-repeat: no-repeat`` 或缺省（CSS 默认 ``repeat``，
        但 psd2code 产物全部显式写 ``no-repeat`` 或通过 background shorthand
        包含 ``no-repeat``）"""
        rep = (rule.get('background-repeat') or '').strip().lower()
        if rep == 'no-repeat':
            return True
        if rep:  # 显式 repeat / round / space → 不裁
            return False
        # 缺省：检查 background shorthand 内是否含 no-repeat
        bg = (rule.get('background') or '').strip().lower()
        if bg and 'no-repeat' in bg:
            return True
        # 缺省 + shorthand 内未声明 → 浏览器默认 repeat → 不安全裁
        return False

    @staticmethod
    def _is_zero_position(rule: Dict[str, str]) -> bool:
        """``background-position: 0 0 / left top / 0% 0%`` 或缺省（CSS 默认
        ``0% 0%`` 即左上角）"""
        pos = (rule.get('background-position') or '').strip().lower()
        if not pos:
            # 缺省：检查 shorthand 是否含位置（含则需具体解析；不含则视为默认 0 0）
            bg = (rule.get('background') or '').strip().lower()
            if not bg:
                return True
            # shorthand 中位置较难精确解析；保守处理：若 shorthand 末尾形如
            # "url(...) Npx Mpx no-repeat" 含非零位置就拦下
            # 简单启发：含 ``px`` 后跟数字（除 0 / 0px 外）视为非零
            # 由于 psd2code 产物在 CssDedup 之后通常显式声明 background-image
            # + background-position，这里 shorthand 复杂场景极少出现；保守返回 False
            return False
        # 归一化：去多余空格
        normalized = ' '.join(pos.split())
        zero_aliases = {
            '0 0', '0px 0px', '0% 0%', 'left top', 'top left',
            '0', '0px', '0%', 'left', 'top',
        }
        return normalized in zero_aliases

    @staticmethod
    def _is_natural_size(rule: Dict[str, str], png_w: int, png_h: int) -> bool:
        """``background-size: auto / auto auto / 缺省 / Wpx Hpx``（W,H 等于
        PNG 原始尺寸）"""
        size = (rule.get('background-size') or '').strip().lower()
        if not size:
            # 缺省：CSS 默认 auto auto = 用图片原始尺寸；与 width/height 容器
            # 尺寸一致即可（已通过前面的 png/css 尺寸一致检查）
            # 但要排除 shorthand 中含非默认 size 的情况
            bg = (rule.get('background') or '').strip().lower()
            if bg and ('cover' in bg or 'contain' in bg or '%' in bg):
                return False
            return True
        normalized = ' '.join(size.split())
        if normalized in ('auto', 'auto auto'):
            return True
        # Wpx Hpx 形式
        parts = normalized.split()
        if len(parts) == 2:
            try:
                w_val = float(parts[0].rstrip('px'))
                h_val = float(parts[1].rstrip('px'))
                if (abs(w_val - png_w) <= 1 and abs(h_val - png_h) <= 1):
                    return True
            except ValueError:
                pass
        # cover / contain / 百分比 / 拉伸尺寸 → 不裁
        return False

    @staticmethod
    def _rewrite_bg_url(
        rule: Dict[str, str],
        old_name: str,
        new_name: str,
    ) -> None:
        """把 ``rule`` 中 ``background-image`` / ``background`` 字段里的
        ``old_name`` 替换为 ``new_name``。仅替换文件名（不动路径前缀如
        ``images/``），避免破坏 url 形态。"""
        for key in ('background-image', 'background'):
            val = rule.get(key)
            if not val or old_name not in val:
                continue
            rule[key] = val.replace(old_name, new_name)

    # ------------------------------------------------------------------
    # 受影响父容器的 envelope 收缩（剔除大废图层后避免父 height 残留）
    # ------------------------------------------------------------------

    def _shrink_parents_to_children_envelope(self, parents: Any) -> None:
        """对受影响的 layer-group 父容器，重算 envelope 并收缩 CSS bbox。

        触发条件（per parent）：
        - 是 div + 有 class（首类即 CSS 选择器）
        - CSS 中是 ``position:absolute``（避免影响 flex 流式布局的兄弟）
        - 还有 ≥1 个子 div（否则收缩成 0 反而把容器搞坏）
        - 子元素 envelope 起点 (env_left/env_top) > 收缩阈值（默认 ≥ 1px 即生效）

        副作用：
        - 修改父 CSS 的 left/top/width/height（左/上加 envelope 起点偏移；
          width/height 改为 envelope 尺寸）
        - 修改每个剩余子的 left/top（减去 envelope 起点偏移），保持视觉
          位置不变

        统计：``self.stats['occluded_parent_shrunk_count']``、
              ``self.stats['occluded_parent_shrunk_pixels']``（节省的总
              像素 = 收缩掉的 width*height 之和，仅供观察）
        """
        shrunk_n = 0
        shrunk_px = 0

        # 兜底初始化（与 __init__ 双保险，方便单元测试 stats=空 dict 跑）
        self.stats.setdefault('occluded_parent_shrunk_count', 0)
        self.stats.setdefault('occluded_parent_shrunk_pixels', 0)

        for parent in parents:
            classes = parent.get('class') or []
            if not classes:
                continue
            selector = '.' + classes[0]
            rule = self.css_rules.get(selector)
            if rule is None:
                continue
            if (rule.get('position') or '').strip() != 'absolute':
                continue

            # 当前父容器自身 bbox
            p_left = self._parse_px(rule.get('left'))
            p_top = self._parse_px(rule.get('top'))
            p_width = self._parse_px(rule.get('width'))
            p_height = self._parse_px(rule.get('height'))
            if None in (p_left, p_top, p_width, p_height):
                continue
            if p_width <= 0 or p_height <= 0:
                continue

            # 收集剩余子的 envelope（子的 left/top 是相对父的）
            children = parent.find_all('div', recursive=False)
            if not children:
                continue

            env_left = None
            env_top = None
            env_right = None
            env_bottom = None
            child_rules: List[Tuple[str, Dict[str, str]]] = []

            for child in children:
                ccls = child.get('class') or []
                if not ccls:
                    continue
                csel = '.' + ccls[0]
                crule = self.css_rules.get(csel)
                if crule is None:
                    continue
                cl = self._parse_px(crule.get('left'))
                ct = self._parse_px(crule.get('top'))
                cw = self._parse_px(crule.get('width'))
                ch = self._parse_px(crule.get('height'))
                if None in (cl, ct, cw, ch):
                    continue
                if cw <= 0 or ch <= 0:
                    continue
                child_rules.append((csel, crule))
                cr = cl + cw
                cb = ct + ch
                if env_left is None or cl < env_left:
                    env_left = cl
                if env_top is None or ct < env_top:
                    env_top = ct
                if env_right is None or cr > env_right:
                    env_right = cr
                if env_bottom is None or cb > env_bottom:
                    env_bottom = cb

            if env_left is None:
                continue

            # envelope 与父原 bbox 的差异
            new_w = env_right - env_left
            new_h = env_bottom - env_top
            if new_w <= 0 or new_h <= 0:
                continue
            # 1px 抖动跳过（避免无意义的 ±1 修改）
            if (env_left < 1 and env_top < 1
                and abs(new_w - p_width) < 1 and abs(new_h - p_height) < 1):
                continue
            # 真有大幅 envelope 起点偏移 / size 收缩才动手
            shift_left = int(round(env_left))
            shift_top = int(round(env_top))
            new_w_int = int(round(new_w))
            new_h_int = int(round(new_h))
            old_w_int = int(round(p_width))
            old_h_int = int(round(p_height))

            # 应用：父 CSS 平移 + 缩尺
            rule['left'] = f'{int(round(p_left + env_left))}px'
            rule['top'] = f'{int(round(p_top + env_top))}px'
            rule['width'] = f'{new_w_int}px'
            rule['height'] = f'{new_h_int}px'

            # 应用：所有剩余子的 left/top 减去 envelope 起点（保持视觉位置）
            if shift_left != 0 or shift_top != 0:
                for csel, crule in child_rules:
                    cl = self._parse_px(crule.get('left')) or 0.0
                    ct = self._parse_px(crule.get('top')) or 0.0
                    crule['left'] = f'{int(round(cl - env_left))}px'
                    crule['top'] = f'{int(round(ct - env_top))}px'

            saved_px = old_w_int * old_h_int - new_w_int * new_h_int
            shrunk_n += 1
            shrunk_px += max(0, saved_px)

            # 仅当收缩比较显著（envelope 起点偏移 > 16px 或尺寸收缩 > 10%）才打印
            sig_offset = (shift_left > 16 or shift_top > 16)
            sig_resize = (
                old_w_int > 0 and old_h_int > 0
                and (1.0 - new_w_int * new_h_int / (old_w_int * old_h_int) >= 0.1)
            )
            if sig_offset or sig_resize:
                print(
                    f"   📐 收缩父容器 {selector:30s} "
                    f"{old_w_int}x{old_h_int}@({int(round(p_left))},{int(round(p_top))}) "
                    f"→ {new_w_int}x{new_h_int}@"
                    f"({int(round(p_left + env_left))},{int(round(p_top + env_top))})"
                )

        self.stats['occluded_parent_shrunk_count'] += shrunk_n
        self.stats['occluded_parent_shrunk_pixels'] += shrunk_px
        if shrunk_n > 0:
            print(
                f"   合计: 收缩 {shrunk_n} 个父容器 envelope "
                f"(共节省 {shrunk_px / 1000:.1f} kpx²)"
            )

    # ------------------------------------------------------------------
    # 1) 收集所有图层（递归累计绝对坐标）
    # ------------------------------------------------------------------

    def _collect_layers(self, canvas: Any) -> List[_LayerRecord]:
        records: List[_LayerRecord] = []

        def walk(elem: Any, abs_left: int, abs_top: int) -> None:
            for child in elem.find_all('div', recursive=False):
                cls_list = child.get('class') or []
                if not cls_list:
                    continue
                first_cls = cls_list[0]
                selector = '.' + first_cls
                rule = self.css_rules.get(selector, {})

                L = self._parse_px(rule.get('left')) or 0
                T = self._parse_px(rule.get('top')) or 0
                W = self._parse_px(rule.get('width')) or 0
                H = self._parse_px(rule.get('height')) or 0
                z = self._parse_int(rule.get('z-index'), 0)

                # 解析 bg url（兼容 background-image / background shorthand）
                bg_url = None
                for key in ('background-image', 'background'):
                    val = rule.get(key)
                    if not val:
                        continue
                    m = _URL_RE.search(val)
                    if m:
                        bg_url = (
                            m.group(1) or m.group(2) or m.group(3) or ''
                        ).strip()
                        break
                bg_path = self._resolve_png(bg_url) if bg_url else None

                # 绝对坐标
                cur_abs_l = int(round(abs_left + L))
                cur_abs_t = int(round(abs_top + T))

                has_inner_div = child.find('div') is not None
                is_leaf = (bg_path is not None) and (not has_inner_div)

                op_val = self._parse_float(rule.get('opacity'), 1.0)
                bm_val = (rule.get('mix-blend-mode') or 'normal').strip()

                records.append(_LayerRecord(
                    element=child,
                    css_class=first_cls,
                    selector=selector,
                    abs_left=cur_abs_l,
                    abs_top=cur_abs_t,
                    width=int(round(W)),
                    height=int(round(H)),
                    z_index=z,
                    bg_path=bg_path,
                    is_image_leaf=is_leaf,
                    opacity_ok=op_val >= self.config.full_opacity,
                    blend_ok=bm_val in ('', 'normal'),
                ))

                if has_inner_div:
                    walk(child, cur_abs_l, cur_abs_t)

        walk(canvas, 0, 0)
        return records

    # ------------------------------------------------------------------
    # 2) 单个图层的遮挡判定
    # ------------------------------------------------------------------

    def _is_offscreen_oversized(
        self,
        X: _LayerRecord,
        canvas_w: float,
        canvas_h: float,
        lowest_z: Optional[int],
    ) -> Tuple[bool, str]:
        """路径 C：跨画布超大离屏图层判定。

        条件 (全部 AND)：
        1. bbox 任一边超出画布 ≥ ``offscreen_overflow_threshold``
        2. 超出画布之外的"自身像素面积"占比 ≥ ``offscreen_area_ratio``
        3. bbox 长边 ≥ ``oversized_dim_ratio × max(canvas_w, canvas_h)``
        4. 不是 image leaf 中 z 最低的页面级根背景
        """
        cfg = self.config
        # 条件 4：保护根背景
        if lowest_z is not None and X.z_index == lowest_z:
            return False, ""

        L, T = X.abs_left, X.abs_top
        R, B = L + X.width, T + X.height

        # 条件 1：任一边超画布
        overshoot_left = max(0, -L)
        overshoot_top = max(0, -T)
        overshoot_right = max(0, int(R - canvas_w))
        overshoot_bottom = max(0, int(B - canvas_h))
        max_overshoot = max(
            overshoot_left, overshoot_top, overshoot_right, overshoot_bottom,
        )
        if max_overshoot < cfg.offscreen_overflow_threshold:
            return False, ""

        # 条件 3：长边超画布最长边的 oversized_dim_ratio
        canvas_max = max(canvas_w, canvas_h)
        layer_max = float(max(X.width, X.height))
        if canvas_max <= 0 or layer_max < canvas_max * cfg.oversized_dim_ratio:
            return False, ""

        # 条件 2：图层与画布相交矩形面积 → 计算"画布外自身面积占比"
        ix1 = max(0, L)
        iy1 = max(0, T)
        ix2 = min(int(canvas_w), R)
        iy2 = min(int(canvas_h), B)
        on_canvas_area = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        self_area = X.width * X.height
        if self_area <= 0:
            return False, ""
        offscreen_ratio = 1.0 - (on_canvas_area / self_area)
        if offscreen_ratio < cfg.offscreen_area_ratio:
            return False, ""

        return True, (
            f"跨画布废图层 ({X.width}x{X.height}, "
            f"超出画布 {max_overshoot}px, 离屏面积 {100*offscreen_ratio:.0f}%)"
        )

    def _is_fully_occluded(
        self,
        X: _LayerRecord,
        all_records: List[_LayerRecord],
    ) -> Tuple[bool, str]:
        import numpy as np
        from PIL import Image

        stride = self.config.sample_stride
        xL, xT = X.abs_left, X.abs_top
        xW, xH = X.width, X.height
        if xW < stride * 2 or xH < stride * 2:
            return False, ""
        if X.bg_path is None:
            return False, ""

        # 加载 X 自身的 PNG，统计自身 alpha
        try:
            x_img = Image.open(X.bg_path).convert('RGBA')
        except Exception:
            return False, ""
        x_arr = np.array(x_img)
        # PNG 与 CSS 尺寸严格一致才检测（避免缩放歧义）
        if abs(x_arr.shape[1] - xW) > 1 or abs(x_arr.shape[0] - xH) > 1:
            return False, ""
        x_alpha = x_arr[:, :, 3]
        x_alpha_ds = x_alpha[::stride, ::stride]
        # 注意：路径 A 与路径 B 对 X 的"视觉像素"判定都用 self_visible_alpha (默认 10)，
        # 不能用 full_alpha (250)：羽化文字/半透明装饰的 alpha 普遍在 10~240 区间，
        # 用 full_alpha 会让 X 整张图\"零像素\"，路径 A 直接误删，路径 B vacuous truth 误删。
        # full_alpha 仅用于判 Y 在某位置是否\"完全不透明\"以视觉等价于 X（路径 B/C 几何遮挡）。

        # ============================================================
        # 路径 A：自身几乎全透明 → 直接判为视觉无贡献（最安全）
        # ============================================================
        x_visible_ds = x_alpha_ds >= self.config.self_visible_alpha
        x_visible_ratio = x_visible_ds.mean() if x_visible_ds.size > 0 else 0.0
        if x_visible_ratio < self.config.self_opaque_threshold:
            return True, f"自身 alpha 仅 {100*x_visible_ratio:.2f}% 可见 (≥{self.config.self_visible_alpha})"

        # ============================================================
        # 路径 B：小 PNG + 单层完整遮挡（几何遮挡）
        # 仅对小 PNG 启用（默认 ≤ 80KB），防止误删主视觉大图。
        # 多层并集在 4px 降采样下常出现伪阳性，所以要求**单个**above
        # 图层就能 100% 覆盖 X。
        # ============================================================
        max_bytes = self.config.max_png_kb * 1024
        try:
            x_size = X.bg_path.stat().st_size
        except OSError:
            return False, ""
        if x_size > max_bytes:
            # 主视觉大图：绝不走几何遮挡路径
            return False, ""

        above = [
            r for r in all_records
            if r is not X
            and r.is_image_leaf
            and r.opacity_ok
            and r.blend_ok
            and r.bg_path is not None
            and r.z_index > X.z_index
        ]

        # 仅当某个 above 单层就能 100% 覆盖 X 才删
        for Y in above:
            yL, yT = Y.abs_left, Y.abs_top
            yW, yH = Y.width, Y.height
            # 几何包含：Y bbox 必须把 X bbox 完整包住（必要条件，先做廉价检查）
            if not (yL <= xL and yT <= xT
                    and yL + yW >= xL + xW
                    and yT + yH >= xT + xH):
                continue
            if Y.bg_path is None:
                continue
            try:
                y_img = Image.open(Y.bg_path).convert('RGBA')
            except Exception:
                continue
            y_arr = np.array(y_img)
            if abs(y_arr.shape[1] - yW) > 1 or abs(y_arr.shape[0] - yH) > 1:
                continue
            y_alpha = y_arr[:, :, 3]
            # 取 Y 中对应 X bbox 的子区域
            sub = y_alpha[
                int(xT - yT):int(xT - yT) + xH,
                int(xL - yL):int(xL - yL) + xW,
            ]
            if sub.shape[0] != xH or sub.shape[1] != xW:
                continue
            y_ds = sub[::stride, ::stride]
            if y_ds.shape != x_alpha_ds.shape:
                continue
            # 路径 B 的 X 视觉像素位置必须用 self_visible_alpha (默认 10) 算，
            # 否则 alpha 全 < 250 的羽化文字 / 半透明装饰会让 x_pixels_for_coverage
            # 全 False，uncovered.sum()==0 走 vacuous truth → 误删。
            # ⚠️ Y 是否盖住 X 仍用 full_alpha=250：要求 Y 在该位置完全不透明才算\"盖住\"，
            # 半透明 Y 不能视觉等价于 X 故仍需保留 X。
            x_pixels_for_coverage = x_alpha_ds >= self.config.self_visible_alpha
            n_x_pixels = int(x_pixels_for_coverage.sum())
            # 防 vacuous truth：X 自身视觉像素采样点数 < 阈值时不走路径 B
            # (路径 A 的 self_opaque_threshold=0.005 已经过滤掉绝大部分全透明，
            # 这里再硬性要求 ≥16 个采样点，作为路径 B 单层覆盖判定的最小证据量)
            if n_x_pixels < 16:
                continue
            covered = y_ds >= self.config.full_alpha
            uncovered = x_pixels_for_coverage & ~covered
            if int(uncovered.sum()) == 0:
                return True, (
                    f"被单层 .{Y.css_class}(z={Y.z_index}) 100% 覆盖 "
                    f"({n_x_pixels} 采样点)"
                )

        return False, ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_png(self, url: str) -> Optional[Path]:
        if not url or '://' in url or url.startswith('data:'):
            return None
        if not url.lower().endswith('.png'):
            return None
        if '..' in url.split('/'):
            return None
        if self.html_dir is None:
            return None
        p = self.html_dir / url
        return p if p.exists() else None

    @staticmethod
    def _parse_px(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        if s.endswith('px'):
            s = s[:-2]
        try:
            return float(s)
        except ValueError:
            return None

    @staticmethod
    def _parse_int(value: Optional[str], default: int = 0) -> int:
        if value is None:
            return default
        try:
            return int(str(value).strip())
        except ValueError:
            return default

    @staticmethod
    def _parse_float(value: Optional[str], default: float = 0.0) -> float:
        if value is None:
            return default
        try:
            return float(str(value).strip())
        except ValueError:
            return default


# ---------------------------------------------------------------------------
# 独立 Stage 入口：在 LayoutOptimizer 之前对 index.html / style.css 做剔除
# ---------------------------------------------------------------------------

def prune_index_html(
    html_content: str,
    css_rules: Dict[str, Dict[str, str]],
    html_dir: Path,
    config: Optional[OccludedPrunerConfig] = None,
) -> Tuple[str, Dict[str, Dict[str, str]], Dict[str, Any]]:
    """对未优化的 index.html / style.css 做"被遮挡图层剔除"。

    Args:
        html_content: index.html 文件内容
        css_rules: parse_css_to_dict 解析的 CSS 规则字典
        html_dir: index.html 所在目录（用于解析 url("images/xxx.png") 相对路径）
        config: OccludedPrunerConfig；None 用默认

    Returns:
        (剔除后 HTML 字符串, 剔除后 css_rules, stats)
        stats 含 ``occluded_layers_pruned`` / ``occluded_bytes_saved``
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_content, 'html.parser')
    stats: Dict[str, Any] = {}
    pruner = OccludedLayerPruner(
        soup=soup,
        css_rules=css_rules,
        stats=stats,
        html_dir=html_dir,
        config=config,
    )
    pruner.run()
    return str(soup), css_rules, stats
