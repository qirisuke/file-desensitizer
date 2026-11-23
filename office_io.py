"""Office 格式（xlsx / docx）读写。

只用标准库 zipfile + xml.etree，不依赖 openpyxl / python-docx ——
项目是内网离线交付，目标机装不了 pip 包。

设计要点：
- 读取时把文件拆成「可替换的文本单元」，每个单元带定位信息。
- 写回时只改动单元文本，其余部分（样式、公式、其它 sheet/段落）原样保留。
- xlsx 脱敏后的单元格改为 inlineStr，避免去动 sharedStrings 表。
"""
import re
import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG_REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
XML = 'http://www.w3.org/XML/1998/namespace'

ET.register_namespace('', MAIN)
ET.register_namespace('r', REL)

XLSX_SUFFIXES = ('.xlsx', '.xlsm')
DOCX_SUFFIXES = ('.docx',)


def is_office_file(path) -> bool:
    p = Path(path)
    return p.suffix.lower() in XLSX_SUFFIXES + DOCX_SUFFIXES


# ---------------------------------------------------------------- xlsx

def _norm_target(target: str) -> str:
    t = target.lstrip('/')
    if t.startswith('xl/'):
        return t
    return 'xl/' + t


def _cell_text(cell, shared):
    ctype = cell.get('t')
    if ctype == 's':
        v = cell.find(f'{{{MAIN}}}v')
        if v is not None and v.text is not None:
            idx = int(v.text)
            return shared[idx] if 0 <= idx < len(shared) else ''
        return ''
    if ctype == 'inlineStr':
        isel = cell.find(f'{{{MAIN}}}is')
        if isel is None:
            return ''
        return ''.join(t.text or '' for t in isel.iter(f'{{{MAIN}}}t'))
    if ctype == 'str':
        v = cell.find(f'{{{MAIN}}}v')
        return v.text or '' if v is not None else ''
    v = cell.find(f'{{{MAIN}}}v')
    return v.text or '' if v is not None else ''


def _read_shared(z):
    if 'xl/sharedStrings.xml' not in z.namelist():
        return []
    root = ET.fromstring(z.read('xl/sharedStrings.xml'))
    out = []
    for si in root.findall(f'{{{MAIN}}}si'):
        out.append(''.join(t.text or '' for t in si.iter(f'{{{MAIN}}}t')))
    return out


def _sheet_map(z):
    """返回 [(sheet名, worksheet 在 zip 内的路径), ...]，保持工作簿顺序。"""
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    relmap = {r.get('Id'): _norm_target(r.get('Target', '')) for r in rels}
    out = []
    for sheet in wb.iter(f'{{{MAIN}}}sheet'):
        target = relmap.get(sheet.get(f'{{{REL}}}id'))
        if target:
            out.append((sheet.get('name'), target))
    return out


def read_xlsx(path):
    """返回 (sheet名, 行索引, 列索引, 单元格文本) 列表。"""
    units = []
    with zipfile.ZipFile(path) as z:
        shared = _read_shared(z)
        for name, target in _sheet_map(z):
            root = ET.fromstring(z.read(target))
            for ri, row in enumerate(root.iter(f'{{{MAIN}}}row')):
                for ci, cell in enumerate(row.findall(f'{{{MAIN}}}c')):
                    units.append((name, ri, ci, _cell_text(cell, shared)))
    return units


def _set_cell_text(cell, text):
    """把单元格设为文本内容（inlineStr），保留样式引用 s。"""
    for tag in ('v', 'is', 'f'):
        for el in cell.findall(f'{{{MAIN}}}{tag}'):
            cell.remove(el)
    cell.set('t', 'inlineStr')
    isel = ET.SubElement(cell, f'{{{MAIN}}}is')
    t = ET.SubElement(isel, f'{{{MAIN}}}t')
    t.text = text
    if text != text.strip() or '  ' in text:
        t.set(f'{{{XML}}}space', 'preserve')


def write_xlsx(src, dst, changes):
    """changes: {(sheet名, 行索引, 列索引): 新文本}"""
    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        blobs = {n: zin.read(n) for n in names}

    for sheet_name, target in _sheet_map_from_blobs(blobs):
        root = ET.fromstring(blobs[target])
        touched = False
        for ri, row in enumerate(root.iter(f'{{{MAIN}}}row')):
            for ci, cell in enumerate(row.findall(f'{{{MAIN}}}c')):
                key = (sheet_name, ri, ci)
                if key in changes:
                    _set_cell_text(cell, changes[key])
                    touched = True
        if touched:
            blobs[target] = ET.tostring(root, encoding='UTF-8', xml_declaration=True)

    _write_zip(src, dst, blobs, names)


def _sheet_map_from_blobs(blobs):
    wb = ET.fromstring(blobs['xl/workbook.xml'])
    rels = ET.fromstring(blobs['xl/_rels/workbook.xml.rels'])
    relmap = {r.get('Id'): _norm_target(r.get('Target', '')) for r in rels}
    out = []
    for sheet in wb.iter(f'{{{MAIN}}}sheet'):
        target = relmap.get(sheet.get(f'{{{REL}}}id'))
        if target:
            out.append((sheet.get('name'), target))
    return out


# ---------------------------------------------------------------- docx

