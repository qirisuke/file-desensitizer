import sys
import json
import socket
import threading
import webbrowser
from pathlib import Path

from flask import Flask, request, jsonify, render_template

import masker
import entity
import office_io
import pipeline

if getattr(sys, 'frozen', False):
    RESOURCE_DIR = Path(sys._MEIPASS)
else:
    RESOURCE_DIR = Path(__file__).resolve().parent

app = Flask(__name__, template_folder=str(RESOURCE_DIR / 'templates'))

FIELD_LIBRARY = masker.load_field_library(RESOURCE_DIR / 'sensitive_fields.json')

TEXT_EXTENSIONS = {
    '.txt', '.md', '.csv', '.json', '.xml', '.yaml', '.yml', '.ini', '.cfg',
    '.conf', '.log', '.tsv', '.sql', '.py', '.js', '.ts', '.java', '.c', '.cpp',
    '.h', '.hpp', '.go', '.rs', '.rb', '.php', '.sh', '.bat', '.ps1',
    '.html', '.htm', '.css', '.scss', '.less', '.vue', '.jsx', '.tsx',
    '.properties', '.env', '.gitignore', '.dockerignore', '.toml', '.lock',
    '.rtf', '.tex', '.rst', '.adoc', '.doc', '.docx', '.xls', '.xlsx', '.ppt',
    '.pptx', '.pdf',
}

BINARY_EXTENSIONS = {
    '.exe', '.dll', '.so', '.dylib', '.png', '.jpg', '.jpeg', '.gif', '.bmp',
    '.ico', '.webp', '.mp3', '.mp4', '.avi', '.mov', '.zip', '.rar', '.7z',
    '.gz', '.tar', '.pyc', '.pyd', '.bin', '.db', '.sqlite', '.woff', '.woff2',
    '.ttf', '.eot', '.otf',
}

SAMPLE_LIMIT = 3
SAMPLE_CHARS = 900


def is_text_file(filepath: Path) -> bool:
    # 后缀只算一次，避免重复调用 filepath.suffix.lower()
    suffix = filepath.suffix.lower()
    if suffix in BINARY_EXTENSIONS:
        return False
    if suffix in TEXT_EXTENSIONS:
        return True
    # 不在扩展名白名单里：读前 8192 字节做 utf-8 解码试探
    try:
        with open(filepath, 'rb') as f:
            chunk = f.read(8192)
        chunk.decode('utf-8')
        return True
    except (UnicodeDecodeError, PermissionError, OSError):
        return False


def _clean_directory(raw: str) -> str:
    return (raw or '').strip().rstrip('/').rstrip('\\')


WINDOWS_RESERVED = {
    'CON', 'PRN', 'AUX', 'NUL',
    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
}


def safe_filename(name: str) -> str:
    """保证重命名目标在 Windows 上合法。"""
    name = (name or '').strip().rstrip('. ')
    if not name:
        return '脱敏'
    stem = name.partition('.')[0].upper()
    if stem in WINDOWS_RESERVED:
        name = '_' + name
    return name


def build_config(data: dict) -> dict:
    mode = data.get('mode', masker.MODE_REPLACE)
    if mode not in masker.VALID_MODES:
        mode = masker.MODE_REPLACE
    return {
        'directory': _clean_directory(data.get('directory', '')),
        'mode': mode,
        'rules': data.get('rules', []) or [],
        'word_list': data.get('word_list', []) or [],
        'field_ids': data.get('field_ids', []) or [],
        'mask': data.get('mask') or masker.DEFAULT_MASK,
        'filename_mask': data.get('filename_mask') or masker.DEFAULT_FILENAME_MASK,
        'case_sensitive': data.get('case_sensitive', True),
        'include_filenames': data.get('include_filenames', True),
        'include_contents': data.get('include_contents', True),
        'recursive': data.get('recursive', True),
        'generalize_entities': data.get('generalize_entities', True),
        'aliases': data.get('aliases') or None,
    }


def _collect_files(base: Path, recursive: bool):
    walker = base.rglob('*') if recursive else base.glob('*')
    return [f for f in walker
            if f.is_file() and (is_text_file(f) or office_io.is_office_file(f))]


