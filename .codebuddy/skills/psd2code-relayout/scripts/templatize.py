#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重复单元模板化的分析器与执行器。

两个模式：

    # 1) 体检：找出几何同构的簇，并标出每簇能不能安全合并
    python scripts/templatize.py <html_dir> --scan

    # 2) 执行：把某个 class 的所有实例压成「容器 + 单层文本」
    python scripts/templatize.py <html_dir> --class points --template reward-tag --apply

为什么先体检：合并子层的两个风险都不在几何上——

  * 文本可能嵌在待删的子层内部（删 div 会连带删掉字）；
  * 子层的 z-index 可能高于文本（改成父背景会翻转层叠）。

这两项都能在 `--scan` 阶段算出来，不用等像素校验失败。

改完必须跑 `verify_page.py`；本脚本不代替验收。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_GEOM_PROPS = ('left', 'top', 'width', 'height', 'margin-left', 'margin-top')


# ---------------------------------------------------------------------------
# CSS 工具
# ---------------------------------------------------------------------------

def parse_css(text: str) -> dict[str, dict[str, str]]:
    rules: dict[str, dict[str, str]] = {}
    for m in re.finditer(r'\.([\w-]+)\s*\{([^}]*)\}', text):
        decls: dict[str, str] = {}
        for part in m.group(2).split(';'):
            if ':' in part:
                k, v = part.split(':', 1)
                if k.strip():
                    decls[k.strip()] = v.strip()
        if decls:
            rules[m.group(1)] = decls
    return rules


def _num(decls: dict[str, str], prop: str, default: float = 0.0) -> float:
    raw = decls.get(prop)
    if not raw:
        return default
    found = re.search(r'-?[\d.]+', raw)
    return float(found.group(0)) if found else default


def _bg(decls: dict[str, str]) -> Optional[str]:
    raw = decls.get('background', '') + decls.get('background-image', '')
    found = re.search(r'url\("?([^")]+)"?\)', raw)
    return found.group(1) if found else None


def _geom(decls: dict[str, str]) -> tuple:
    return tuple(
        (p, decls[p]) for p in _GEOM_PROPS if p in decls
    )


def _replace_rule(css: str, cls: str, body: str) -> str:
    pat = re.compile(r'^\.' + re.escape(cls) + r'\s*\{[^}]*\}\s*$', re.M)
    return pat.sub(f'.{cls} {{ {body} }}', css, count=1)


def _drop_rule(css: str, cls: str) -> str:
    pat = re.compile(r'^\.' + re.escape(cls) + r'\s*\{[^}]*\}\s*$', re.M)
    return pat.sub('', css)


# ---------------------------------------------------------------------------
# 结构分析
# ---------------------------------------------------------------------------

@dataclass
class LayerInfo:
    cls: str
    decls: dict[str, str]
    z: float
    bg: Optional[str]
    element: Any = None
    nested_imgs: int = 0      # 子层内部还有几张图（压平会丢）
    nested_spans: int = 0     # 子层内部有几段文本


@dataclass
class NodeInfo:
    element: Any
    cls: str
    decls: dict[str, str]
    layers: list[LayerInfo] = field(default_factory=list)
    text: Optional[str] = None
    text_depth: int = 0          # span 到容器的层数（0 = 直接子级）
    text_container_cls: Optional[str] = None
    text_z: float = 0.0
    text_container_decls: Optional[dict[str, str]] = None

    @property
    def signature(self) -> tuple:
        return (_geom(self.decls), len(self.layers), self.text_depth)


