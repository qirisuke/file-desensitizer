"""性能基准脚本：测量脱敏引擎与目录扫描的耗时。

使用方式:
    python bench.py                 # 默认场景
    python bench.py --repeats 5     # 多次重复取中位数
    python bench.py --tag optimized # 标记本次运行标签
    python bench.py --ref _ref      # 与 _ref/ 下的原始实现做差分对比(见 compare 场景)

设计要点:
- 语料由 bench_corpus.py 生成（.bench_corpus/），保证可复现。
- 全部用 time.perf_counter() 计时。
- 场景 A：单文件大文本 apply_ops
- 场景 B：整个目录 scan_directory
- 场景 C：apply_changes 落盘（复制目录后执行，不破坏语料）
"""
import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import masker  # noqa: E402
import app as service  # noqa: E402

CORPUS = ROOT / '.bench_corpus'
BIG_FILE = CORPUS / 'big' / '示例能源_经营情况_大文件.txt'
SMALL_DIR = CORPUS / 'small'

# 与 test_e2e.py / test_offline.py 一致的配置：组合模式 + 全量内置字段 + 敏感词
WORD_LIST = ['示例能源', '星海项目']
KEYWORD_RULES = [{'keyword': '张三丰', 'replacement': '张某'}]


def make_config(directory: str) -> dict:
    cfg = service.build_config({
        'directory': str(directory),
        'mode': 'combo',
        'rules': KEYWORD_RULES,
        'word_list': WORD_LIST,
        'field_ids': masker.default_field_ids(service.FIELD_LIBRARY),
        'mask': '***',
        'filename_mask': '已脱敏',
        'case_sensitive': True,
        'include_filenames': True,
        'include_contents': True,
        'recursive': True,
    })
    cfg['library'] = service.FIELD_LIBRARY
    return cfg


def _time(fn, repeats: int):
    times = []
    result = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        result = fn()
        times.append(time.perf_counter() - t0)
    return times, result


def stat(name, times, extra=None):
    med = statistics.median(times)
    line = {
        'name': name,
        'median': med,
        'min': min(times),
        'max': max(times),
        'repeats': len(times),
    }
    if extra:
        line.update(extra)
    return line


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repeats', type=int, default=3)
    ap.add_argument('--tag', default='current')
    ap.add_argument('--json-out', default=None)
    args = ap.parse_args()

    if not BIG_FILE.exists() or not SMALL_DIR.exists():
        print('语料不存在，先生成：python bench_corpus.py --force')
        return 2

    config = make_config(SMALL_DIR)
    big_text = BIG_FILE.read_text(encoding='utf-8')
    big_bytes = BIG_FILE.stat().st_size

    n_small = sum(1 for p in SMALL_DIR.rglob('*') if p.is_file())
    small_bytes = sum(p.stat().st_size for p in SMALL_DIR.rglob('*') if p.is_file())

    results = []

    # ---- 场景 0: build_ops ----
    times, ops = _time(
        lambda: masker.build_ops({**config, 'library': service.FIELD_LIBRARY}),
        args.repeats)
    results.append(stat('build_ops', times, {'n_ops': len(ops)}))

    # ---- 场景 A: 单文件大文本 apply_ops ----
    times, out_a = _time(lambda: masker.apply_ops(big_text, ops), args.repeats)
    results.append(stat('apply_ops_bigfile', times, {
        'files': 1, 'bytes': big_bytes,
        'hits': sum(r['count'] for r in out_a[1]),
    }))

    # ---- 场景 A2: 大文件 apply_ops（仅统计，用于交叉验证） ----
    # ---- 场景 B: 整个目录 scan_directory ----
    times, preview = _time(lambda: service.scan_directory(config), args.repeats)
    results.append(stat('scan_directory', times, {
        'files': preview.get('scanned_files', n_small),
        'bytes': small_bytes,
        'hits': preview.get('total_content_changes'),
    }))

    # ---- 场景 C: apply_changes 落盘（在临时副本上做，不动语料） ----
    def _apply_once():
        tmp = Path(tempfile.mkdtemp(prefix='bench_apply_'))
        try:
            target = tmp / 'small'
            shutil.copytree(SMALL_DIR, target)
            cfg = make_config(target)
            prev = service.scan_directory(cfg)
            t = time.perf_counter()
            res = service.apply_changes(str(target), prev)
            return res, time.perf_counter() - t
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    apply_times = []
    apply_res = None
    for _ in range(args.repeats):
        apply_res, dt = _apply_once()
        apply_times.append(dt)
    results.append(stat('apply_changes_only', apply_times, {
        'files': apply_res.get('applied_files'),
        'errors': len(apply_res.get('errors', [])),
    }))

    # ---- 场景 D: is_text_file 遍历 ----
    all_files = [p for p in SMALL_DIR.rglob('*') if p.is_file()]
    times, _ = _time(
        lambda: [service.is_text_file(p) for p in all_files], args.repeats)
    results.append(stat('is_text_file_sweep', times, {'files': len(all_files)}))

    # ---- 场景 E: 全流程（扫描 + 落盘），真实用户路径 ----
    def _pipeline_once():
        tmp = Path(tempfile.mkdtemp(prefix='bench_pipe_'))
        try:
            target = tmp / 'small'
            shutil.copytree(SMALL_DIR, target)
            cfg = make_config(target)
            t = time.perf_counter()
            prev = service.scan_directory(cfg)
            res = service.apply_changes(str(target), prev)
            return res, time.perf_counter() - t
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    pipe_times = []
    pipe_res = None
    for _ in range(args.repeats):
        pipe_res, dt = _pipeline_once()
        pipe_times.append(dt)
    results.append(stat('pipeline_scan_plus_apply', pipe_times, {
        'files': pipe_res.get('applied_files'),
        'renames': pipe_res.get('applied_renames'),
        'errors': len(pipe_res.get('errors', [])),
    }))

    out = {'tag': args.tag, 'results': results}
    print(f'===== 基准 [{args.tag}] repeats={args.repeats} =====')
    print(f'{"场景":<24}{"中位耗时(s)":>14}{"最小(s)":>12}{"最大(s)":>12}   附加')
    for r in results:
        extra = {k: v for k, v in r.items()
                 if k not in ('name', 'median', 'min', 'max', 'repeats')}
        print(f'{r["name"]:<24}{r["median"]:>14.4f}{r["min"]:>12.4f}{r["max"]:>12.4f}   {extra}')

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'JSON 已写入 {args.json_out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
