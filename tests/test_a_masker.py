"""A 组：脱敏引擎 masker 单元测试。

覆盖要点：
  1. build_ops 的操作顺序（word_ops -> 标签型字段 -> 纯值型字段 -> keyword_ops）
  2. mode 语义（replace / mask / combo）
  3. apply_ops 基本替换与 count 统计
  4. 正则替换不吞换行符（行数守恒）
  5. 捕获组反向引用 \\1 \\2 回填
  6. for_filename=True 时使用 filename_to
  7. ci（大小写不敏感）标志
  8. case_sensitive=False 时 word op 的 ci=True
  9. 非法正则不崩溃，count 为 0
 10. sanitize_filename_mask 各种边界
 11. load_field_library 三种情况
 12. default_field_ids 只返回 default_enabled 的字段
 13. ops_for_filename 只保留 apply_to_filename=True
"""
import json
import tempfile
import unittest
from pathlib import Path

from .helpers import load_library, PROJECT_ROOT
import masker


# 构造一个最小字段库：1 个标签型规则（mask 含 \1）+ 1 个纯值型规则
MINI_LIBRARY = {
    'categories': [
        {
            'id': 'cat',
            'name': '测试分类',
            'default_enabled': True,
            'fields': [
                {
                    'id': 'labeled',
                    'label': '标签型',
                    'pattern': r'(联系人)[ \t]*[:：]?[ \t]*(\S+)',
                    'mask': r'\1：***',
                },
                {
                    'id': 'plain',
                    'label': '纯值型',
                    'pattern': r'(?<![0-9])1[3-9]\d{9}(?![0-9])',
                    'mask': '***',
                    'apply_to_filename': True,
                },
            ],
        }
    ]
}


def _op(type_, source, **extra):
    op = {'type': type_, 'source': source}
    op.update(extra)
    return op


class TestBuildOpsOrder(unittest.TestCase):
    """A1: build_ops 的操作顺序。"""

    def setUp(self):
        self.config = {
            'mode': masker.MODE_COMBO,
            'word_list': ['星海项目'],
            'field_ids': ['labeled', 'plain'],
            'rules': [{'keyword': '张三丰', 'replacement': '张某'}],
            'library': MINI_LIBRARY,
            'mask': '***',
            'case_sensitive': True,
        }

    def test_full_order_word_labeled_value_keyword(self):
        ops = masker.build_ops(self.config)
        signature = [(op['type'], op['source']) for op in ops]
        self.assertEqual(
            signature,
            [
                ('word', 'wordlist'),
                ('regex', 'builtin'),   # labeled（标签型）
                ('regex', 'builtin'),   # plain（纯值型）
                ('word', 'keyword'),
            ],
        )

    def test_labeled_field_precedes_plain_field(self):
        ops = masker.build_ops(self.config)
        field_ids = [op['field_id'] for op in ops if op['source'] == 'builtin']
        self.assertEqual(field_ids, ['labeled', 'plain'])

    def test_labeled_and_value_groups_are_separated(self):
        """标签型规则全部排在纯值型规则之前（分组而非交错）。"""
        ops = masker.build_ops(self.config)
        builtin = [op for op in ops if op['source'] == 'builtin']
        # 按 mask 模板（含 \1 反向引用）判定标签型
        labeled_flags = [
            masker._is_labeled_rule({'mask': op['to']}) for op in builtin
        ]
        # 标签型在前、纯值型在后，不得交错
        self.assertEqual(labeled_flags, sorted(labeled_flags, reverse=True))
        self.assertEqual(labeled_flags, [True, False])

    def test_op_contains_expected_metadata(self):
        ops = masker.build_ops(self.config)
        for op in ops:
            self.assertIn('to', op)
            self.assertIn('filename_to', op)
            self.assertIn('ci', op)
            self.assertIn('apply_to_filename', op)

    def test_unselected_field_ids_are_skipped(self):
        config = dict(self.config, field_ids=['plain'])
        ops = masker.build_ops(config)
        field_ids = [op.get('field_id') for op in ops if op['source'] == 'builtin']
        self.assertEqual(field_ids, ['plain'])

    def test_empty_word_and_rules_are_ignored(self):
        config = dict(self.config, word_list=['', '   ', None], rules=[{'keyword': '  '}])
        ops = masker.build_ops(config)
        self.assertEqual([op['source'] for op in ops], ['builtin', 'builtin'])


