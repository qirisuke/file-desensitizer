"""集成处理流水线。

把「实体泛化 + 敏感字段库 + 表格语义」串成一条路径，
所有文件格式（txt / csv / md / xlsx / docx）都走这里。

关键设计
--------
- **扫描与落盘共用同一路径**：先算出每个单元的新文本（预览），落盘时直接写这份结果。
- **表格语义**：xlsx / csv 里标签与值常常分列分行（表头是「授信额度」，值在下一列），
  单靠正则匹配不到。表格语义引擎负责识别「哪些列/单元格该遮蔽」。
- **实体映射全局共享**：整个目录树一个 EntityMapper，保证跨文件泛化名一致。
"""
import re
from pathlib import Path

import masker
import office_io

# 表格里表示「数值」的列 —— 与首列的指标名配合使用
VALUE_COLUMN_HINTS = ('本年累计', '上年同期', '本年', '上年', '本期', '上期',
                       '金额', '余额', '额度', '数值', '数量', '值')

# 表格里表示「变动率」的列 —— 期望结果里这些不遮蔽
DELTA_COLUMN_HINTS = ('同比', '环比', '变动', '增幅', '增幅率', '变化', '增减')


def _clean_header(h):
    return (h or '').strip()


class Pipeline:
    """一次扫描/落盘任务的处理器。"""

    def __init__(self, config: dict, library: dict, mapper=None):
        self.config = config
        self.library = library
        self.mapper = mapper
        if self.mapper is None and config.get('generalize_entities', True):
            aliases = config.get('aliases') or entity.load_aliases()
            self.mapper = entity.EntityMapper(aliases)
        self.ops = masker.build_ops({**config, 'library': library})
        self.filename_ops = masker.ops_for_filename(self.ops)
        self.mode = config.get('mode', masker.MODE_REPLACE)
        self.mask = config.get('mask') or masker.DEFAULT_MASK
        self.include_filenames = config.get('include_filenames', True)
        self.include_contents = config.get('include_contents', True)
        # 表头本身是敏感字段名时，该列的值整列遮蔽
        self._column_mask_rules = self._build_column_rules()

    # ------------------------------------------------------ 列级规则

    def _build_column_rules(self):
        """从字段库里抽取「标签词」，做成 标签 -> 字段id 的映射。

        标签词 = 标签型规则的第一个捕获组里的交替项，例如
        `(统一社会信用代码|社会信用代码|信用代码)[ \t]*[:：=]...` 抽出三个词。
        这些词用于精确判断「某个单元格是不是一个敏感字段名」。
        """
        rules = []
        tag_re = re.compile(r'^\(([^)]*)\)')
        for category in self.library.get('categories', []):
            for field in category.get('fields', []):
                if '\\1' not in field.get('mask', ''):
                    continue
                for label in field.get('column_labels') or []:
                    rules.append((label, field['id']))
                m = tag_re.match(field['pattern'])
                if m:
                    for word in m.group(1).split('|'):
                        word = word.strip()
                        # 去掉分支里的正则片段（如 (?!级)）
                        word = re.sub(r'\(\?![^)]*\)', '', word).strip()
                        if word and re.fullmatch(r'[一-龥A-Za-z0-9 ]{2,12}', word):
                            rules.append((word, field['id']))
        return rules

    def _label_word_matches(self, text: str):
        """文本是否精确等于某个敏感字段的标签词。返回字段 id 或 None。"""
        t = (text or '').strip()
        if not t:
            return None
        for label, fid in self._column_mask_rules:
            if t == label:
                return fid
        return None

    def _column_mask_for(self, header: str):
        """返回该表头对应的敏感字段 id（命中则整列遮蔽）。"""
        h = _clean_header(header)
        if not h:
            return None
        for label, fid in self._column_mask_rules:
            if h == label or (len(label) >= 3 and label in h):
                return fid
        return None

    # ------------------------------------------------------ 单元格处理

    def _transform_text(self, text: str, context: str = '', prev_context: str = ''):
        """对一段文本做脱敏。返回 (新文本, 命中明细)。"""
        if not text:
            return text, []

        # 1) 实体泛化（先做，避免实体名被字段规则遮蔽掉）
        protect = []
        if self.mapper is not None and self.mode in (masker.MODE_MASK, masker.MODE_COMBO):
            text = self.mapper.map_text(text, context, prev_context)
            # 已泛化的实体名不再被字段规则二次遮蔽
            protect = [g for g in self.mapper.generic_to_entity
                       if g and g in text]
        # 文本里已存在的泛化形态词（如上一轮留下的「人员A」）同样受保护 ——
        # 否则「法定代表人：人员A」会被字段规则吃掉「人员」变成「***A」
        for m in self._GENERIC_ANY_RE.finditer(text):
            protect.append(m.group(0))

        # 2) 敏感字段 / 关键词规则
        text, matched = masker.apply_ops(text, self.ops, collect=True,
                                         matched_only=True,
                                         protect_terms=protect or None)

        return text, matched

    # ------------------------------------------------------ 标签驱动的值处理

    _GENERIC_RE = re.compile(r'^(客户|供应商|人员)[A-Z]$')
    _GENERIC_ANY_RE = re.compile(r'(客户|供应商|人员)[A-Z]')

    def _mask_by_label(self, label: str, value: str):
        """表格里「标签 | 值」分列时，用标签触发对应的遮蔽规则。

        做法：构造 `标签｜值` 的探针交给规则匹配，再取回值部分。
        复用现有规则，不额外维护一套。
        返回 (新值, 命中明细列表)
        """
        if not label or not value:
            return value, []
        if self._GENERIC_RE.match(value.strip()):
            return value, []      # 已是泛化名，不再遮蔽
        probe = f'{label}：{value}'
        masked, matched = masker.apply_ops(probe, self.ops, collect=True, matched_only=True)
        if '：' not in masked:
            return value, []
        new_value = masked.rsplit('：', 1)[1]
        if new_value == value:
            return value, []
        return new_value, matched

    def _transform_cell(self, value: str, label: str, context: str = ''):
        """处理表格里的一个单元格：先按标签做实体泛化，再按标签做遮蔽。"""
        if not value:
            return value, []
        matched = []
        new_value = value

        # 1) 标签驱动的实体泛化
        if self.mapper is not None and self.mode in (masker.MODE_MASK, masker.MODE_COMBO):
            if label:
                mapped = self.mapper.map_value(new_value, label)
                if mapped != new_value:
                    new_value = mapped
                    matched.append({
                        'type': 'entity', 'label': label, 'count': 1,
                        'source': 'entity',
                    })

        # 2) 标签驱动的遮蔽
        if label and not self._GENERIC_RE.match(new_value.strip()):
            masked_value, m = self._mask_by_label(label, new_value)
            if masked_value != new_value:
                new_value = masked_value
                matched.extend(m)

        # 3) 常规规则（值里自带标签时）
        new_value, m = self._transform_text(new_value, context)
        matched.extend(m)
        return new_value, matched

    # ------------------------------------------------------ 表格处理

    def _process_table(self, header, rows):
        """处理一个表格。header 是列名列表，rows 是二维数组。

        返回 (新表头, 新行列表, 命中明细列表)
        """
        header = [_clean_header(h) for h in header]
        column_mask = [self._column_mask_for(h) for h in header]

        value_cols = [i for i, h in enumerate(header)
                      if any(k in h for k in VALUE_COLUMN_HINTS)]
        delta_cols = [i for i, h in enumerate(header)
                      if any(k in h for k in DELTA_COLUMN_HINTS)]

        new_rows = []
        all_matched = []
        for row in rows:
            new_row = list(row)
            row_matched = []

            # 行级规则：本行任意单元格是敏感字段名（如「存款余额」）时，
            # 同行里的数值列一并遮蔽。指标名不一定在首列，所以逐列找。
            row_labels = [(ci, row[ci]) for ci in range(len(row))
                          if ci < len(row) and row[ci]
                          and self._row_label_is_sensitive(row[ci])]
            row_label_text = row_labels[0][1] if row_labels else ''
            row_label_is_first = bool(row_labels) and row_labels[0][0] == 0

            for ci, value in enumerate(row):
                if ci >= len(header):
                    break
                col = header[ci]
                ctx = col
                if row_label_text:
                    ctx = f'{col} 行标签={row_label_text}'

                # 标签优先级：
                #   第0列就是敏感字段名 -> 该行其余列都是这个字段的值（行标签语义）
                #   否则用列名（列标签语义）
                if row_label_is_first and ci != 0:
                    cell_label = row_label_text
                else:
                    cell_label = col

                # 表头本身是敏感字段 -> 整列遮蔽
                if (column_mask[ci] and value and not row_label_is_first
                        and not (row_labels and ci == row_labels[0][0])):
                    new_row[ci] = self.mask
                    row_matched.append({
                        'type': 'column', 'label': col, 'count': 1,
                        'source': 'builtin', 'field_id': column_mask[ci],
                    })
                    continue

                # 行标签命中敏感字段 + 本列是数值列 -> 遮蔽
                if (row_labels and not row_label_is_first
                        and ci in value_cols and ci not in delta_cols
                        and value and ci not in [c for c, _ in row_labels]):
                    new_row[ci] = self.mask
                    row_matched.append({
                        'type': 'row', 'label': f'{row_label_text}/{col}', 'count': 1,
                        'source': 'builtin',
                    })
                    continue

                new_value, matched = self._transform_cell(value, cell_label, ctx)
                new_row[ci] = new_value
                row_matched.extend(matched)

            new_rows.append(new_row)
            all_matched.extend(row_matched)

        return header, new_rows, all_matched

    def _process_row_label_table(self, rows):
        """处理「行首是标签」的表格（如 docx 的「合作要素|内容」表）。

        行首单元是标签时，该行后续单元按标签规则处理。
        """
        if not rows:
            return rows, []
        header = rows[0]
        new_rows = []
        all_matched = []
        for row in rows:
            new_row = list(row)
            if not row:
                new_rows.append(new_row)
                continue
            label = _clean_header(row[0])
            for ci in range(1, len(row)):
                value = row[ci]
                if not value:
                    continue
                ctx = f'{header[ci] if ci < len(header) else ""} 行标签={label}'
                # 行标签本身是敏感字段 -> 该行整行遮蔽
                if self._row_label_is_sensitive(label):
                    new_row[ci] = self.mask
                    all_matched.append({
                        'type': 'row', 'label': f'{label}/{ci}', 'count': 1,
                        'source': 'builtin',
                    })
                    continue
                new_value, matched = self._transform_cell(value, label, ctx)
                new_row[ci] = new_value
                all_matched.extend(matched)
            new_rows.append(new_row)
        return new_rows, all_matched

    def _row_label_is_sensitive(self, label: str) -> bool:
        """这个单元格的值是不是一个敏感字段名（如「存款余额」「授信额度」）。

        用**精确匹配**而不是子串包含 —— 「客户总数」含「客户」但它是个指标名，
        不是字段名，用子串会把整行值都误当客户名处理。
        """
        if not label:
            return False
        return self._label_word_matches(label) is not None

    # ------------------------------------------------------ 文件处理

    def process_plain_text(self, text: str):
        """处理纯文本（txt / md）。markdown 表格做表格感知处理。"""
        lines = text.splitlines(keepends=True)
        out_lines = []
        matched = []
        prev_ctx = ''
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            # markdown 表格：当前行含 | 且下一行是分隔行
            if ('|' in line and i + 1 < n
                    and re.match(r'^\s*\|?[\s:|-]+\|[\s:|-]*$', lines[i + 1])):
                block, i, m = self._consume_markdown_table(lines, i)
                out_lines.extend(block)
                matched.extend(m)
                continue

            new_line, m = self._transform_text(line, '', prev_ctx)
            matched.extend(m)
            out_lines.append(new_line)
            # 段落级上下文延续：出现「客户名单/供应商名单」时影响后续行
            if '供应商名单' in line:
                prev_ctx = '供应商名单'
            elif '客户名单' in line:
                prev_ctx = '客户名单'
            i += 1
        return ''.join(out_lines), matched

    def _consume_markdown_table(self, lines, start):
        """处理一段 markdown 表格，返回 (输出行列表, 下一行索引, 命中明细)。"""
        n = len(lines)
        header_line = lines[start]
        cells = _split_md_row(header_line)
        body = []
        body_lines = []
        j = start + 2          # 跳过表头与分隔行
        while j < n and '|' in lines[j]:
            body.append(_split_md_row(lines[j]))
            body_lines.append(lines[j])
            j += 1

        new_header, new_rows, matched = self._process_table(cells, body)

        out = [_join_md_row(new_header, _line_ending(header_line))]
        out.append(lines[start + 1])        # 分隔行原样保留
        for row, src in zip(new_rows, body_lines):
            out.append(_join_md_row(row, _line_ending(src)))
        return out, j, matched


    def process_csv(self, text: str):
        """处理 CSV：按行解析，表头是第一行。"""
        import csv
        import io
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            return text, []
        header = rows[0]
        body = rows[1:]
        new_header, new_rows, matched = self._process_table(header, body)
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator='\n')
        writer.writerow(new_header)
        for r in new_rows:
            writer.writerow(r)
        return buf.getvalue(), matched

    def process_xlsx(self, path):
        """处理 xlsx：按 sheet 读表头 + 行，产出新的单元值。"""
        units = office_io.read_units(path)
        # 还原成 {sheet: {行: {列: 文本}}}
        sheets = {}
        for unit in units:
            _kind, name, ri, ci, text = unit
            rows = sheets.setdefault(name, {})
            cols = rows.setdefault(ri, {})
            cols[ci] = text

        changes = {}
        all_matched = []
        for name, rows in sheets.items():
            max_ri = max(rows) if rows else -1
            max_ci = max((max(c) for c in rows.values()), default=-1)
            grid = []
            for ri in range(max_ri + 1):
                cols = rows.get(ri, {})
                grid.append([cols.get(ci, '') for ci in range(max_ci + 1)])

            if not grid:
                continue
            header = grid[0]
            body = grid[1:]
            new_header, new_rows, matched = self._process_table(header, body)
            all_matched.extend(matched)

            for ci, text in enumerate(new_header):
                changes[(name, 0, ci)] = text
            for ri, row in enumerate(new_rows):
                for ci, text in enumerate(row):
                    changes[(name, ri + 1, ci)] = text

        return changes, all_matched

    def process_docx(self, path):
        """处理 docx：段落逐个处理，表格按「列标签」或「行标签」两种语义处理。"""
        units = office_io.read_units(path)
        tables = office_io.read_docx_tables(path)
        changes = {}
        all_matched = []

        # 段落
        prev_ctx = ''
        for unit in units:
            if unit[0] != 'p':
                continue
            key = office_io.unit_key(unit)
            text = unit[-1]
            new_text, matched = self._transform_text(text, '', prev_ctx)
            changes[key] = new_text
            all_matched.extend(matched)
            if '供应商名单' in text:
                prev_ctx = '供应商名单'
            elif '客户名单' in text:
                prev_ctx = '客户名单'

        # 表格
        for ti, rows in enumerate(tables):
            if not rows:
                continue
            header = rows[0]
            body = rows[1:]
            # 判断这张表是「列标签」还是「行标签」语义
            col_semantic = self._table_is_column_semantic(header, body)
            if col_semantic:
                new_header, new_rows, matched = self._process_table(header, body)
                new_rows = [new_header] + new_rows
            else:
                new_rows, matched = self._process_row_label_table(rows)
            all_matched.extend(matched)
            for ri, row in enumerate(new_rows):
                for ci, text in enumerate(row):
                    changes[('c', ti, ri, ci)] = text

        return changes, all_matched

    @staticmethod
    def _table_is_column_semantic(header, body) -> bool:
        """表头是字段名 -> 列语义；表头是「项目|内容」这类 -> 行语义。"""
        if not header:
            return False
        # 表头第二列若是「内容」「值」这类占位词，说明行首才是标签
        for h in header[1:]:
            if h.strip() in ('内容', '值', '说明', '备注'):
                return False
        return True


def _split_md_row(line: str):
    """拆分 markdown 表格行。"""
    s = line.strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|'):
        s = s[:-1]
    return [c.strip() for c in s.split('|')]


def _line_ending(line: str) -> str:
    """取出原行的换行符，保持文件风格一致。"""
    for ending in ('\r\n', '\n', '\r'):
        if line.endswith(ending):
            return ending
    return ''


def _join_md_row(cells, ending='\n'):
    """拼回 markdown 表格行。"""
    return '| ' + ' | '.join(cells) + ' |' + ending

