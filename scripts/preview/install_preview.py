#!/usr/bin/env python3
"""将预览模板复制到 PSD 输出目录（与 index.html 同目录）。

用法:
  python scripts/preview/install_preview.py [target_dir]

如果不提供 target_dir，则使用 scripts.config.config.Config.OUTPUT_BASE_DIR
"""
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE_DIR = os.path.join(ROOT, 'scripts', 'preview')

def default_output_dir():
    try:
        from scripts.config.config import Config
        return Config.OUTPUT_BASE_DIR
    except Exception:
        return os.path.join(ROOT, 'output')

def install(target_dir=None):
    if target_dir is None:
        target_dir = default_output_dir()
    if not os.path.isdir(target_dir):
        raise SystemExit(f"目标路径不存在: {target_dir}")
    files = ['preview.html','preview.css','preview.js']
    for f in files:
        src = os.path.join(TEMPLATE_DIR, f)
        dst = os.path.join(target_dir, f)
        shutil.copyfile(src, dst)
        print(f'已复制 {src} -> {dst}')

    # 强制刷新 preview.js 缓存：每次部署都更新 preview.html 中的 js 版本参数
    preview_path = os.path.join(target_dir, 'preview.html')
    if os.path.isfile(preview_path):
        try:
            with open(preview_path, 'r', encoding='utf-8') as f:
                preview_text = f.read()
            version = str(int(time.time()))
            preview_text = re.sub(
                r'preview\.js(?:\?v=[^"\']*)?',
                f'preview.js?v={version}',
                preview_text,
                count=1,
            )
            with open(preview_path, 'w', encoding='utf-8') as f:
                f.write(preview_text)
            print(f'已更新 preview.js 版本参数: v={version}')
        except Exception as e:
            print(f'更新 preview.js 版本参数失败：{e}')

    # 如果目标目录包含 index.html，嵌入其内容到 preview.html 以规避 file:// 同源限制
    index_path = os.path.join(target_dir, 'index.html')
    preview_path = os.path.join(target_dir, 'preview.html')
    if os.path.isfile(index_path) and os.path.isfile(preview_path):
        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                index_html = f.read()
            # 内联 CSS 和图片资源，替换相对引用为 data: URI 或内置样式
            def inline_resources(html, base_dir):
                import re, base64

                # inline linked CSS (match link tag with rel=stylesheet in any attribute order)
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

                    # replace url(...) in css
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

                # inline <img src=>
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

            index_html = inline_resources(index_html, target_dir)
            # 将 index.html 原文放入一个安全的文本脚本节点中
            marker = '\n<!-- __EMBEDDED_INDEX_PLACEHOLDER__ -->\n'
            embed_tag = f"<script id=\"__embedded_index\" type=\"text/plain\">{index_html}</script>"
            with open(preview_path, 'r', encoding='utf-8') as f:
                preview_text = f.read()
            if '__embedded_index' not in preview_text:
                # 尝试把 embed_tag 插入到 </body> 之前
                if '</body>' in preview_text:
                    preview_text = preview_text.replace('</body>', embed_tag + '\n</body>')
                else:
                    preview_text = preview_text + embed_tag
                with open(preview_path, 'w', encoding='utf-8') as f:
                    f.write(preview_text)
                print(f'已将 index.html 嵌入到 {preview_path}（用于离线预览）')
        except Exception as e:
            print(f'嵌入 index.html 到 preview.html 失败：{e}')

if __name__ == '__main__':
    td = sys.argv[1] if len(sys.argv)>1 else None
    install(td)