class TestBuildOpsModes(unittest.TestCase):
    """A2: mode 语义。"""

    def setUp(self):
        self.base = {
            'word_list': ['星海项目'],
            'field_ids': ['labeled', 'plain'],
            'rules': [{'keyword': '张三丰', 'replacement': '张某'}],
            'library': MINI_LIBRARY,
            'mask': '***',
        }

    def _sources(self, mode):
        ops = masker.build_ops(dict(self.base, mode=mode))
        return [op['source'] for op in ops]

    def test_replace_mode_only_keyword(self):
        sources = self._sources(masker.MODE_REPLACE)
        self.assertEqual(sources, ['keyword'])
        self.assertNotIn('wordlist', sources)
        self.assertNotIn('builtin', sources)

    def test_mask_mode_has_word_and_fields_no_keyword(self):
        sources = self._sources(masker.MODE_MASK)
        self.assertIn('wordlist', sources)
        self.assertIn('builtin', sources)
        self.assertNotIn('keyword', sources)
        self.assertEqual(sources, ['wordlist', 'builtin', 'builtin'])

    def test_combo_mode_has_all_three(self):
        sources = self._sources(masker.MODE_COMBO)
        self.assertEqual(sources, ['wordlist', 'builtin', 'builtin', 'keyword'])

    def test_replace_mode_keyword_uses_replacement_not_mask(self):
        ops = masker.build_ops(dict(self.base, mode=masker.MODE_REPLACE))
        self.assertEqual(ops[0]['to'], '张某')
        self.assertEqual(ops[0]['filename_to'], '张某')

    def test_mask_mode_word_op_uses_mask(self):
        ops = masker.build_ops(dict(self.base, mode=masker.MODE_MASK))
        word_op = next(op for op in ops if op['source'] == 'wordlist')
        self.assertEqual(word_op['to'], '***')

    def test_invalid_mode_produces_no_ops(self):
        """已知行为差异（见报告 Defect D1）：build_ops 不校验 mode。

        app.build_config 会把非法 mode 兜底为 'replace'，但直接调用
        masker.build_ops 时非法 mode 会让所有分支都不命中 -> 返回空操作列表。
        这里如实记录当前实际行为。
        """
        ops = masker.build_ops(dict(self.base, mode='not-a-mode'))
        self.assertEqual(ops, [])

    def test_missing_mode_defaults_to_replace(self):
        ops = masker.build_ops(dict(self.base))
        self.assertEqual([op['source'] for op in ops], ['keyword'])


