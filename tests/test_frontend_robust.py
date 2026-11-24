"""前端健壮性自检：验证后端返回 HTML 时，页面给出可诊断的错误而非 JSON 崩溃。

背景：内网环境常见「后端返回 404/500 的 HTML 页面」或「代理拦截页」，
前端直接 res.json() 会抛 "unexpected token < in json at position 0"，用户无从判断原因。

这里用 Node（若可用）跑一遍页面里的请求封装逻辑，验证：
1. 正常 JSON -> 解析成功
2. HTML 响应 -> 抛出可诊断错误（含状态码与响应片段）
3. 非 JSON 的 200 -> 抛出明确错误
4. 非法 JSON -> 抛出解析错误
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / 'templates' / 'index.html'

FAILURES = []


def check(cond, msg, detail=''):
    if cond:
        print(f'  [OK]   {msg}')
    else:
        print(f'  [FAIL] {msg}')
        if detail:
            print(f'         {detail}')
        FAILURES.append(msg)


def extract_block(js, start_marker):
    """从 start_marker 处取出完整的代码块（按大括号配对）。

    不能直接截到下一个 'function'，那会把后面的函数一起带进来，
    触发 'addRule is not defined' 之类的错误。
    """
    idx = js.find(start_marker)
    if idx < 0:
        return None
    # 跳过到第一个 { 或 class 体开始
    brace = js.find('{', idx)
    if brace < 0:
        return None
    depth = 0
    i = brace
    while i < len(js):
        c = js[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return js[idx:i + 1]
        i += 1
    return None


def extract_js():
    """取出页面里的 <script> 内容。"""
    html = TPL.read_text(encoding='utf-8')
    m = re.search(r'<script>(.*?)</script>', html, re.S)
    if not m:
        raise SystemExit('页面里没有 script 块')
    return m.group(1)


HARNESS = r'''
// 把页面的请求封装抽出来单独测试
{api_block}

const results = [];
function check(name, fn) {
  return Promise.resolve().then(fn).then(
    () => results.push({name, ok: true}),
    (e) => results.push({name, ok: false, msg: String(e && e.message), detail: String(e && e.detail)})
  );
}

// 用假的 fetch 替换全局
globalThis.fetch = (path) => {
  if (path === '/ok') {
    return Promise.resolve({
      ok: true, status: 200,
      headers: { get: () => 'application/json; charset=utf-8' },
      text: () => Promise.resolve('{"a":1}'),
    });
  }
  if (path === '/html404') {
    return Promise.resolve({
      ok: false, status: 404,
      headers: { get: () => 'text/html; charset=utf-8' },
      text: () => Promise.resolve('<!DOCTYPE html><html><body>404 Not Found</body></html>'),
    });
  }
  if (path === '/html200') {
    return Promise.resolve({
      ok: true, status: 200,
      headers: { get: () => 'text/html' },
      text: () => Promise.resolve('<html>proxy block page</html>'),
    });
  }
  if (path === '/badjson') {
    return Promise.resolve({
      ok: true, status: 200,
      headers: { get: () => 'application/json' },
      text: () => Promise.resolve('{oops'),
    });
  }
  return Promise.reject(new Error('network down'));
};

(async () => {
  await check('正常 JSON 可解析', async () => {
    const d = await apiGet('/ok');
    if (d.a !== 1) throw new Error('解析结果不对');
  });
  await check('HTML 404 -> 报状态码与片段', async () => {
    try {
      await apiGet('/html404');
      throw new Error('本该抛错却成功了');
    } catch (e) {
      if (!/404/.test(e.message)) throw new Error('错误信息里没有状态码: ' + e.message);
      if (!/html|HTML/i.test(e.detail)) throw new Error('错误信息里没有提示非 JSON: ' + e.detail);
    }
  });
  await check('HTML 200 -> 报类型不符', async () => {
    try {
      await apiGet('/html200');
      throw new Error('本该抛错却成功了');
    } catch (e) {
      if (!/不是 JSON/.test(e.message)) throw new Error('错误信息不明确: ' + e.message);
    }
  });
  await check('非法 JSON -> 报解析失败', async () => {
    try {
      await apiGet('/badjson');
      throw new Error('本该抛错却成功了');
    } catch (e) {
      if (!/JSON/.test(e.message)) throw new Error('错误信息不明确: ' + e.message);
    }
  });
  await check('连不上 -> 报连接失败', async () => {
    try {
      await apiGet('/down');
      throw new Error('本该抛错却成功了');
    } catch (e) {
      if (!/无法连接/.test(e.message)) throw new Error('错误信息不明确: ' + e.message);
    }
  });
  console.log(JSON.stringify(results));
})();
'''


def main():
    js = extract_js()
    # 抽出 ApiError / apiGet / apiPost / parseJson 这几段
    blocks = []
    for name in ('class ApiError', 'async function apiGet', 'async function apiPost',
                 'async function parseJson'):
        block = extract_block(js, name)
        if block is None:
            print(f'  [FAIL] 页面里找不到 {name}')
            FAILURES.append(name)
            return 1
        blocks.append(block)
    api_block = '\n\n'.join(blocks)

    harness = HARNESS.replace('{api_block}', api_block)

    # 写成临时脚本再执行 —— 命令行有长度限制，-e 传长脚本在 Windows 上会失败
    import tempfile
    fd, script_name = tempfile.mkstemp(prefix='fe_robust_', suffix='.js')
    os.close(fd)
    script_path = Path(script_name)
    script_path.write_text(harness, encoding='utf-8')

    node = None
    for cand in ('node', 'nodejs'):
        try:
            r = subprocess.run([cand, '--version'], capture_output=True, timeout=10)
            if r.returncode == 0:
                node = cand
                break
        except (OSError, subprocess.TimeoutExpired):
            continue

    if node is None:
        print('  [SKIP] 未安装 Node，跳过前端封装逻辑测试')
        print('  （静态检查仍然进行）')
        # 退化为静态检查
        src = TPL.read_text(encoding='utf-8')
        check('前端不再有裸 res.json() 调用',
              'res.json()' not in src,
              '仍有直接调用 res.json() 的地方')
        check('存在 res.ok 检查', 'res.ok' in src)
        return 0 if not FAILURES else 1

    r = subprocess.run([node, str(script_path)], capture_output=True, timeout=30,
                       cwd=str(ROOT))
    out = r.stdout.decode('utf-8', errors='replace').strip()
    if r.returncode != 0 or not out:
        print('  Node 执行失败:')
        print(r.stderr.decode('utf-8', errors='replace')[:1500])
        return 1

    import json
    results = json.loads(out.splitlines()[-1])
    for item in results:
        check(item['ok'], item['name'], item.get('detail', ''))

    # 静态检查：页面里不应再有裸 res.json()
    src = TPL.read_text(encoding='utf-8')
    check('前端不再有裸 res.json() 调用',
          'res.json()' not in src.replace('parseJson', ''),
          '仍有直接调用 res.json() 的地方')
    check('存在 res.ok 检查', 'res.ok' in src)
    check('启动时做健康检查', '/api/health' in src)

    print()
    print('=' * 60)
    if FAILURES:
        print(f'失败 {len(FAILURES)} 项')
        for f in FAILURES:
            print('  -', f)
        return 1
    print('前端健壮性自检通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
