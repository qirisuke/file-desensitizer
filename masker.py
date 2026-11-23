"""脱敏引擎。

统一构建「操作列表」，扫描阶段与写入阶段调用同一份 apply_ops，
保证预览结果与实际落盘结果完全一致。

性能说明（行为与旧实现 bit-for-bit 等价）：
- 正则在首次使用时编译并缓存，避免每条规则对每篇文本重复 re.compile。
- 用 subn 取代 findall + sub，把「每条规则扫全文 2 遍」降为 1 遍；
  subn 返回的替换次数与 findall 的结果长度完全一致（含带捕获组的模式）。
- word 类型（纯字面量）在大小写敏感时直接用 str.replace / str.count，
  语义与 re.escape + 替换完全一致，且快一个数量级。
- 字面量替换一律走 lambda（返回常量），因此替换文本中的反斜杠不会被解释；
  标签型规则（to 含 \\1）仍是字符串替换模板，保留 re.sub 的反向引用语义。
"""
import json
import re
from functools import lru_cache
from pathlib import Path

DEFAULT_MASK = '***'
DEFAULT_FILENAME_MASK = '已脱敏'

# Windows 文件名非法字符
ILLEGAL_FILENAME_CHARS = '<>:"/\\|?*'

MODE_REPLACE = 'replace'
MODE_MASK = 'mask'
MODE_COMBO = 'combo'
VALID_MODES = (MODE_REPLACE, MODE_MASK, MODE_COMBO)

# 掩码模板引用了捕获组 => 标签型规则（预编译，避免逐字段编译）
_LABELED_RE = re.compile(r'\\\d')


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
    return bool(_LABELED_RE.search(field.get('mask', '')))


@lru_cache(maxsize=4096)
def _compile_cached(pattern: str, flags: int):
    """带缓存的正则编译：同一个 (pattern, flags) 只编译一次。

    返回 None 表示该模式非法（与旧实现 re.error 分支等价）。
    """
    try:
        return re.compile(pattern, flags)
    except re.error:
        return None


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


def _apply_word(text: str, op: dict, target: str):
    """word 类型：字面量替换。

    - 大小写敏感：用 str.replace / str.count，与 re.escape + 替换完全等价。
    - 大小写不敏感：走 IGNORECASE 正则，替换值仍按 lambda 语义（反斜杠不解释）。
    返回 (新文本, 命中次数)。
    """
    source = op['from']
    if not op.get('ci'):
        count = text.count(source)
        if not count:
            return text, 0
        return text.replace(source, target), count

    pattern = _compile_cached(re.escape(source), re.IGNORECASE)
    if pattern is None:
        return text, 0
    new_text, count = pattern.subn(lambda _m, lit=target: lit, text)
    return new_text, count


def apply_ops(text: str, ops: list, for_filename: bool = False,
              collect: bool = True, matched_only: bool = False,
              protect_terms: list = None) -> tuple:
    """执行操作列表，返回 (新文本, 每项命中次数)。

    collect=False 时只返回 (新文本, None)，跳过统计明细的构造。
    用于「只关心最终文本」的调用方（如落盘阶段），省去每条规则一次的
    字典拷贝。默认 collect=True，行为与旧实现完全一致。

    matched_only=True 时只保留命中次数 > 0 的条目（省去未命中规则的字典拷贝）。

    protect_terms：受保护的文本片段（如实体泛化后的「人员A」）。
    若某条规则的命中内容包含其中任一片段，则跳过该规则 ——
    避免已泛化的实体被字段规则二次遮蔽成「***」。
    """
    results = [] if collect else None
    protect_terms = protect_terms or []

    for op in ops:
        target = op.get('filename_to', op['to']) if for_filename else op['to']
        # 保护区间每条规则前重算：前面的规则会改变文本长度，
        # 缓存下来的位置会失效，导致后续规则被误判为「覆盖受保护片段」。
        protected_spans = []
        for term in protect_terms:
            i = text.find(term)
            if i >= 0:
                protected_spans.append((i, i + len(term)))

        if op['type'] == 'word':
            if protect_terms and op['from'] in text:
                continue
            text, count = _apply_word(text, op, target)
        else:
            flags = re.IGNORECASE if op.get('ci') else 0
            pattern = _compile_cached(op['pattern'], flags)
            if pattern is None:
                if collect and not matched_only:
                    results.append({**op, 'count': 0})
                continue
            if protected_spans:
                skip = False
                for m in pattern.finditer(text):
                    if any(m.start() < pe and ps < m.end()
                           for ps, pe in protected_spans):
                        skip = True
                        break
                if skip:
                    if collect and not matched_only:
                        results.append({**op, 'count': 0})
                    continue
            text, count = pattern.subn(target, text)
        if collect and (count > 0 or not matched_only):
            results.append({**op, 'count': count})
    return text, results


def ops_for_filename(ops: list) -> list:
    return [op for op in ops if op.get('apply_to_filename')]