class TestApplyOpsBasics(unittest.TestCase):
    """A3: apply_ops 基本替换与 count 统计。"""

    def test_simple_word_replacement_and_count(self):
        ops = [{'type': 'word', 'from': 'abc', 'to': '***', 'filename_to': '***',
                'ci': False, 'apply_to_filename': True}]
        text, results = masker.apply_ops('abc abc\nabc', ops)
        self.assertEqual(text, '*** ***\n***')
        self.assertEqual(results[0]['count'], 3)

    def test_count_is_zero_when_no_match(self):
        ops = [{'type': 'word', 'from': 'zzz', 'to': '***', 'filename_to': '***',
                'ci': False, 'apply_to_filename': True}]
        text, results = masker.apply_ops('hello', ops)
        self.assertEqual(text, 'hello')
        self.assertEqual(results[0]['count'], 0)

    def test_results_length_matches_ops_length(self):
        ops = [
            {'type': 'word', 'from': 'a', 'to': '1', 'filename_to': '1', 'ci': False},
            {'type': 'regex', 'pattern': 'b', 'to': '2', 'filename_to': '2', 'ci': False},
        ]
        _, results = masker.apply_ops('ab', ops)
        self.assertEqual(len(results), 2)

    def test_results_preserve_op_fields_plus_count(self):
        ops = [{'type': 'word', 'from': 'a', 'to': '1', 'filename_to': '1',
                'ci': False, 'label': 'A', 'source': 'wordlist'}]
        _, results = masker.apply_ops('a', ops)
        self.assertEqual(results[0]['label'], 'A')
        self.assertEqual(results[0]['source'], 'wordlist')
        self.assertIn('count', results[0])

    def test_ops_applied_in_sequence(self):
        ops = [
            {'type': 'word', 'from': 'A', 'to': 'B', 'filename_to': 'B', 'ci': False},
            {'type': 'word', 'from': 'B', 'to': 'C', 'filename_to': 'C', 'ci': False},
        ]
        text, _ = masker.apply_ops('A', ops)
        self.assertEqual(text, 'C')

    def test_empty_ops_returns_text_unchanged(self):
        text, results = masker.apply_ops('hello', [])
        self.assertEqual(text, 'hello')
        self.assertEqual(results, [])


class TestNewlinePreservation(unittest.TestCase):
    """A4: 正则替换不吞换行符（脱敏工具的核心保证）。"""

    def test_line_count_conserved_for_regex_op(self):
        ops = [{'type': 'regex', 'pattern': r'1[3-9]\d{9}', 'to': '***',
                'filename_to': '***', 'ci': False}]
        text = '第一行 13800000000\n第二行\n第三行 13900000000\n第四行'
        out, _ = masker.apply_ops(text, ops)
        self.assertEqual(len(out.splitlines()), len(text.splitlines()))

    def test_line_count_conserved_with_dotall_risk_pattern(self):
        """即使 pattern 含 .*（贪婪匹配），也不应跨越换行符。"""
        ops = [{'type': 'regex', 'pattern': 'A.*B', 'to': 'X', 'filename_to': 'X', 'ci': False}]
        text = 'A\nline1\nline2\nB'
        out, _ = masker.apply_ops(text, ops)
        self.assertEqual(len(out.splitlines()), len(text.splitlines()))

    def test_labeled_rule_keeps_line_structure(self):
        """标签型规则（\1：***）不应把整行吞掉。"""
        ops = masker.build_ops({
            'mode': masker.MODE_MASK,
            'field_ids': ['labeled'],
            'library': MINI_LIBRARY,
            'mask': '***',
        })
        text = '联系人：张三\n其他内容\n联系人：��四'
        out, _ = masker.apply_ops(text, ops)
        self.assertEqual(len(out.splitlines()), len(text.splitlines()))
        self.assertNotIn('张三', out)
        self.assertNotIn('李四', out)

    def test_trailing_newline_preserved(self):
        ops = [{'type': 'regex', 'pattern': 'secret', 'to': '***',
                'filename_to': '***', 'ci': False}]
        text = 'a secret value\n'
        out, _ = masker.apply_ops(text, ops)
        self.assertTrue(out.endswith('\n'))
        self.assertEqual(out.count('\n'), text.count('\n'))

    def test_real_library_rules_do_not_swallow_newlines(self):
        """内置字段库全量规则跑一遍，行数必须守恒。"""
        library = load_library()
        ops = masker.build_ops({
            'mode': masker.MODE_MASK,
            'field_ids': masker.default_field_ids(library),
            'library': library,
            'mask': '***',
        })
        text = (
            '企业名称：示例能源集团有限公司\n'
            '统一社会信用代码：91120100MA06XYQWY0\n'
            '手机：13800000000\n'
            '客户名单：A公司、B公司\n'
            '密码：Admin@2024\n'
        )
        out, _ = masker.apply_ops(text, ops)
        self.assertEqual(len(out.splitlines()), len(text.splitlines()))


