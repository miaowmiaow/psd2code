#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文本提取模块
- 从 PSD 文本图层提取内容和样式
- 检测旋转/倾斜变换（有变换的文本应降级为图片）
- 支持多语言：文本保留为 HTML，可通过 JS 动态替换
"""

from typing import Any


class TextExtractor:
    """文本提取器（静态工具类）"""

    # ------------------------------------------------------------------
    # 检测方法
    # ------------------------------------------------------------------

    @staticmethod
    def has_transform(layer: Any) -> bool:
        """
        检测文本图层是否有旋转 / 倾斜变换。
        有旋转/倾斜的文本无法准确用 CSS 还原，应降级为图片导出。

        Returns:
            True 表示有旋转/倾斜
        """
        if hasattr(layer, 'transform'):
            t = layer.transform
            if t and len(t) >= 4:
                a, b, c, d = t[0], t[1], t[2], t[3]
                if abs(b) > 0.01 or abs(c) > 0.01:
                    return True
        return False

    # ------------------------------------------------------------------
    # 提取方法
    # ------------------------------------------------------------------

    @staticmethod
    def extract_text_info(
        layer: Any,
        layer_height: int = 0,
        layer_width: int = 0,
    ) -> dict[str, Any] | None:
        """
        提取文本图层的内容和样式。

        Args:
            layer: PSD 文本图层
            layer_height: 图层高度，用于修正 font_size（font_size >= height * 0.85 时取 height * 0.85）
            layer_width: 图层宽度，用于中文文本的横向兜底（避免中文单行被浏览器挤换行）

        Returns:
            { 'text': str, 'style': { font_size, color, text_align } }
            失败返回 None
        """
        try:
            text: str = layer.text
            if not text:
                return None

            style: dict[str, Any] = {}

            # 提取 transform 的 X 轴缩放（用于修正 font_size）
            # PSD 的 FontSize 是\"原始字号\"（transform 前），实际渲染时
            # PS 会用 transform 缩放。我们的 bbox 是渲染后的像素尺寸，
            # 因此 font_size 也必须乘以缩放因子才能匹配 bbox。
            font_scale: float = 1.0
            if hasattr(layer, 'transform') and layer.transform:
                tr = layer.transform
                if len(tr) >= 4:
                    # transform = (a, b, c, d, e, f)
                    # a = X 轴缩放, d = Y 轴缩放（仅当无旋转/倾斜时）
                    # 文本 X、Y 缩放通常一致，取 a 即可
                    a_scale = float(tr[0])
                    if a_scale > 0.01:
                        font_scale = a_scale

            if hasattr(layer, 'engine_dict'):
                engine: Any = layer.engine_dict

                style_dict: Any = (
                    engine.get('StyleRun', {})
                    .get('RunArray', [{}])[0]
                    .get('StyleSheet', {})
                    .get('StyleSheetData', {})
                )

                # 字体大小（应用 transform 缩放）
                if 'FontSize' in style_dict:
                    style['font_size'] = float(style_dict['FontSize']) * font_scale

                # 行距 (Leading)
                if 'Leading' in style_dict:
                    style['leading'] = float(style_dict['Leading']) * font_scale
                elif 'AutoLeading' in style_dict and style_dict['AutoLeading']:
                    # 自动行距默认 1.2 倍字号（font_size 已含 scale）
                    if 'font_size' in style:
                        style['leading'] = style['font_size'] * 1.2

                # 颜色
                # FillColor.Values 格式为 [灰度, R, G, B]（非 [R, G, B, A]）
                if 'FillColor' in style_dict:
                    fc = style_dict['FillColor']
                    if hasattr(fc, 'get'):
                        vals = fc.get('Values', [1, 0, 0, 0])
                        if len(vals) >= 4:
                            # vals[0] 是灰度值，vals[1..3] 是 R, G, B
                            r = int(vals[1] * 255)
                            g = int(vals[2] * 255)
                            b = int(vals[3] * 255)
                            style['color'] = f'rgba({r}, {g}, {b}, 1)'
                        elif len(vals) >= 3:
                            r = int(vals[0] * 255)
                            g = int(vals[1] * 255)
                            b = int(vals[2] * 255)
                            style['color'] = f'rgba({r}, {g}, {b}, 1)'

                # 文本对齐：ParagraphSheet 在 ParagraphRun 路径下，
                # 不是 StyleRun（旧代码路径错误，实际拿不到值）。
                para_run = engine.get('ParagraphRun', {})
                if hasattr(para_run, 'get'):
                    pr_array = para_run.get('RunArray', [])
                    if pr_array:
                        para_props = (
                            pr_array[0]
                            .get('ParagraphSheet', {})
                            .get('Properties', {})
                        )
                        if hasattr(para_props, 'get') and 'Justification' in para_props:
                            align_map = {0: 'left', 1: 'right', 2: 'center'}
                            style['text_align'] = align_map.get(
                                para_props['Justification'], 'left'
                            )

            # 默认值
            style.setdefault('font_size', 14)
            style.setdefault('color', 'rgba(0, 0, 0, 1)')
            style.setdefault('text_align', 'left')

            # font_size 视觉兜底截断（重要！不要随意放宽）：
            # 设计稿字体（如思源黑体/造字工房）与浏览器默认字体（PingFang/微软雅黑/Arial）
            # 字宽不同，相同字号下浏览器渲染会比 PSD 宽，导致文字溢出 bbox 或被挤到下一行
            # （典型：单行文本变两行、按钮内文字撑出按钮）。
            # 因此约定：如果 PSD 提取出的字号 >= 单行高度 * 0.85，就压到 0.85*单行高度，
            # 牺牲一点字号精度换取浏览器视觉接近 PSD，由开发人员手动微调。
            # 注意：transform.scale 修正后字号变大，更需要这条兜底。
            #
            # 多行文本修正（重要）：layer_height 是**整段**文本块高度，对单行文本
            # 等于单行高度，但对多行文本等于 N × 单行高度。如果直接用 layer_height
            # 触发兜底，多行文本会被压成"字号 ≈ 整段高"，每行字号相互重叠。
            # 因此必须先估算行数 line_count：
            #   1) 优先按文本中的硬换行符（\n / \r）数 + 1
            #   2) 若无硬换行，且有 leading（行高），按 round(layer_height / leading)
            #   3) 兜底为 1（视为单行）
            line_count = 1
            if text:
                # PSD 文本的硬换行通常是 \r（少数为 \n / \r\n）。统一按"换行符簇"算：
                # 先把 \r\n 折叠为 1 个，再加上孤立的 \n / \r。
                normalized_breaks = text.replace('\r\n', '\n').replace('\r', '\n').count('\n')
                if normalized_breaks > 0:
                    line_count = normalized_breaks + 1
                elif layer_height > 0 and style.get('font_size', 0) > 0:
                    # 无硬换行时，用 font_size 估算行数（比 leading 更稳定）：
                    # - 单行文本 bbox height ≈ 1 倍 font_size（如 DNF 昵称 h=19, fs=19.69）
                    # - 真正的多行文本（无换行符、软换行）bbox height ≥ 1.5 倍 font_size
                    # 过去用 leading 估算会误判："Leading 设计值 < font_size * 1.2" 时
                    # round(h/leading) 会把单行误判为 2 行（如 h=19, leading=10.7 → round=2）
                    fs = style['font_size']
                    ratio = layer_height / fs
                    if ratio >= 1.5:
                        line_count = max(1, round(ratio))

            if layer_height > 0:
                single_line_h = layer_height / line_count
                if style['font_size'] >= single_line_h * 0.85:
                    style['font_size'] = single_line_h * 0.85

            # 中文横向兜底（针对"短中文标题"，避免被浏览器挤换行）：
            # 浏览器中文每字宽度 ≈ 1 倍字号。PSD 中常见"3~6 个中文字 + 紧 bbox"
            # 的标题（如「预测四强」width=164/4字、「主赛区」width=122/3字），
            # 兜底纵向截断后字号仍可能 > width / 字数，导致单行折行。
            # 仅当文本是纯中文（CJK 占比 >= 0.9）且字数 <= 12 时启用，避免误伤多字数
            # 中文段落（自然换行无害）和英文/混合文本（英文每字宽差别巨大）。
            # 估算 max_font = width / cjk_count * 0.95（5% 字间距/边距容差）
            if layer_width > 0 and text:
                cjk_count = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
                total_count = sum(1 for ch in text if not ch.isspace())
                if (
                    total_count > 0
                    and total_count <= 12
                    and cjk_count / total_count >= 0.9
                ):
                    max_font_by_width = layer_width / cjk_count * 0.95
                    if style['font_size'] > max_font_by_width:
                        style['font_size'] = max_font_by_width

            return {'text': text, 'style': style}

        except Exception as e:
            print(f"    ⚠️  文本提取失败: {e}")
            return None
