"""脱敏引擎。

统一构建「操作列表」，扫描阶段与写入阶段调用同一份 apply_ops，
保证预览结果与实际落盘结果完全一致。
"""
import json
import re
from pathlib import Path

DEFAULT_MASK = '***'
DEFAULT_FILENAME_MASK = '已脱敏'

# Windows 文件名非法字符
ILLEGAL_FILENAME_CHARS = '<>:"/\\|?*'

MODE_REPLACE = 'replace'
MODE_MASK = 'mask'
MODE_COMBO = 'combo'
VALID_MODES = (MODE_REPLACE, MODE_MASK, MODE_COMBO)


def sanitize_filename_mask(mask: str) -> str:
    """把掩码转成 Windows 文件名可用的形式。"""
    cleaned = ''.join(
        '_' if (c in ILLEGAL_FILENAME_CHARS or ord(c) < 32) else c
        for c in (mask or '')
    )
    cleaned = cleaned.strip(' .')
    return cleaned or '脱敏'


def load_field_library(path) -> dict:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {'categories': []}


def _field_default_enabled(field: dict) -> bool:
    return bool(field.get('default_enabled', True))


def iter_library_fields(library: dict):
    """产出 (category, field)。"""
    for category in library.get('categories', []):
        for field in category.get('fields', []):
            yield category, field


def default_field_ids(library: dict) -> list:
    ids = []
    for category, field in iter_library_fields(library):
        if category.get('default_enabled', True) and _field_default_enabled(field):
            ids.append(field['id'])
    return ids


def _is_labeled_rule(field: dict) -> bool:
    """掩码模板引用了捕获组 => 标签型规则，需先于纯值型执行。"""
    return bool(re.search(r'\\\d', field.get('mask', '')))


def build_ops(config: dict) -> list:
    """按固定顺序构建操作列表。

    执行顺序：敏感词表 -> 内置字段规则(标签型) -> 内置字段规则(纯值型) -> 关键词替换
    每个操作同时带 to（内容掩码）与 filename_to（文件名掩码）。
    """
    mode = config.get('mode', MODE_REPLACE)
    mask = config.get('mask') or DEFAULT_MASK
    filename_mask = sanitize_filename_mask(config.get('filename_mask') or DEFAULT_FILENAME_MASK)
    case_sensitive = config.get('case_sensitive', True)
    library = config.get('library', {})
    selected = set(config.get('field_ids') or [])

    word_ops = []
    field_labeled = []
    field_value = []
    keyword_ops = []

    if mode in (MODE_MASK, MODE_COMBO):
        for word in config.get('word_list', []):
            word = (word or '').strip()
            if word:
                word_ops.append({
                    'type': 'word',
                    'from': word,
                    'to': mask,
                    'filename_to': filename_mask,
                    'ci': not case_sensitive,
                    'label': word,
                    'source': 'wordlist',
                    'apply_to_filename': True,
                })

        for _category, field in iter_library_fields(library):
            if field['id'] not in selected:
                continue
            op = {
                'type': 'regex',
                'pattern': field['pattern'],
                'to': field.get('mask') or mask,
                'filename_to': filename_mask,
                'ci': bool(field.get('ci', False)),
                'label': field.get('label', field['id']),
                'source': 'builtin',
                'field_id': field['id'],
                'apply_to_filename': bool(field.get('apply_to_filename', False)),
            }
            (field_labeled if _is_labeled_rule(field) else field_value).append(op)

    if mode in (MODE_REPLACE, MODE_COMBO):
        for rule in config.get('rules', []):
            keyword = (rule.get('keyword') or '').strip()
            if keyword:
                replacement = rule.get('replacement', '')
                keyword_ops.append({
                    'type': 'word',
                    'from': keyword,
                    'to': replacement,
                    'filename_to': replacement,
                    'ci': False,
                    'label': keyword,
                    'source': 'keyword',
                    'apply_to_filename': True,
                })

    return word_ops + field_labeled + field_value + keyword_ops


def apply_ops(text: str, ops: list, for_filename: bool = False) -> tuple:
    """执行操作列表，返回 (新文本, 每项命中次数)。"""
    results = []
    for op in ops:
        flags = re.IGNORECASE if op.get('ci') else 0
        target = op.get('filename_to', op['to']) if for_filename else op['to']
        count = 0
        if op['type'] == 'word':
            try:
                pattern = re.compile(re.escape(op['from']), flags)
            except re.error:
                results.append({**op, 'count': 0})
                continue
            count = len(pattern.findall(text))
            if count:
                text = pattern.sub(lambda _m, lit=target: lit, text)
        else:
            try:
                pattern = re.compile(op['pattern'], flags)
            except re.error:
                results.append({**op, 'count': 0})
                continue
            count = len(pattern.findall(text))
            if count:
                text = pattern.sub(target, text)
        results.append({**op, 'count': count})
    return text, results


def ops_for_filename(ops: list) -> list:
    return [op for op in ops if op.get('apply_to_filename')]