class TestBackreference(unittest.TestCase):
    """A5: 捕获组反向引用（\\1 \\2）在标签型规则中的正确回填。"""

    def test_group1_backreference_filled(self):
        ops = masker.build_ops({
            'mode': masker.MODE_MASK,
            'field_ids': ['labeled'],
            'library': MINI_LIBRARY,
            'mask': '***',
        })
        out, _ = masker.apply_ops('联系人：张三', ops)
        self.assertEqual(out, '联系人：***')

    def test_group2_backreference_filled(self):
        library = {
            'categories': [{
                'id': 'c',
                'fields': [{
                    'id': 'two_groups',
                    'pattern': r'(部门)[ \t]*[:：][ \t]*(\S+)[ \t]*(编号[:：][ \t]*\S+)',
                    'mask': r'\1：***（\3）',
                }],
            }]
        }
        ops = masker.build_ops({
            'mode': masker.MODE_MASK, 'field_ids': ['two_groups'],
            'library': library, 'mask': '***',
        })
        out, _ = masker.apply_ops('部门：财务部 编号：A-001', ops)
        self.assertEqual(out, '部门：***（编号：A-001）')
        self.assertIn('编号：A-001', out)

    def test_is_labeled_rule_detection(self):
        self.assertTrue(masker._is_labeled_rule({'mask': r'\1：***'}))
        self.assertTrue(masker._is_labeled_rule({'mask': r'\2***'}))
        self.assertFalse(masker._is_labeled_rule({'mask': '***'}))
        self.assertFalse(masker._is_labeled_rule({}))

    def test_real_library_labeled_rule_keeps_label(self):
        library = load_library()
        ops = masker.build_ops({
            'mode': masker.MODE_MASK,
            'field_ids': ['usci'],
            'library': library,
            'mask': '***',
        })
        out, _ = masker.apply_ops('统一社会信用代码：91120100MA06XYQWY0', ops)
        self.assertEqual(out, '统一社会信用代码：***')


class TestFilenameMode(unittest.TestCase):
    """A6: for_filename=True 时使用 filename_to 而非 to。"""

    def test_for_filename_uses_filename_to(self):
        ops = [{'type': 'word', 'from': 'X', 'to': 'CONTENT', 'filename_to': 'NAME',
                'ci': False, 'apply_to_filename': True}]
        self.assertEqual(masker.apply_ops('X', ops, for_filename=False)[0], 'CONTENT')
        self.assertEqual(masker.apply_ops('X', ops, for_filename=True)[0], 'NAME')

    def test_for_filename_uses_filename_to_for_regex_op(self):
        ops = [{'type': 'regex', 'pattern': 'X', 'to': 'CONTENT', 'filename_to': 'NAME',
                'ci': False, 'apply_to_filename': True}]
        self.assertEqual(masker.apply_ops('X', ops, for_filename=True)[0], 'NAME')

    def test_filename_to_falls_back_to_to_when_missing(self):
        ops = [{'type': 'word', 'from': 'X', 'to': 'CONTENT', 'ci': False}]
        self.assertEqual(masker.apply_ops('X', ops, for_filename=True)[0], 'CONTENT')

    def test_word_op_filename_to_is_literal(self):
        """word op 的替换值为字面量，反向引用不被解释（与 regex op 语义不同）。"""
        ops = [{'type': 'word', 'from': 'AAA', 'to': r'X\1Y', 'filename_to': r'X\1Y',
                'ci': False, 'apply_to_filename': True}]
        self.assertEqual(masker.apply_ops('AAA', ops)[0], r'X\1Y')


