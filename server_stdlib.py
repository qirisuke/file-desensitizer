"""纯标准库 HTTP 服务器 —— 内网断网环境的零依赖运行方案。

用 http.server 实现与 Flask 版完全相同的接口，因此内网机器只要有
Python 3.8+ 就能直接跑，不需要 pip install flask。

接口：
    GET  /                     前端页面
    GET  /api/health           健康检查（版本、Python 版本、后端类型）
    GET  /api/field-library    内置敏感字段库
    GET  /api/aliases          默认别名表
    POST /api/scan             扫描预览
    POST /api/apply             执行脱敏
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import core

VERSION = '1.2.0'
BACKEND = 'stdlib'


class Handler(BaseHTTPRequestHandler):
    server_version = f'DesensitizeTool/{VERSION}'
    protocol_version = 'HTTP/1.1'

    # 默认不打印访问日志，干扰排查时用 -v 打开
    def log_message(self, fmt, *args):
        if getattr(self.server, 'verbose', False):
            super().log_message(fmt, *args)

    # ------------------------------------------------------ 响应

    def _send(self, status, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self._send(status, body, 'application/json; charset=utf-8')

    def _send_html_error(self, status, message):
        body = (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
                f'<title>{status}</title></head><body>'
                f'<h1>{status}</h1><p>{message}</p></body></html>').encode('utf-8')
        self._send(status, body, 'text/html; charset=utf-8')

    def _read_json(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError(f'请求体不是合法 JSON: {e}')

    # ------------------------------------------------------ 路由

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path in ('/', '/index.html'):
            return self._serve_index()
        if path == '/api/health':
            return self._send_json(200, self._health())
        if path == '/api/field-library':
            return self._send_json(200, {
                'library': core.FIELD_LIBRARY,
                'default_field_ids': core.masker.default_field_ids(core.FIELD_LIBRARY),
            })
        if path == '/api/aliases':
            return self._send_json(200, core.entity.load_aliases())
        return self._send_html_error(404, f'未知路径: {path}')

    def do_POST(self):
        path = self.path.split('?', 1)[0]
        try:
            data = self._read_json()
        except ValueError as e:
            return self._send_json(400, {'error': str(e)})

        if path == '/api/scan':
            config = core.build_config(data)
            return self._send_json(200, core.scan_directory(config))
        if path == '/api/apply':
            preview_data = data.get('preview_data') or {}
            if not preview_data:
                return self._send_json(400, {'error': '请先执行预览扫描'})
            config = core.build_config(data)
            return self._send_json(200, core.apply_changes(config, preview_data))
        return self._send_html_error(404, f'未知路径: {path}')

    # ------------------------------------------------------ 静态页

    def _serve_index(self):
        tpl = core.RESOURCE_DIR / 'templates' / 'index.html'
        try:
            body = tpl.read_bytes()
        except OSError:
            return self._send_html_error(
                500, f'找不到前端页面: {tpl}（部署不完整）')
        self._send(200, body, 'text/html; charset=utf-8')

    @staticmethod
    def _health():
        import platform
        return {
            'ok': True,
            'version': VERSION,
            'backend': BACKEND,
            'python': platform.python_version(),
            'flask_available': _flask_available(),
            'field_count': sum(len(c['fields'])
                               for c in core.FIELD_LIBRARY.get('categories', [])),
            'categories': len(core.FIELD_LIBRARY.get('categories', [])),
        }


def _flask_available() -> bool:
    try:
        import flask  # noqa: F401
        return True
    except ImportError:
        return False


def make_server(port: int, verbose: bool = False):
    httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    httpd.daemon_threads = True
    httpd.verbose = verbose
    return httpd


def run(port: int = 5000, verbose: bool = False):
    core._setup_console()
    port = core.find_free_port(port)
    url = f'http://127.0.0.1:{port}'
    print('=' * 58)
    print('  文件脱敏工具  |  本地离线运行，不联网、不调用任何模型')
    print('=' * 58)
    print('  后端: Python 标准库（零依赖）')
    print(f'  访问地址: {url}')
    print('  关闭服务: 在本窗口按 Ctrl+C')
    print('=' * 58)
    httpd = make_server(port, verbose)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
    finally:
        httpd.server_close()


if __name__ == '__main__':
    _verbose = '-v' in sys.argv
    _port = 5000
    for arg in sys.argv[1:]:
        if arg.isdigit():
            _port = int(arg)
    run(_port, _verbose)
