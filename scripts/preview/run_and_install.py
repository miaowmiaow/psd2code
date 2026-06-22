#!/usr/bin/env python3
"""运行 psd_to_code 转换并在生成产物目录自动安装 preview 模板。

用法: 在仓库根目录运行：
  python3 scripts/preview/run_and_install.py [--] <args...>

所有附加参数都会转发给 `psd_to_code.py`（例如输入文件和 --target）。
脚本会：
  - 调用 `psd_to_code.py` 完成转换
  - 在 `Config.OUTPUT_BASE_DIR` 下查找最近修改的 `index.html`
  - 将 `preview.html|preview.css|preview.js` 复制到对应目录（与 index.html 同目录）
"""
from __future__ import annotations
import sys
import os
import subprocess
import time
import shutil
from typing import List

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE_DIR = os.path.join(ROOT, 'scripts', 'preview')
TEMPLATE_FILES = ['preview.html', 'preview.css', 'preview.js']

def run_converter(argv: List[str]) -> int:
    cmd = [sys.executable, os.path.join(ROOT, 'psd_to_code.py')] + argv
    print('运行转换：', ' '.join(cmd))
    ret = subprocess.run(cmd)
    return ret.returncode

def find_recent_index_html(output_base: str, since_ts: float) -> List[str]:
    matches = []
    for dirpath, dirs, files in os.walk(output_base):
        if 'index.html' in files:
            path = os.path.join(dirpath, 'index.html')
            try:
                m = os.path.getmtime(path)
            except OSError:
                continue
            if m >= since_ts - 2:
                matches.append(path)
    return matches

def find_most_recent_index_html(output_base: str) -> str | None:
    best = None
    best_m = 0
    for dirpath, dirs, files in os.walk(output_base):
        if 'index.html' in files:
            path = os.path.join(dirpath, 'index.html')
            try:
                m = os.path.getmtime(path)
            except OSError:
                continue
            if m > best_m:
                best_m = m
                best = path
    return best

def install_to_dirs(dirs: List[str]):
    for d in dirs:
        for f in TEMPLATE_FILES:
            src = os.path.join(TEMPLATE_DIR, f)
            dst = os.path.join(d, f)
            try:
                shutil.copyfile(src, dst)
                print(f'已复制 {f} -> {d}')
            except Exception as e:
                print(f'复制失败 {src} -> {dst}: {e}')
        # 嵌入 index.html 到 preview.html 以便本地 file:// 预览可访问内容
        index_path = os.path.join(d, 'index.html')
        preview_path = os.path.join(d, 'preview.html')
        if os.path.isfile(index_path) and os.path.isfile(preview_path):
            try:
                with open(index_path, 'r', encoding='utf-8') as f:
                    index_html = f.read()
                # 内联 CSS 与图片以避免 file:// 加载限制
                def inline_resources(html, base_dir):
                    import re, base64

                    def repl_link(m):
                        tag = m.group(0)
                        href_m = re.search(r'href=["\']([^"\']+)["\']', tag)
                        if not href_m:
                            return tag
                        href = href_m.group(1)
                        if href.startswith(('http://','https://','//')):
                            return tag
                        css_path = os.path.join(base_dir, href)
                        try:
                            with open(css_path, 'r', encoding='utf-8') as cf:
                                css = cf.read()
                        except Exception:
                            return tag

                        def repl_url(mu):
                            url = mu.group(1).strip(' "\'')
                            if url.startswith(('data:','http://','https://','//')):
                                return f'url({url})'
                            candidate = os.path.join(base_dir, url)
                            if not os.path.isfile(candidate):
                                return f'url({url})'
                            try:
                                with open(candidate, 'rb') as imf:
                                    b = imf.read()
                                ext = os.path.splitext(candidate)[1].lstrip('.') or 'png'
                                mime = 'image/' + (ext if ext!='svg' else 'svg+xml')
                                data = 'data:%s;base64,%s' % (mime, base64.b64encode(b).decode('ascii'))
                                return f'url({data})'
                            except Exception:
                                return f'url({url})'

                        css = re.sub(r'url\(([^)]+)\)', repl_url, css)
                        return f'<style>/* inlined {href} */\n{css}\n</style>'

                    html = re.sub(r'<link[^>]*rel=["\']stylesheet["\'][^>]*>', repl_link, html, flags=re.I)

                    def repl_img(m):
                        src = m.group(1)
                        if src.startswith(('http://','https://','data:','//')):
                            return m.group(0)
                        img_path = os.path.join(base_dir, src)
                        if not os.path.isfile(img_path):
                            return m.group(0)
                        try:
                            with open(img_path, 'rb') as imf:
                                b = imf.read()
                            ext = os.path.splitext(img_path)[1].lstrip('.') or 'png'
                            mime = 'image/' + (ext if ext!='svg' else 'svg+xml')
                            data = 'data:%s;base64,%s' % (mime, base64.b64encode(b).decode('ascii'))
                            return m.group(0).replace(src, data)
                        except Exception:
                            return m.group(0)

                    html = re.sub(r'<img[^>]+src=["\']([^"\']+)["\']', repl_img, html, flags=re.I)
                    return html
                index_html = inline_resources(index_html, d)
                embed_tag = f"<script id=\"__embedded_index\" type=\"text/plain\">{index_html}</script>"
                with open(preview_path, 'r', encoding='utf-8') as f:
                    preview_text = f.read()
                if '__embedded_index' not in preview_text:
                    if '</body>' in preview_text:
                        preview_text = preview_text.replace('</body>', embed_tag + '\n</body>')
                    else:
                        preview_text = preview_text + embed_tag
                    with open(preview_path, 'w', encoding='utf-8') as f:
                        f.write(preview_text)
                    print(f'已将 index.html 嵌入到 {preview_path}（用于离线预览）')
            except Exception as exc:
                print('嵌入 index.html 失败：', exc)

def default_output_base() -> str:
    try:
        from scripts.config.config import Config
        return Config.OUTPUT_BASE_DIR
    except Exception:
        return os.path.join(ROOT, 'output')

def main():
    # 转发所有传入参数给 psd_to_code
    argv = sys.argv[1:]
    start_ts = time.time()
    code = run_converter(argv)
    if code != 0:
        print('转换器返回非零退出码，停止安装。')
        sys.exit(code)

    output_base = default_output_base()
    if not os.path.isdir(output_base):
        print('输出基目录不存在: ', output_base)
        sys.exit(0)

    recent = find_recent_index_html(output_base, start_ts)
    targets = []
    if recent:
        targets = [os.path.dirname(p) for p in recent]
    else:
        most = find_most_recent_index_html(output_base)
        if most:
            targets = [os.path.dirname(most)]

    if not targets:
        print('未找到任何 index.html；将预览文件复制到输出基目录：', output_base)
        targets = [output_base]

    targets = list(dict.fromkeys(targets))
    install_to_dirs(targets)

if __name__ == '__main__':
    main()