class TestCaseInsensitive(unittest.TestCase):
    """A7: ci（大小写不敏感）标志生效。"""

    def test_ci_true_matches_all_cases(self):
        ops = [{'type': 'word', 'from': 'abc', 'to': '#', 'filename_to': '#',
                'ci': True, 'apply_to_filename': True}]
        text, results = masker.apply_ops('ABC abc AbC', ops)
        self.assertEqual(text, '# # #')
        self.assertEqual(results[0]['count'], 3)

    def test_ci_false_only_matches_exact_case(self):
        ops = [{'type': 'word', 'from': 'abc', 'to': '#', 'filename_to': '#',
                'ci': False, 'apply_to_filename': True}]
        text, results = masker.apply_ops('ABC abc AbC', ops)
        self.assertEqual(text, 'ABC # AbC')
        self.assertEqual(results[0]['count'], 1)

    def test_ci_true_for_regex_op(self):
        ops = [{'type': 'regex', 'pattern': 'abc', 'to': '#', 'filename_to': '#',
                'ci': True, 'apply_to_filename': True}]
        text, results = masker.apply_ops('ABC abc', ops)
        self.assertEqual(text, '# #')
        self.assertEqual(results[0]['count'], 2)

    def test_builtin_field_ci_flag_respected(self):
        library = {
            'categories': [{
                'id': 'c',
                'fields': [{
                    'id': 'ci_field',
                    'pattern': 'secret',
                    'mask': '***',
                    'ci': True,
                }],
            }]
        }
        ops = masker.build_ops({
            'mode': masker.MODE_MASK, 'field_ids': ['ci_field'],
            'library': library, 'mask': '***',
        })
        self.assertTrue(ops[0]['ci'])
        out, _ = masker.apply_ops('SECRET secret', ops)
        self.assertEqual(out, '*** ***')


class TestCaseSensitiveConfig(unittest.TestCase):
    """A8: case_sensitive=False 时 word op 的 ci 为 True。"""

    def test_case_sensitive_false_sets_word_ci_true(self):
        ops = masker.build_ops({
            'mode': masker.MODE_MASK,
            'word_list': ['星海项目'],
            'case_sensitive': False,
            'library': MINI_LIBRARY,
            'mask': '***',
        })
        word_op = next(op for op in ops if op['source'] == 'wordlist')
        self.assertTrue(word_op['ci'])

    def test_case_sensitive_true_sets_word_ci_false(self):
        ops = masker.build_ops({
            'mode': masker.MODE_MASK,
            'word_list': ['星海项目'],
            'case_sensitive': True,
            'library': MINI_LIBRARY,
            'mask': '***',
        })
        word_op = next(op for op in ops if op['source'] == 'wordlist')
        self.assertFalse(word_op['ci'])

    def test_case_sensitive_defaults_to_true(self):
        ops = masker.build_ops({
            'mode': masker.MODE_MASK,
            'word_list': ['星海项目'],
            'library': MINI_LIBRARY,
            'mask': '***',
        })
        word_op = next(op for op in ops if op['source'] == 'wordlist')
        self.assertFalse(word_op['ci'])

    def test_keyword_op_always_ci_false(self):
        """关键词替换是精确匹配，case_sensitive 不影响它。"""
        ops = masker.build_ops({
            'mode': masker.MODE_COMBO,
            'rules': [{'keyword': 'Abc', 'replacement': 'X'}],
            'case_sensitive': False,
            'library': MINI_LIBRARY,
            'mask': '***',
        })
        keyword_op = next(op for op in ops if op['source'] == 'keyword')
        self.assertFalse(keyword_op['ci'])