def analyze(
    soup: Any,
    rules: dict[str, dict[str, str]],
    class_filter: Optional[str | list[str]] = None,
) -> list[NodeInfo]:
    wanted: Optional[set[str]] = None
    if class_filter:
        wanted = {class_filter} if isinstance(class_filter, str) else set(class_filter)

    nodes: list[NodeInfo] = []
    for el in soup.find_all('div'):
        classes = el.get('class') or []
        if wanted is not None and not (wanted & set(classes)):
            continue
        primary = next((c for c in classes if c in rules), None)
        if not primary:
            continue
        decls = rules[primary]
        if 'width' not in decls:
            continue

        layers: list[LayerInfo] = []
        for kid in el.find_all('div', recursive=False):
            kcls = next((c for c in (kid.get('class') or []) if c in rules), None)
            if not kcls:
                continue
            kdecls = rules[kcls]
            nested_imgs = sum(
                1 for d in kid.find_all('div')
                if _bg(rules.get(next((c for c in (d.get('class') or []) if c in rules), ''), {}))
            )
            layers.append(LayerInfo(
                cls=kcls, decls=kdecls,
                z=_num(kdecls, 'z-index'),
                bg=_bg(kdecls),
                element=kid,
                nested_imgs=nested_imgs,
                nested_spans=len(kid.find_all('span')),
            ))

        if not layers:
            continue  # 没有子层的叶子图层不参与模板化

        info = NodeInfo(element=el, cls=primary, decls=decls, layers=layers)

        span = el.find('span')
        if span is not None:
            info.text = (span.get_text() or '').strip()[:30]
            depth = 0
            node = span.parent
            chain: list[str] = []
            while node is not None and node is not el:
                depth += 1
                for c in (node.get('class') or []):
                    if c in rules:
                        chain.append(c)
                        break
                node = node.parent
            if node is el:
                info.text_depth = depth
                if depth > 0 and chain:
                    tcls = chain[0]
                    info.text_container_cls = tcls
                    info.text_container_decls = rules.get(tcls, {})
                    info.text_z = _num(info.text_container_decls, 'z-index')
                else:
                    # 直接子级：文本按静态绘制，z 视为 0
                    info.text_z = 0.0

        nodes.append(info)
    return nodes


def verdict(candidates: list[NodeInfo]) -> tuple[bool, str]:
    """判断一簇能否安全地把子层压成单层。"""
    if not candidates:
        return False, '没有实例'
    first = candidates[0]
    if len(first.layers) != 1:
        return False, f'子层数 {len(first.layers)}，只支持单子层合并'
    layer = first.layers[0]
    if not layer.bg:
        return False, f'.{layer.cls} 没有背景图'
    # 子层内部若还有「图片元素」或「多段文本」，压成单张背景会丢掉它们
    # （实测：领奖 .rect-7 内含 13 个 div，其中有图标；V1 .rect-37 只包一层文本容器，可安全合并）
    if layer.nested_spans > 1:
        return False, f'.{layer.cls} 内含 {layer.nested_spans} 段文本，压平会丢字'
    if layer.nested_imgs:
        return False, f'.{layer.cls} 内含 {layer.nested_imgs} 个图片元素，压平会丢图'

    if len({n.signature for n in candidates}) > 1:
        return False, '实例几何不一致'

    if layer.z > first.text_z:
        return False, f'子层 z={layer.z:g} 高于文本 z={first.text_z:g}，改背景会翻转层叠'

    if first.text is not None and first.text_depth > 0:
        # 不拒绝：--apply 会先把 span 摘出来。但要显式提示，这是最常见的丢字原因。
        return True, f'可合并；文本嵌在 {first.text_depth} 层内，apply 会先 extract'

    return True, '可安全合并'


# ---------------------------------------------------------------------------
# 命令：scan / apply
# ---------------------------------------------------------------------------

def do_scan(soup: Any, rules: dict[str, dict[str, str]], min_count: int) -> dict:
    nodes = analyze(soup, rules)
    buckets: dict[tuple, list[NodeInfo]] = {}
    for n in nodes:
        buckets.setdefault(n.signature, []).append(n)

    clusters = []
    for sig, members in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        if len(members) < min_count:
            continue
        safe, reason = verdict(members)
        clusters.append({
            'count': len(members),
            'classes': (lambda cs: cs[:5] + (['...'] if len(cs) > 5 else []))(
                sorted({m.cls for m in members})),
            'layers': [l.cls for l in members[0].layers],
            'geom': dict(sig[0]),
            'text_depth': members[0].text_depth,
            'z': {'layer': members[0].layers[0].z if members[0].layers else None,
                  'text': members[0].text_z},
            'safe': safe,
            'reason': reason,
        })
    return {'clusters': clusters}