def _para_text(p):
    return ''.join(t.text or '' for t in p.iter(f'{{{W}}}t'))


def read_docx(path):
    """返回 ('p', 段落索引, 文本) 与 ('c', 表索引, 行索引, 列索引, 文本) 列表。

    段落索引与 write_docx 使用的索引一致：body 中 w:p 元素的出现次序。
    """
    units = []
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('word/document.xml'))
        body = root.find(f'{{{W}}}body')
        pi = 0
        for el in body:
            if el.tag.split('}')[1] != 'p':
                continue
            units.append(('p', pi, _para_text(el)))
            pi += 1
    for ti, rows in enumerate(read_docx_tables(path)):
        for ri, row in enumerate(rows):
            for ci, text in enumerate(row):
                units.append(('c', ti, ri, ci, text))
    return units


def read_docx_tables(path):
    """返回 [(表格二维数组), ...]，保留表格边界与行列结构。"""
    tables = []
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('word/document.xml'))
        body = root.find(f'{{{W}}}body')
        for el in body:
            if el.tag.split('}')[1] != 'tbl':
                continue
            rows = []
            for tr in el.findall(f'{{{W}}}tr'):
                cells = []
                for tc in tr.findall(f'{{{W}}}tc'):
                    cells.append(''.join(t.text or '' for t in tc.iter(f'{{{W}}}t')))
                rows.append(cells)
            tables.append(rows)
    return tables


def docx_paragraphs(path):
    """只返回段落文本（不含表格）。"""
    out = []
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('word/document.xml'))
        body = root.find(f'{{{W}}}body')
        for el in body:
            if el.tag.split('}')[1] == 'p':
                out.append(''.join(t.text or '' for t in el.iter(f'{{{W}}}t')))
    return out


def _write_text_into(container, text):
    """把 text 写进段落/单元格，保留 run 结构与格式。

    做法：把全部文字放进第一个 w:t，其余 w:t 清空 —— run 数量与 rPr 不变，
    因此字体、字号等格式不会丢失。
    """
    ts = list(container.iter(f'{{{W}}}t'))
    if not ts:
        return
    ts[0].text = text
    if text != text.strip() or '  ' in text:
        ts[0].set(f'{{{XML}}}space', 'preserve')
    for t in ts[1:]:
        t.text = ''


def write_docx(src, dst, changes):
    """changes: {('p', 段落索引): 新文本} 或 {('c', 表, 行, 列): 新文本}"""
    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        blobs = {n: zin.read(n) for n in names}

    root = ET.fromstring(blobs['word/document.xml'])
    body = root.find(f'{{{W}}}body')
    pi, ti = 0, 0
    for el in body:
        tag = el.tag.split('}')[1]
        if tag == 'p':
            key = ('p', pi)
            if key in changes:
                _write_text_into(el, changes[key])
            pi += 1
        elif tag == 'tbl':
            for ri, tr in enumerate(el.findall(f'{{{W}}}tr')):
                for ci, tc in enumerate(tr.findall(f'{{{W}}}tc')):
                    key = ('c', ti, ri, ci)
                    if key in changes:
                        _write_text_into(tc, changes[key])
            ti += 1
    blobs['word/document.xml'] = ET.tostring(root, encoding='UTF-8', xml_declaration=True)

    _write_zip(src, dst, blobs, names)


# ---------------------------------------------------------------- 通用

def _write_zip(src, dst, blobs, names):
    """重写 zip：保留原顺序与压缩方式。"""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as zin:
        infos = {i.filename: i for i in zin.infolist()}
    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        for name in names:
            info = infos.get(name)
            if info is not None:
                new_info = zipfile.ZipInfo(name, date_time=info.date_time)
                new_info.compress_type = info.compress_type
                new_info.external_attr = info.external_attr
            else:
                new_info = zipfile.ZipInfo(name)
                new_info.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(new_info, blobs[name])


def read_units(path):
    """读取文件的可替换单元。返回统一结构的列表。

    单元元组：
      ('cell', sheet名, 行索引, 列索引, 文本)
      ('p',    段落索引, 文本)
      ('c',    表索引, 行索引, 列索引, 文本)
    """
    p = Path(path)
    if p.suffix.lower() in XLSX_SUFFIXES:
        return [('cell',) + u for u in read_xlsx(p)]
    if p.suffix.lower() in DOCX_SUFFIXES:
        return read_docx(p)
    raise ValueError(f'不是 Office 文件: {path}')


def unit_key(unit):
    """单元定位键（不含文本），与写回端的键格式一致。

    xlsx 单元格 -> (sheet名, 行索引, 列索引)
    docx 段落   -> ('p', 段落索引)
    docx 单元格 -> ('c', 表索引, 行索引, 列索引)
    """
    kind = unit[0]
    if kind == 'cell':
        return (unit[1], unit[2], unit[3])
    if kind == 'p':
        return ('p', unit[1])
    return ('c', unit[1], unit[2], unit[3])


def write_units(src, dst, changes):
    """按 changes 字典写回。键格式见 unit_key。"""
    p = Path(src)
    if p.suffix.lower() in XLSX_SUFFIXES:
        write_xlsx(p, dst, changes)
    elif p.suffix.lower() in DOCX_SUFFIXES:
        write_docx(p, dst, changes)
    else:
        raise ValueError(f'不是 Office 文件: {src}')