class TestInvalidRegex(unittest.TestCase):
    """A9: 非法正则 pattern 时 apply_ops 不崩溃，count 为 0。"""

    def test_invalid_regex_pattern_does_not_raise(self):
        ops = [{'type': 'regex', 'pattern': '(', 'to': 'X', 'filename_to': 'Y',
                'ci': False, 'apply_to_filename': True}]
        text, results = masker.apply_ops('abc', ops)
        self.assertEqual(text, 'abc')
        self.assertEqual(results[0]['count'], 0)

    def test_invalid_regex_in_middle_of_ops_does_not_stop_pipeline(self):
        ops = [
            {'type': 'regex', 'pattern': '(', 'to': 'X', 'filename_to': 'Y', 'ci': False},
            {'type': 'word', 'from': 'abc', 'to': '***', 'filename_to': '***', 'ci': False},
        ]
        text, results = masker.apply_ops('abc', ops)
        self.assertEqual(text, '***')
        self.assertEqual(results[0]['count'], 0)
        self.assertEqual(results[1]['count'], 1)

    def test_invalid_regex_for_filename_mode(self):
        ops = [{'type': 'regex', 'pattern': '[', 'to': 'X', 'filename_to': 'Y',
                'ci': False, 'apply_to_filename': True}]
        text, results = masker.apply_ops('abc', ops, for_filename=True)
        self.assertEqual(text, 'abc')
        self.assertEqual(results[0]['count'], 0)

    def test_invalid_regex_in_real_flow_does_not_crash(self):
        library = {
            'categories': [{
                'id': 'c',
                'fields': [{'id': 'bad', 'pattern': '(unclosed', 'mask': '***'}],
            }]
        }
        ops = masker.build_ops({
            'mode': masker.MODE_MASK, 'field_ids': ['bad'],
            'library': library, 'mask': '***',
        })
        text, results = masker.apply_ops('anything', ops)
        self.assertEqual(text, 'anything')
        self.assertEqual(results[0]['count'], 0)


class TestSanitizeFilenameMask(unittest.TestCase):
    """A10: sanitize_filename_mask。"""

    def test_illegal_chars_replaced_with_underscore(self):
        result = masker.sanitize_filename_mask('<>:"/\\|?*')
        self.assertEqual(result, '_________')
        for ch in '<>:"/\\|?*':
            self.assertNotIn(ch, result)

    def test_illegal_chars_mixed_with_text(self):
        self.assertEqual(
            masker.sanitize_filename_mask('a<b>c:d"e/f\\g|h?i*j'),
            'a_b_c_d_e_f_g_h_i_j',
        )

    def test_control_chars_replaced(self):
        result = masker.sanitize_filename_mask('\x00\x01\x1fab\x7f')
        self.assertNotIn('\x00', result)
        self.assertNotIn('\x01', result)
        self.assertNotIn('\x1f', result)
        self.assertIn('ab', result)

    def test_leading_trailing_spaces_and_dots_stripped(self):
        self.assertEqual(masker.sanitize_filename_mask('  .dot.  '), 'dot')
        self.assertEqual(masker.sanitize_filename_mask('...abc...'), 'abc')

    def test_empty_string_returns_default(self):
        self.assertEqual(masker.sanitize_filename_mask(''), '脱敏')

    def test_none_returns_default(self):
        self.assertEqual(masker.sanitize_filename_mask(None), '脱敏')

    def test_whitespace_only_returns_default(self):
        self.assertEqual(masker.sanitize_filename_mask('   '), '脱敏')

    def test_dots_only_returns_default(self):
        self.assertEqual(masker.sanitize_filename_mask('...'), '脱敏')

    def test_normal_chinese_mask_unchanged(self):
        self.assertEqual(masker.sanitize_filename_mask('已脱敏'), '已脱敏')

    def test_result_has_no_windows_illegal_char(self):
        result = masker.sanitize_filename_mask('a<b>c:d"e/f\\g|h?i*j')
        for ch in masker.ILLEGAL_FILENAME_CHARS:
            self.assertNotIn(ch, result)


class TestLoadFieldLibrary(unittest.TestCase):
    """A11: load_field_library 三种情况。"""

    def test_valid_json_loaded(self):
        library = load_library()
        self.assertIn('categories', library)
        self.assertGreater(len(library['categories']), 0)

    def test_missing_file_returns_empty_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / 'nope.json'
            library = masker.load_field_library(missing)
            self.assertEqual(library, {'categories': []})

    def test_corrupted_json_returns_empty_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / 'broken.json'
            broken.write_text('{ this is not json ', encoding='utf-8')
            library = masker.load_field_library(broken)
            self.assertEqual(library, {'categories': []})

    def test_directory_path_returns_empty_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = masker.load_field_library(tmp)
            self.assertEqual(library, {'categories': []})

    def test_project_library_is_valid_and_has_fields(self):
        library = load_library()
        fields = list(masker.iter_library_fields(library))
        self.assertGreater(len(fields), 20)
        for _category, field in fields:
            self.assertIn('id', field)
            self.assertIn('pattern', field)


