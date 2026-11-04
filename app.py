import sys
import json
import socket
import threading
import webbrowser
from pathlib import Path

from flask import Flask, request, jsonify, render_template

import masker

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
    if filepath.suffix.lower() in BINARY_EXTENSIONS:
        return False
    if filepath.suffix.lower() in TEXT_EXTENSIONS:
        return True
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
    }


def scan_directory(config: dict) -> dict:
    base = Path(config['directory'])
    if not config['directory']:
        return {'error': '请输入目标目录'}
    if not base.exists() or not base.is_dir():
        return {'error': f'目录不存在: {config["directory"]}'}

    ops = masker.build_ops({**config, 'library': FIELD_LIBRARY})
    if not ops:
        return {'error': '请至少配置一条替换规则，或在「敏感词屏蔽」中启用字段'}

    filename_ops = masker.ops_for_filename(ops)

    walker = base.rglob('*') if config['recursive'] else base.glob('*')
    text_files = [f for f in walker if f.is_file() and is_text_file(f)]

    filename_changes = []
    content_changes = []
    samples = []

    for filepath in text_files:
        rel_path = filepath.relative_to(base)

        if config['include_filenames'] and filename_ops:
            new_name, _ = masker.apply_ops(filepath.name, filename_ops, for_filename=True)
            new_name = safe_filename(new_name)
            if new_name != filepath.name:
                filename_changes.append({
                    'old': str(rel_path),
                    'new': str(rel_path.parent / new_name),
                    'ops': [op for op in filename_ops],
                })

        if config['include_contents']:
            try:
                content = filepath.read_text(encoding='utf-8')
            except (UnicodeDecodeError, PermissionError, OSError):
                continue
            new_content, results = masker.apply_ops(content, ops)
            if new_content != content:
                matched = [r for r in results if r['count'] > 0]
                content_changes.append({
                    'file': str(rel_path),
                    'ops': matched,
                })
                if len(samples) < SAMPLE_LIMIT:
                    samples.append({
                        'file': str(rel_path),
                        'sample': new_content[:SAMPLE_CHARS],
                    })

    return {
        'scanned_files': len(text_files),
        'filename_changes': filename_changes,
        'content_changes': content_changes,
        'samples': samples,
        'total_filename_changes': len(filename_changes),
        'total_content_changes': sum(
            sum(r['count'] for r in c['ops'])
            for c in content_changes
        ),
        'matched_files': len(content_changes),
    }


def apply_changes(directory: str, preview_data: dict) -> dict:
    base = Path(directory)
    applied_files = 0
    applied_renames = 0
    errors = []

    for item in preview_data.get('content_changes', []):
        filepath = base / item['file']
        if not filepath.exists():
            continue
        try:
            content = filepath.read_text(encoding='utf-8')
            new_content, _ = masker.apply_ops(content, item.get('ops', []))
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
        new_path.parent.mkdir(parents=True, exist_ok=True)
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


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/field-library')
def field_library():
    return jsonify({
        'library': FIELD_LIBRARY,
        'default_field_ids': masker.default_field_ids(FIELD_LIBRARY),
    })


@app.route('/api/scan', methods=['POST'])
def scan():
    config = build_config(request.json or {})
    return jsonify(scan_directory(config))


@app.route('/api/apply', methods=['POST'])
def apply():
    data = request.json or {}
    directory = _clean_directory(data.get('directory', ''))
    preview_data = data.get('preview_data', {})
    if not preview_data:
        return jsonify({'error': '请先执行预览扫描'})
    return jsonify(apply_changes(directory, preview_data))


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
