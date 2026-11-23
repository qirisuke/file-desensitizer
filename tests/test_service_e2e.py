"""服务级端到端测试：通过 Flask API 走完整流程（扫描 -> 执行），并对比期望结果。

用法：PYTHONIOENCODING=utf-8 python tests/test_service_e2e.py
"""
import json
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as service

DATA = Path(r'C:\Users\cheng\Desktop\程富雕AI\测试文档\脱敏测试数据')
ORIGINAL = DATA / '原始文档'
EXPECTED = DATA / '期望结果'

FAILURES = []


def check(cond, msg, detail=''):
    if cond:
        print(f'  [OK]   {msg}')
    else:
        print(f'  [FAIL] {msg}')
        if detail:
            print(f'         {detail}')
        FAILURES.append(msg)


def post(path, payload):
    req = urllib.request.Request(
        f'http://127.0.0.1:{service.find_free_port()}' if False else
        'http://127.0.0.1:5099' + path,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req))


def main():
    import threading
    import socket

    port = 5099
    # 确保端口空闲
    with socket.socket() as s:
        try:
            s.bind(('127.0.0.1', port))
        except OSError:
            port = 5098

    tmp = Path(tempfile.mkdtemp(prefix='svc_test_'))
    work = tmp / 'work'
    shutil.copytree(ORIGINAL, work)

    thread = threading.Thread(
        target=lambda: service.app.run(host='127.0.0.1', port=port,
                                       debug=False, use_reloader=False),
        daemon=True)
    thread.start()

    import time
    base = f'http://127.0.0.1:{port}'
    for _ in range(40):
        try:
            urllib.request.urlopen(base + '/', timeout=1)
            break
        except Exception:
            time.sleep(0.25)

    def api(path, payload=None):
        if payload is None:
            return json.load(urllib.request.urlopen(base + path))
        req = urllib.request.Request(
            base + path,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        return json.load(urllib.request.urlopen(req))

    print('--- 1. 字段库 API ---')
    lib = api('/api/field-library')
    cats = lib['library']['categories']
    check(len(cats) >= 8, f'字段库返回 {len(cats)} 个分类')
    check(len(lib['default_field_ids']) >= 50,
          f"默认勾选 {len(lib['default_field_ids'])} 项")

    print('\n--- 2. 扫描 API ---')
    payload = {
        'directory': str(work),
        'mode': 'combo',
        'rules': [],
        'word_list': [],
        'field_ids': lib['default_field_ids'],
        'mask': '***',
        'filename_mask': '已脱敏',
        'case_sensitive': True,
        'include_filenames': False,
        'include_contents': True,
        'recursive': True,
        'generalize_entities': True,
    }
    preview = api('/api/scan', payload)
    check('error' not in preview, '扫描无错误', str(preview.get('error')))
    check(preview['scanned_files'] == 6,
          f"扫描到 {preview['scanned_files']} 个文件（期望 6）")
    check(preview['total_content_changes'] > 50,
          f"命中 {preview['total_content_changes']} 处")
    check(bool(preview.get('entity_map')), '返回实体映射表')
    check(len(preview.get('samples', [])) > 0, '返回脱敏样例')

    print('\n--- 3. 执行 API ---')
    result = api('/api/apply', {**payload, 'preview_data': preview})
    check(result['applied_files'] == 6,
          f"改写 {result['applied_files']} 个文件（期望 6）")
    check(not result['errors'], '无错误', '; '.join(result['errors']))

    print('\n--- 4. 结果对比 ---')
    import importlib.util
    evaluate_path = DATA.parent / 'evaluate.py'
    spec = importlib.util.spec_from_file_location('evaluate', evaluate_path)
    evaluate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluate)

    report = evaluate.evaluate(work)
    s = report['summary']
    check(s['漏脱敏'] == 0, f"漏脱敏 {s['漏脱敏']} 项（期望 0）")
    check(s['过度脱敏'] == 0, f"过度脱敏 {s['过度脱敏']} 项（期望 0）")
    check(s['格式损坏'] == 0, f"格式损坏 {s['格式损坏']} 项（期望 0）")
    if s['漏脱敏'] or s['过度脱敏'] or s['格式损坏']:
        evaluate.print_report(report)

    shutil.rmtree(tmp, ignore_errors=True)

    print()
    print('=' * 60)
    if FAILURES:
        print(f'失败 {len(FAILURES)} 项')
        for f in FAILURES:
            print('  -', f)
        return 1
    print('服务级端到端测试全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