class TestDefaultFieldIds(unittest.TestCase):
    """A12: default_field_ids 只返回 default_enabled 的字段。"""

    def test_only_default_enabled_returned(self):
        library = {
            'categories': [{
                'id': 'c',
                'default_enabled': True,
                'fields': [
                    {'id': 'on', 'pattern': 'x'},
                    {'id': 'off', 'pattern': 'y', 'default_enabled': False},
                ],
            }]
        }
        self.assertEqual(masker.default_field_ids(library), ['on'])

    def test_category_level_default_enabled_false_excludes_all(self):
        library = {
            'categories': [{
                'id': 'c',
                'default_enabled': False,
                'fields': [{'id': 'a', 'pattern': 'x'}],
            }]
        }
        self.assertEqual(masker.default_field_ids(library), [])

    def test_missing_default_enabled_defaults_to_true(self):
        library = {'categories': [{'id': 'c', 'fields': [{'id': 'a', 'pattern': 'x'}]}]}
        self.assertEqual(masker.default_field_ids(library), ['a'])

    def test_empty_library_returns_empty_list(self):
        self.assertEqual(masker.default_field_ids({}), [])
        self.assertEqual(masker.default_field_ids({'categories': []}), [])

    def test_project_library_excludes_id_card_bare(self):
        """内置库中 id_card_bare 显式 default_enabled=false，应被排除。"""
        library = load_library()
        ids = masker.default_field_ids(library)
        self.assertNotIn('id_card_bare', ids)
        self.assertIn('mobile', ids)


class TestOpsForFilename(unittest.TestCase):
    """A13: ops_for_filename 只保留 apply_to_filename=True 的操作。"""

    def test_only_true_flag_kept(self):
        ops = [
            {'type': 'word', 'from': 'a', 'apply_to_filename': True},
            {'type': 'regex', 'pattern': 'b', 'apply_to_filename': False},
            {'type': 'word', 'from': 'c'},
        ]
        filtered = masker.ops_for_filename(ops)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]['from'], 'a')

    def test_builtin_field_flags_respected(self):
        library = load_library()
        ops = masker.build_ops({
            'mode': masker.MODE_MASK,
            'field_ids': masker.default_field_ids(library),
            'library': library,
            'mask': '***',
        })
        filename_ops = masker.ops_for_filename(ops)
        self.assertTrue(all(op['apply_to_filename'] for op in filename_ops))
        field_ids = {op.get('field_id') for op in filename_ops}
        # mobile / email / id_card 在内置库中标记了 apply_to_filename
        self.assertIn('mobile', field_ids)
        self.assertIn('email', field_ids)
        # usci 是内容型，不应出现在文件名操作里
        self.assertNotIn('usci', field_ids)

    def test_empty_ops_returns_empty(self):
        self.assertEqual(masker.ops_for_filename([]), [])


class TestIterLibraryFields(unittest.TestCase):
    """A 补充: iter_library_fields 产出 (category, field)。"""

    def test_yields_category_field_pairs(self):
        library = {'categories': [
            {'id': 'c1', 'fields': [{'id': 'f1'}, {'id': 'f2'}]},
            {'id': 'c2', 'fields': [{'id': 'f3'}]},
        ]}
        pairs = list(masker.iter_library_fields(library))
        self.assertEqual(len(pairs), 3)
        self.assertEqual(pairs[0][0]['id'], 'c1')
        self.assertEqual(pairs[0][1]['id'], 'f1')
        self.assertEqual(pairs[2][0]['id'], 'c2')

    def test_empty_library_yields_nothing(self):
        self.assertEqual(list(masker.iter_library_fields({})), [])


if __name__ == '__main__':
    unittest.main()