def _process_file(pipe, filepath: Path):
    """处理单个文件。

    返回 (新文本, 命中明细, 新单元值)。纯文本给新文本，Office 给单元值字典。
    """
    suffix = filepath.suffix.lower()
    if suffix in office_io.XLSX_SUFFIXES:
        changes, matched = pipe.process_xlsx(filepath)
        return None, matched, changes
    if suffix in office_io.DOCX_SUFFIXES:
        changes, matched = pipe.process_docx(filepath)
        return None, matched, changes
    content = filepath.read_text(encoding='utf-8')
    if suffix == '.csv':
        new_content, matched = pipe.process_csv(content)
    else:
        new_content, matched = pipe.process_plain_text(content)
    return new_content, matched, None


def _new_filename(pipe, filepath: Path):
    """计算脱敏后的文件名。"""
    if not pipe.filename_ops:
        return filepath.name
    new_name, _ = masker.apply_ops(filepath.name, pipe.filename_ops,
                                     for_filename=True, collect=False)
    if pipe.mapper is not None:
        new_name = pipe.mapper.map_text(new_name, '')
    return safe_filename(new_name)


def _office_sample(cell_changes: dict) -> str:
    """从 Office 单元值里取一段样例文本。"""
    parts = []
    for value in cell_changes.values():
        if value:
            parts.append(str(value))
        if len(' | '.join(parts)) > SAMPLE_CHARS:
            break
    return ' | '.join(parts)[:SAMPLE_CHARS]


def scan_directory(config: dict) -> dict:
    base = Path(config['directory'])
    if not config['directory']:
        return {'error': '请输入目标目录'}
    if not base.exists() or not base.is_dir():
        return {'error': f'目录不存在: {config["directory"]}'}

    ops = masker.build_ops({**config, 'library': FIELD_LIBRARY})
    if not ops:
        return {'error': '请至少配置一条替换规则，或在「敏感词屏蔽」中启用字段'}

    mapper = entity.EntityMapper() if config.get('generalize_entities', True) else None
    pipe = pipeline.Pipeline(config, FIELD_LIBRARY, mapper)

    files = _collect_files(base, config['recursive'])

    filename_changes = []
    content_changes = []
    samples = []

    include_filenames = config['include_filenames']
    include_contents = config['include_contents']

    for filepath in files:
        rel_path = filepath.relative_to(base)
        rel_str = str(rel_path)

        if include_filenames:
            new_name = _new_filename(pipe, filepath)
            if new_name != filepath.name:
                filename_changes.append({
                    'old': rel_str,
                    'new': str(rel_path.parent / new_name),
                })

        if not include_contents:
            continue
        try:
            new_content, matched, cell_changes = _process_file(pipe, filepath)
        except (UnicodeDecodeError, PermissionError, OSError):
            continue

        if cell_changes is not None:
            content_changes.append({
                'file': rel_str,
                'ops': matched,
                'kind': 'office',
            })
            if len(samples) < SAMPLE_LIMIT:
                samples.append({
                    'file': rel_str,
                    'sample': _office_sample(cell_changes),
                })
        elif new_content is not None:
            try:
                original = filepath.read_text(encoding='utf-8')
            except (UnicodeDecodeError, PermissionError, OSError):
                continue
            if new_content != original:
                content_changes.append({
                    'file': rel_str,
                    'ops': matched,
                    'kind': 'text',
                })
                if len(samples) < SAMPLE_LIMIT:
                    samples.append({
                        'file': rel_str,
                        'sample': new_content[:SAMPLE_CHARS],
                    })

    return {
        'scanned_files': len(files),
        'filename_changes': filename_changes,
        'content_changes': content_changes,
        'samples': samples,
        'entity_map': mapper.export_map() if mapper else {},
        # 落盘时按同一份配置重算，保证与预览完全一致（幂等）
        'config': {k: v for k, v in config.items() if k != 'aliases'},
        'total_filename_changes': len(filename_changes),
        'total_content_changes': sum(
            sum(r.get('count', 0) for r in c['ops'])
            for c in content_changes
        ),
        'matched_files': len(content_changes),
    }