def do_apply(
    soup: Any,
    css: str,
    rules: dict[str, dict[str, str]],
    target_cls: str | list[str],
    template: str,
    mode: str,
) -> tuple[str, str, dict]:
    target_classes = [target_cls] if isinstance(target_cls, str) else list(target_cls)
    nodes = analyze(soup, rules, class_filter=target_classes)
    if not nodes:
        raise SystemExit(f'没有找到 {target_classes} 的实例')

    safe, reason = verdict(nodes)
    if not safe:
        raise SystemExit(f'拒绝合并 {target_classes}：{reason}')

    first = nodes[0]
    layer = first.layers[0]
    decls = layer.decls

    left = _num(decls, 'left')
    top = _num(decls, 'top')
    width = _num(decls, 'width')
    height = _num(decls, 'height')
    z = _num(decls, 'z-index')

    use_pseudo = mode == 'before' or (mode == 'auto' and z > 0)
    extra: list[str] = []

    # 模板类继承容器的**全部**声明（margin / z-index / flex-shrink 都不能丢）
    container = dict(rules[nodes[0].cls])

    if use_pseudo:
        extra.append(
            f'.{template}::before {{ content: ""; position: absolute; left: {left:g}px; '
            f'top: {top:g}px; width: {width:g}px; height: {height:g}px; z-index: {z:g}; '
            f'background: url("{layer.bg}") no-repeat; }}'
        )
    else:
        container['background'] = f'url("{layer.bg}") no-repeat'

    extra.append(
        f'.{template} {{ ' + '; '.join(f'{k}: {v}' for k, v in container.items()) + ' }'
    )
    if first.text_container_decls:
        tc = dict(first.text_container_decls)
        body = '; '.join(f'{k}: {v}' for k, v in tc.items())
        extra.append(f'.{template} .{template}__text {{ {body} }}')

    removed_nodes = 0
    for node in nodes:
        span = node.element.find('span')
        if span is not None:
            span.extract()          # 必须先摘出，否则随父 div 一起被删
        for d in list(node.element.find_all('div')):
            d.decompose()
            removed_nodes += 1
        node.element['class'] = [template]
        if span is not None:
            span['class'] = [f'{template}__text']
            node.element.append(span)

    # 删除**所有实例**的子层/文本容器规则（各实例的类名不同，不能只删第一个）
    dead: set[str] = set(target_classes)
    for node in nodes:
        dead.update(l.cls for l in node.layers)
        if node.text_container_cls:
            dead.add(node.text_container_cls)
    for cls in dead:
        css = _drop_rule(css, cls)
    css = css.rstrip() + '\n\n/* templatize: ' + ','.join(target_classes) + ' -> ' + template + ' */\n' + '\n'.join(extra) + '\n'

    return css, str(soup), {
        'instances': len(nodes),
        'removed_nodes': removed_nodes,
        'mode': 'before' if use_pseudo else 'background',
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='重复单元模板化：体检 / 执行')
    parser.add_argument('html_dir')
    parser.add_argument('--html', default='index_ai.html')
    parser.add_argument('--css', default='style_ai.css')
    parser.add_argument('--scan', action='store_true', help='只体检，输出同构簇与安全判定')
    parser.add_argument('--min-count', type=int, default=3, help='--scan 的最小实例数')
    parser.add_argument('--class', dest='target_cls', help='要合并的目标 class')
    parser.add_argument('--classes', help='多个 class（逗号分隔），用于类名不同但几何同构的簇')
    parser.add_argument('--template', help='新模板类名')
    parser.add_argument('--mode', choices=('auto', 'background', 'before'), default='auto')
    parser.add_argument('--apply', action='store_true', help='真正写入文件')
    args = parser.parse_args(argv)

    from bs4 import BeautifulSoup

    html_dir = Path(args.html_dir).expanduser().resolve()
    html_path, css_path = html_dir / args.html, html_dir / args.css
    if not html_path.exists() or not css_path.exists():
        raise SystemExit(f'缺少 {html_path.name} / {css_path.name}')

    css = css_path.read_text(encoding='utf-8')
    soup = BeautifulSoup(html_path.read_text(encoding='utf-8'), 'html.parser')
    rules = parse_css(css)

    if args.scan:
        print(json.dumps(do_scan(soup, rules, args.min_count), ensure_ascii=False, indent=1))
        return 0

    targets = args.target_cls or args.classes
    if not targets or not args.template:
        raise SystemExit('需要 --class/--classes 与 --template')
    target = [c.strip() for c in targets.split(',') if c.strip()]
    if len(target) == 1:
        target = target[0]
    if not args.apply:
        raise SystemExit('需要 --apply（建议先 --scan 看判定）')

    new_css, new_html, stats = do_apply(soup, css, rules, target, args.template, args.mode)
    css_path.write_text(new_css, encoding='utf-8')
    html_path.write_text(new_html, encoding='utf-8')
    print(json.dumps(stats, ensure_ascii=False))
    print('下一步：verify_page.py 做像素裁决')
    return 0


if __name__ == '__main__':
    sys.exit(main())
