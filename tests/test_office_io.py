"""Office 读写层自检：验证脱敏后文件仍是合法 Office 文件，且结构保真。

用法：PYTHONIOENCODING=utf-8 python tests/test_office_io.py
"""
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import office_io

DATA = Path(r'C:\Users\cheng\Desktop\程富雕AI\测试文档\脱敏测试数据\原始文档')
MAIN = office_io.MAIN
W = office_io.W

# 匹配长数字：含千分位与小数，如 1,286,543.90 / 91120100MA06XYWX0 / 13800138000
LONG_NUM = re.compile(r'(?<![0-9])[0-9][0-9,.]*[0-9](?![0-9])')
ALNUM = re.compile(r'(?<![0-9A-Za-z])[0-9A-Za-z]{8,}(?![0-9A-Za-z])')

FAILURES = []


def check(cond, msg):
    if cond:
        print(f'  [OK]   {msg}')
    else:
        print(f'  [FAIL] {msg}')
        FAILURES.append(msg)


def sheet_names(path):
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        return [s.get('name') for s in wb.iter(f'{{{MAIN}}}sheet')]


def sheet_grid(path):
    """{sheet名: [[单元格文本...]]}"""
    units = office_io.read_units(path)
    out = {}
    for _kind, name, ri, ci, text in units:
        rows = out.setdefault(name, [])
        while len(rows) <= ri:
            rows.append([])
        while len(rows[ri]) <= ci:
            rows[ri].append('')
        rows[ri][ci] = text
    return out


def docx_stats(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('word/document.xml'))
    body = root.find(f'{{{W}}}body')
    paras = body.findall(f'{{{W}}}p')
    tables = body.findall(f'{{{W}}}tbl')
    table_shapes = []
    for tbl in tables:
        rows = tbl.findall(f'{{{W}}}tr')
        shape = []
        for tr in rows:
            shape.append(len(tr.findall(f'{{{W}}}tc')))
        table_shapes.append(shape)
    return len(paras), table_shapes


def valid_xlsx(path):
    """能否被解析 + 关键部件齐全"""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            for required in ('xl/workbook.xml', '[Content_Types].xml'):
                if required not in names:
                    return False, f'缺少 {required}'
            for n in names:
                if n.startswith('xl/worksheets/') and n.endswith('.xml'):
                    ET.fromstring(z.read(n))
            ET.fromstring(z.read('xl/workbook.xml'))
            if 'xl/sharedStrings.xml' in names:
                ET.fromstring(z.read('xl/sharedStrings.xml'))
        return True, 'ok'
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'


def valid_docx(path):
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if 'word/document.xml' not in names:
                return False, '缺少 word/document.xml'
            ET.fromstring(z.read('word/document.xml'))
        return True, 'ok'
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'


def test_xlsx(path, tmp):
    print(f'\n--- xlsx: {path.name} ---')
    units = office_io.read_units(path)
    orig_grid = sheet_grid(path)
    orig_sheets = sheet_names(path)
    print(f'  读取到 {len(units)} 个单元')

    # 脱敏：把长数字串替换掉
    changes = {}
    for u in units:
        key = office_io.unit_key(u)
        text = u[-1]
        new = LONG_NUM.sub('***', text)
        if new != text:
            changes[key] = new
    print(f'  {len(changes)} 个单元有变更')

    dst = tmp / path.name
    office_io.write_units(path, dst, changes)

    ok, msg = valid_xlsx(dst)
    check(ok, f'输出是合法 xlsx ({msg})')
    check(sheet_names(dst) == orig_sheets,
          f'sheet 名与顺序不变: {sheet_names(dst)}')
    new_grid = sheet_grid(dst)
    check(list(new_grid.keys()) == list(orig_grid.keys()), 'sheet 集合不变')
    for name in orig_grid:
        a, b = orig_grid[name], new_grid.get(name, [])
        check(len(a) == len(b), f'sheet「{name}」行数不变 ({len(a)} -> {len(b)})')
        if len(a) == len(b):
            widths_ok = all(len(r1) == len(r2) for r1, r2 in zip(a, b))
            check(widths_ok, f'sheet「{name}」各行列数不变')
            # 抽查变更确实生效
            hit = False
            for r1, r2 in zip(a, b):
                for c1, c2 in zip(r1, r2):
                    if c1 != c2:
                        if '***' in c2:
                            hit = True
            check(hit, f'sheet「{name}」脱敏内容已写入')
    return dst


def test_docx(path, tmp):
    print(f'\n--- docx: {path.name} ---')
    units = office_io.read_units(path)
    orig_paras, orig_tables = docx_stats(path)
    print(f'  读取到 {len(units)} 个单元（段落 {orig_paras}，表格 {len(orig_tables)}）')

    changes = {}
    for u in units:
        key = office_io.unit_key(u)
        text = u[-1]
        new = LONG_NUM.sub('***', text)
        if new != text:
            changes[key] = new
    print(f'  {len(changes)} 个单元有变更')

    dst = tmp / path.name
    office_io.write_units(path, dst, changes)

    ok, msg = valid_docx(dst)
    check(ok, f'输出是合法 docx ({msg})')
    new_paras, new_tables = docx_stats(dst)
    check(new_paras == orig_paras, f'段落数不变 ({orig_paras} -> {new_paras})')
    check(new_tables == orig_tables,
          f'表格数与行列结构不变 ({orig_tables} -> {new_tables})')

    # 读回内容验证替换生效
    new_units = office_io.read_units(dst)
    changed = sum(1 for u in new_units if '***' in u[-1])
    check(changed > 0, f'替换已生效（{changed} 个单元含 ***）')
    # 非数字内容必须原样保留
    orig_map = {office_io.unit_key(u): u[-1] for u in units}
    kept = 0
    bad = []
    for u in new_units:
        k = office_io.unit_key(u)
        o = orig_map.get(k, '')
        n = u[-1]
        if o == n:
            kept += 1
        elif LONG_NUM.sub('***', o) != n:
            bad.append((k, o[:40], n[:40]))
    check(not bad, f'非目标内容原样保留（保留 {kept} 个单元）')
    if bad:
        for b in bad[:5]:
            print('        差异:', b)
    return dst


def main():
    files = sorted(DATA.iterdir())
    xlsx = [f for f in files if f.suffix.lower() == '.xlsx']
    docx = [f for f in files if f.suffix.lower() == '.docx']

    if not xlsx and not docx:
        print('找不到测试数据:', DATA)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix='office_io_test_'))
    try:
        for f in xlsx:
            test_xlsx(f, tmp)
        for f in docx:
            test_docx(f, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print('=' * 60)
    if FAILURES:
        print(f'失败 {len(FAILURES)} 项:')
        for f in FAILURES:
            print('  -', f)
        return 1
    print('全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