def apply_changes(config, preview_data: dict) -> dict:
    # 向后兼容：早期签名为 apply_changes(directory, preview_data)。
    # 那时 preview 里没有 config，只能按预览记录的 ops 直接落盘。
    if isinstance(config, str):
        preview_config = preview_data.get('config')
        if preview_config:
            config = build_config(preview_config)
        else:
            return _apply_changes_legacy(Path(_clean_directory(config)),
                                          preview_data)

    base = Path(config['directory'])
    applied_files = 0
    applied_renames = 0
    errors = []

    # 用预览时保存的实体映射表重建，保证泛化名与预览完全一致
    mapper = entity.EntityMapper() if config.get('generalize_entities', True) else None
    if mapper is not None and preview_data.get('entity_map'):
        mapper.import_map(preview_data['entity_map'])
    pipe = pipeline.Pipeline(config, FIELD_LIBRARY, mapper)

    for item in preview_data.get('content_changes', []):
        filepath = base / item['file']
        if not filepath.exists():
            continue
        try:
            new_content, _matched, cell_changes = _process_file(pipe, filepath)
            if cell_changes is not None:
                if cell_changes:
                    _write_office_inplace(filepath, cell_changes)
                    applied_files += 1
                continue
            if new_content is None:
                continue
            original = filepath.read_text(encoding='utf-8')
            if new_content != original:
                filepath.write_text(new_content, encoding='utf-8')
                applied_files += 1
        except Exception as e:
            errors.append(f'{item["file"]}: {e}')

    for item in preview_data.get('filename_changes', []):
        old_path = base / item['old']
        new_path = base / item['new']
        if not old_path.exists():
            continue
        parent = new_path.parent
        if not parent.is_dir():
            parent.mkdir(parents=True, exist_ok=True)
        try:
            old_path.rename(new_path)
            applied_renames += 1
        except Exception as e:
            errors.append(f'{item["old"]}: {e}')

    return {
        'applied_files': applied_files,
        'applied_renames': applied_renames,
        'errors': errors,
    }


def _apply_changes_legacy(base: Path, preview_data: dict) -> dict:
    """旧签名路径：按预览里记录的 ops 直接落盘（不重算）。"""
    applied_files = 0
    applied_renames = 0
    errors = []

    for item in preview_data.get('content_changes', []):
        filepath = base / item['file']
        try:
            content = filepath.read_text(encoding='utf-8')
        except FileNotFoundError:
            continue
        except Exception as e:
            errors.append(f'{item["file"]}: {e}')
            continue
        try:
            new_content, _ = masker.apply_ops(content, item.get('ops', []),
                                              collect=False)
            if new_content != content:
                filepath.write_text(new_content, encoding='utf-8')
                applied_files += 1
        except Exception as e:
            errors.append(f'{item["file"]}: {e}')

    for item in preview_data.get('filename_changes', []):
        old_path = base / item['old']
        new_path = base / item['new']
        if not old_path.exists():
            continue
        parent = new_path.parent
        if not parent.is_dir():
            parent.mkdir(parents=True, exist_ok=True)
        try:
            old_path.rename(new_path)
            applied_renames += 1
        except Exception as e:
            errors.append(f'{item["old"]}: {e}')

    return {
        'applied_files': applied_files,
        'applied_renames': applied_renames,
        'errors': errors,
    }


def _write_office_inplace(filepath: Path, cell_changes: dict):
    """原地改写 Office 文件：先写临时文件，成功后再替换。"""
    import os
    import tempfile
    tmp_dir = filepath.parent
    fd, tmp_name = tempfile.mkstemp(dir=str(tmp_dir), suffix=filepath.suffix)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        office_io.write_units(filepath, tmp_path, cell_changes)
        os.replace(str(tmp_path), str(filepath))
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/field-library')
def field_library():
    return jsonify({
        'library': FIELD_LIBRARY,
        'default_field_ids': masker.default_field_ids(FIELD_LIBRARY),
    })


@app.route('/api/aliases')
def aliases():
    return jsonify(entity.load_aliases())


@app.route('/api/scan', methods=['POST'])
def scan():
    config = build_config(request.json or {})
    return jsonify(scan_directory(config))


@app.route('/api/apply', methods=['POST'])
def apply():
    data = request.json or {}
    config = build_config(data)
    preview_data = data.get('preview_data', {})
    if not preview_data:
        return jsonify({'error': '请先执行预览扫描'})
    return jsonify(apply_changes(config, preview_data))


def find_free_port(preferred=5000, attempts=30):
    # 不设 SO_REUSEADDR：否则在 Windows 上即使端口已被占用也能绑定成功，
    # 会与已有服务抢占同一端口。
    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
            except OSError:
                continue
            return port
    return preferred


def _setup_console():
    """让中文在任意 Windows 控制台下都能正常显示。"""
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def main():
    _setup_console()
    port = find_free_port()
    url = f'http://127.0.0.1:{port}'
    print('=' * 58)
    print('  文件脱敏工具  |  本地离线运行，不联网、不调用任何模型')
    print('=' * 58)
    print(f'  访问地址: {url}')
    print('  关闭服务: 在本窗口按 Ctrl+C')
    print('=' * 58)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
