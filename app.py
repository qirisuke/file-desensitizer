"""文件脱敏工具 —— 服务入口。

后端二选一：
    - 有 Flask：走 Flask（app.py 本文件）
    - 没有 Flask：自动回退到纯标准库服务器（server_stdlib.py）

内网断网环境通常装不上 pip 包，回退到标准库后只要有 Python 3.8+ 就能跑。
两种后端提供完全相同的接口，前端无需区分。
"""
import sys
import threading
import webbrowser
from pathlib import Path

import core

VERSION = '1.2.0'

# 业务逻辑已抽到 core.py，这里重新导出，保持既有调用方
# （测试脚本、外部集成）可以继续 `from app import build_config` 这样用。
build_config = core.build_config
scan_directory = core.scan_directory
apply_changes = core.apply_changes
is_text_file = core.is_text_file
safe_filename = core.safe_filename
find_free_port = core.find_free_port
FIELD_LIBRARY = core.FIELD_LIBRARY
RESOURCE_DIR = core.RESOURCE_DIR
TEXT_EXTENSIONS = core.TEXT_EXTENSIONS
BINARY_EXTENSIONS = core.BINARY_EXTENSIONS
SAMPLE_LIMIT = core.SAMPLE_LIMIT
SAMPLE_CHARS = core.SAMPLE_CHARS

try:
    from flask import Flask, request, jsonify, render_template
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


if HAS_FLASK:
    app = Flask(__name__, template_folder=str(core.RESOURCE_DIR / 'templates'))

    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/api/health')
    def health():
        import platform
        return jsonify({
            'ok': True,
            'version': VERSION,
            'backend': 'flask',
            'python': platform.python_version(),
            'flask_available': True,
            'field_count': sum(len(c['fields'])
                               for c in core.FIELD_LIBRARY.get('categories', [])),
            'categories': len(core.FIELD_LIBRARY.get('categories', [])),
        })

    @app.route('/api/field-library')
    def field_library():
        return jsonify({
            'library': core.FIELD_LIBRARY,
            'default_field_ids': core.masker.default_field_ids(core.FIELD_LIBRARY),
        })

    @app.route('/api/aliases')
    def aliases():
        return jsonify(core.entity.load_aliases())

    @app.route('/api/scan', methods=['POST'])
    def scan():
        config = core.build_config(request.json or {})
        return jsonify(core.scan_directory(config))

    @app.route('/api/apply', methods=['POST'])
    def apply():
        data = request.json or {}
        preview_data = data.get('preview_data') or {}
        if not preview_data:
            return jsonify({'error': '请先执行预览扫描'})
        config = core.build_config(data)
        return jsonify(core.apply_changes(config, preview_data))


def main():
    core._setup_console()

    if not HAS_FLASK:
        import server_stdlib
        print('[提示] 未检测到 Flask，自动切换到 Python 标准库服务器（零依赖）')
        server_stdlib.run()
        return

    port = core.find_free_port()
    url = f'http://127.0.0.1:{port}'
    print('=' * 58)
    print('  文件脱敏工具  |  本地离线运行，不联网、不调用任何模型')
    print('=' * 58)
    print(f'  后端: Flask')
    print(f'  访问地址: {url}')
    print('  关闭服务: 在本窗口按 Ctrl+C')
    print('=' * 58)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
