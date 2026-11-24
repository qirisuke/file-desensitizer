"""双后端一致性自检：Flask 版与标准库版必须提供完全相同的接口与行为。

内网断网环境常常装不上 Flask，因此必须有零依赖的兜底后端，
并且两者对外表现要一致，否则用户会遇到「换个环境就报错」的问题。

用法：PYTHONIOENCODING=utf-8 python tests/test_backends.py
"""
import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core
import server_stdlib

DATA = Path(r'C:\Users\cheng\Desktop\程富雕AI\测试文档\脱敏测试数据')
ORIGINAL = DATA / '原始文档'

FAILURES = []


def check(cond, msg, detail=''):
    if cond:
        print(f'  [OK]   {msg}')
    else:
        print(f'  [FAIL] {msg}')
        if detail:
            print(f'         {detail}')
        FAILURES.append(msg)


def call(base, path, payload=None, method=None):
    """调用接口，返回 (状态码, 响应体文本, Content-Type)。"""
    if payload is None and method is None:
        req = urllib.request.Request(base + path)
    else:
        data = json.dumps(payload or {}).encode('utf-8')
        req = urllib.request.Request(base + path, data=data, method=method or 'POST',
                                     headers={'Content-Type': 'application/json'})
    try:
        res = urllib.request.urlopen(req, timeout=30)
        return res.status, res.read().decode('utf-8'), res.headers.get('Content-Type', '')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', errors='replace'), \
            e.headers.get('Content-Type', '')


def start_flask(port):
    import app as service
    if not service.HAS_FLASK:
        return None
    t = threading.Thread(
        target=lambda: service.app.run(host='127.0.0.1', port=port,
                                       debug=False, use_reloader=False),
        daemon=True)
    t.start()
    return t


def wait_ready(base, tries=40):
    for _ in range(tries):
        try:
            urllib.request.urlopen(base + '/api/health', timeout=1)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def compare(flask_base, stdlib_base):
    print('--- 1. 接口清单一致 ---')
    paths = ['/api/health', '/api/field-library', '/api/aliases']
    for p in paths:
        s1, b1, c1 = call(flask_base, p)
        s2, b2, c2 = call(stdlib_base, p)
        check(s1 == s2 == 200, f'{p} 两端都返回 200', f'flask={s1} stdlib={s2}')
        check('json' in c1.lower() and 'json' in c2.lower(),
              f'{p} 两端都返回 JSON', f'flask={c1} stdlib={c2}')
        try:
            j1, j2 = json.loads(b1), json.loads(b2)
            if p == '/api/health':
                check(j1.get('field_count') == j2.get('field_count'),
                      '健康检查的字段数一致',
                      f"{j1.get('field_count')} vs {j2.get('field_count')}")
                check(j1.get('version') == j2.get('version'),
                      '健康检查的版本号一致',
                      f"{j1.get('version')} vs {j2.get('version')}")
            else:
                check(j1 == j2, f'{p} 响应内容完全一致')
        except json.JSONDecodeError as e:
            check(False, f'{p} 响应是合法 JSON', str(e))

    print('\n--- 2. 首页一致 ---')
    s1, b1, c1 = call(flask_base, '/')
    s2, b2, c2 = call(stdlib_base, '/')
    check(s1 == s2 == 200, '两端首页都返回 200')
    # 末尾换行差异无害（Flask 的 render_template 会去掉），比较时忽略
    check(b1.rstrip('\r\n') == b2.rstrip('\r\n'), '两端首页内容完全一致',
          f'长度 {len(b1)} vs {len(b2)}')
    check('/api/health' in b2, '首页包含健康检查调用')

    print('\n--- 3. 未知路径返回 404 ---')
    s1, b1, _ = call(flask_base, '/api/nope')
    s2, b2, _ = call(stdlib_base, '/api/nope')
    check(s1 == 404 and s2 == 404, f'两端都返回 404', f'flask={s1} stdlib={s2}')

    print('\n--- 4. 扫描结果一致 ---')
    tmp = Path(tempfile.mkdtemp(prefix='backend_cmp_'))
    work = tmp / 'work'
    if ORIGINAL.exists():
        shutil.copytree(ORIGINAL, work)
        payload = {
            'directory': str(work),
            'mode': 'combo', 'rules': [], 'word_list': [],
            'field_ids': core.masker.default_field_ids(core.FIELD_LIBRARY),
            'mask': '***', 'filename_mask': '已脱敏', 'case_sensitive': True,
            'include_filenames': False, 'include_contents': True,
            'recursive': True, 'generalize_entities': True,
        }
        s1, b1, _ = call(flask_base, '/api/scan', payload)
        s2, b2, _ = call(stdlib_base, '/api/scan', payload)
        j1, j2 = json.loads(b1), json.loads(b2)
        check(j1.get('scanned_files') == j2.get('scanned_files'),
              '扫描文件数一致',
              f"{j1.get('scanned_files')} vs {j2.get('scanned_files')}")
        check(j1.get('total_content_changes') == j2.get('total_content_changes'),
              '命中处数一致',
              f"{j1.get('total_content_changes')} vs {j2.get('total_content_changes')}")
        check(j1.get('entity_map') == j2.get('entity_map'),
              '实体映射表一致')
    else:
        print('  [SKIP] 找不到测试数据，跳过扫描对比')
    shutil.rmtree(tmp, ignore_errors=True)


def main():
    flask_port, stdlib_port = 5087, 5086
    flask_base = f'http://127.0.0.1:{flask_port}'
    stdlib_base = f'http://127.0.0.1:{stdlib_port}'

    print('启动标准库后端 ...')
    httpd = server_stdlib.make_server(stdlib_port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    print('启动 Flask 后端 ...')
    start_flask(flask_port)

    if not wait_ready(stdlib_base):
        print('  标准库后端未就绪')
        return 1
    flask_ready = wait_ready(flask_base)
    if not flask_ready:
        print('  [SKIP] Flask 不可用，只验证标准库后端自洽性')
        s, b, c = call(stdlib_base, '/api/health')
        check(s == 200 and 'json' in c.lower(), '标准库后端健康检查正常')
        httpd.shutdown()
        return 0 if not FAILURES else 1

    try:
        compare(flask_base, stdlib_base)
    finally:
        httpd.shutdown()

    print()
    print('=' * 60)
    if FAILURES:
        print(f'失败 {len(FAILURES)} 项')
        for f in FAILURES:
            print('  -', f)
        return 1
    print('两种后端行为一致')
    return 0


if __name__ == '__main__':
    sys.exit(main())
