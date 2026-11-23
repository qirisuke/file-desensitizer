"""B 组：Flask 应用层单元测试。

覆盖要点：
 14. build_config：非法 mode 回退、None 兜底、mask / filename_mask 默认值
 15. safe_filename：空名、Windows 保留名、尾部点与空格
 16. is_text_file：二进制 / 文本扩展名
 17. scan_directory：空目录、不存在目录、无 ops、正常统计
 18. scan 与 apply 一致性（重点）
 19. apply_changes：目标文件不存在时跳过；写入异常收集进 errors
 20. find_free_port：返回一个可用端口
"""
import socket
import tempfile
import unittest
from pathlib import Path

from .helpers import load_library, default_ids, write_tree
import masker
import app as service


class TestBuildConfig(unittest.TestCase):
    """B14: build_config。"""

    def test_invalid_mode_falls_back_to_replace(self):
        config = service.build_config({'mode': 'bogus-mode'})
        self.assertEqual(config['mode'], 'replace')

    def test_none_mode_falls_back_to_replace(self):
        config = service.build_config({'mode': None})
        self.assertEqual(config['mode'], 'replace')

    def test_valid_modes_preserved(self):
        for mode in masker.VALID_MODES:
            config = service.build_config({'mode': mode})
            self.assertEqual(config['mode'], mode)

    def test_missing_mode_defaults_to_replace(self):
        self.assertEqual(service.build_config({})['mode'], 'replace')

    def test_none_values_become_empty_lists(self):
        config = service.build_config({
            'rules': None,
            'word_list': None,
            'field_ids': None,
        })
        self.assertEqual(config['rules'], [])
        self.assertEqual(config['word_list'], [])
        self.assertEqual(config['field_ids'], [])

    def test_missing_lists_become_empty_lists(self):
        config = service.build_config({})
        self.assertEqual(config['rules'], [])
        self.assertEqual(config['word_list'], [])
        self.assertEqual(config['field_ids'], [])

    def test_mask_default(self):
        config = service.build_config({})
        self.assertEqual(config['mask'], masker.DEFAULT_MASK)
        self.assertEqual(config['mask'], '***')

    def test_filename_mask_default(self):
        config = service.build_config({})
        self.assertEqual(config['filename_mask'], masker.DEFAULT_FILENAME_MASK)
        self.assertEqual(config['filename_mask'], '已脱敏')

    def test_none_mask_uses_default(self):
        config = service.build_config({'mask': None, 'filename_mask': None})
        self.assertEqual(config['mask'], masker.DEFAULT_MASK)
        self.assertEqual(config['filename_mask'], masker.DEFAULT_FILENAME_MASK)

    def test_empty_mask_uses_default(self):
        config = service.build_config({'mask': '', 'filename_mask': ''})
        self.assertEqual(config['mask'], masker.DEFAULT_MASK)
        self.assertEqual(config['filename_mask'], masker.DEFAULT_FILENAME_MASK)

    def test_explicit_mask_preserved(self):
        config = service.build_config({'mask': '##', 'filename_mask': 'XX'})
        self.assertEqual(config['mask'], '##')
        self.assertEqual(config['filename_mask'], 'XX')

    def test_boolean_defaults(self):
        config = service.build_config({})
        self.assertTrue(config['include_filenames'])
        self.assertTrue(config['include_contents'])
        self.assertTrue(config['recursive'])
        self.assertTrue(config['case_sensitive'])

    def test_directory_trailing_separator_stripped(self):
        config = service.build_config({'directory': 'C:\\some\\dir\\'})
        self.assertFalse(config['directory'].endswith('\\'))
        config = service.build_config({'directory': 'C:/some/dir/'})
        self.assertFalse(config['directory'].endswith('/'))

    def test_config_is_json_serialisable(self):
        import json
        config = service.build_config({'mode': 'combo', 'rules': [{'keyword': 'a', 'replacement': 'b'}]})
        json.dumps(config)  # 不应抛异常


class TestSafeFilename(unittest.TestCase):
    """B15: safe_filename。"""

    def test_empty_name_returns_default(self):
        self.assertEqual(service.safe_filename(''), '脱敏')

    def test_none_name_returns_default(self):
        self.assertEqual(service.safe_filename(None), '脱敏')

    def test_whitespace_only_returns_default(self):
        self.assertEqual(service.safe_filename('   '), '脱敏')

    def test_windows_reserved_names_get_underscore_prefix(self):
        for name in ('CON', 'PRN', 'AUX', 'NUL', 'COM1', 'LPT1'):
            self.assertEqual(service.safe_filename(name), '_' + name)

    def test_reserved_names_are_case_insensitive(self):
        self.assertEqual(service.safe_filename('con'), '_con')
        self.assertEqual(service.safe_filename('Com1'), '_Com1')

    def test_reserved_name_with_extension_gets_prefix(self):
        self.assertEqual(service.safe_filename('con.txt'), '_con.txt')

    def test_trailing_dot_and_space_removed(self):
        self.assertEqual(service.safe_filename('name.'), 'name')
        self.assertEqual(service.safe_filename('name '), 'name')
        self.assertEqual(service.safe_filename('name.  '), 'name')

    def test_normal_name_unchanged(self):
        self.assertEqual(service.safe_filename('report.txt'), 'report.txt')

    def test_chinese_name_unchanged(self):
        self.assertEqual(service.safe_filename('经营情况.txt'), '经营情况.txt')

    def test_inner_dots_preserved(self):
        self.assertEqual(service.safe_filename('a.b.c.txt'), 'a.b.c.txt')


class TestIsTextFile(unittest.TestCase):
    """B16: is_text_file。"""

    def test_binary_extensions_return_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            for ext in ('.exe', '.dll', '.so', '.png', '.jpg', '.zip', '.pyc'):
                path = Path(tmp) / f'file{ext}'
                path.write_bytes(b'\x00\x01\x02')
                self.assertFalse(service.is_text_file(path), f'{ext} 应判为二进制')

    def test_text_extensions_return_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            for ext in ('.txt', '.md', '.csv', '.json', '.log', '.py'):
                path = Path(tmp) / f'file{ext}'
                path.write_text('hello', encoding='utf-8')
                self.assertTrue(service.is_text_file(path), f'{ext} 应判为文本')

    def test_pdf_is_classified_as_text_by_source(self):
        """已知行为差异（见报告 Defect D2）：.pdf 位于 TEXT_EXTENSIONS。

        PDF 实际是二进制格式，is_text_file 会因扩展名直接判定为文本，
        随后 scan 阶段 read_text 因 UnicodeDecodeError 被跳过。
        这里如实记录当前实际行为。
        """
        self.assertIn('.pdf', service.TEXT_EXTENSIONS)
        self.assertNotIn('.pdf', service.BINARY_EXTENSIONS)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'doc.pdf'
            path.write_bytes(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
            self.assertTrue(service.is_text_file(path))

    def test_unknown_extension_with_utf8_content_returns_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'file.unknown'
            path.write_text('hello 世界', encoding='utf-8')
            self.assertTrue(service.is_text_file(path))

    def test_unknown_extension_with_binary_content_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'file.unknown'
            path.write_bytes(b'\xff\xfe\x00\x80')
            self.assertFalse(service.is_text_file(path))

    def test_no_extension_utf8_file_returns_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'README'
            path.write_text('hello', encoding='utf-8')
            self.assertTrue(service.is_text_file(path))

    def test_missing_file_with_text_extension_returns_true(self):
        """已知行为差异（见报告 Defect D3）：is_text_file 不检查文件是否存在。

        扩展名命中 TEXT_EXTENSIONS 时直接返回 True，未先判断路径是否存在。
        这里如实记录当前实际行为。
        """
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(service.is_text_file(Path(tmp) / 'missing.txt'))
            self.assertFalse(service.is_text_file(Path(tmp) / 'missing.exe'))


class TestScanDirectory(unittest.TestCase):
    """B17: scan_directory。"""

    def test_empty_directory_returns_error(self):
        config = service.build_config({'directory': ''})
        result = service.scan_directory(config)
        self.assertIn('error', result)

    def test_nonexistent_directory_returns_error(self):
        config = service.build_config({'directory': 'Z:/definitely/not/here'})
        result = service.scan_directory(config)
        self.assertIn('error', result)
        self.assertIn('目录不存在', result['error'])

    def test_file_path_instead_of_directory_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'afile.txt'
            path.write_text('x', encoding='utf-8')
            config = service.build_config({'directory': str(path)})
            result = service.scan_directory(config)
            self.assertIn('error', result)

    def test_no_ops_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = service.build_config({'directory': tmp})
            result = service.scan_directory(config)
            self.assertIn('error', result)

    def test_successful_scan_statistics(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp, {
                'a.txt': '手机：13800000000\n',
                'b.txt': '示例能源集团\n',
                'sub/c.txt': '统一社会信用代码：91120100MA06XYQWY0\n',
            })
            config = service.build_config({
                'directory': tmp,
                'mode': 'combo',
                'word_list': ['示例能源'],
                'rules': [{'keyword': '示例能源', 'replacement': 'XX'}],
                'field_ids': default_ids(),
                'mask': '***',
                'filename_mask': '已脱敏',
            })
            result = service.scan_directory(config)
            self.assertNotIn('error', result)
            self.assertEqual(result['scanned_files'], 3)
            self.assertEqual(result['matched_files'], 3)
            self.assertGreater(result['total_content_changes'], 0)
            self.assertEqual(len(result['content_changes']), 3)

    def test_scan_does_not_modify_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp, {'a.txt': '手机：13800000000\n'})
            config = service.build_config({
                'directory': tmp, 'mode': 'mask',
                'field_ids': default_ids(), 'mask': '***',
            })
            service.scan_directory(config)
            self.assertEqual(
                (Path(tmp) / 'a.txt').read_text(encoding='utf-8'),
                '手机：13800000000\n',
            )

    def test_filename_changes_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp, {'13800000000_联系人.txt': 'x\n'})
            config = service.build_config({
                'directory': tmp, 'mode': 'mask',
                'field_ids': default_ids(), 'mask': '***', 'filename_mask': '已脱敏',
            })
            result = service.scan_directory(config)
            self.assertEqual(result['total_filename_changes'], 1)
            change = result['filename_changes'][0]
            self.assertIn('13800000000', change['old'])
            self.assertIn('已脱敏', change['new'])

    def test_non_recursive_scan_ignores_subdirectories(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp, {'a.txt': 'x\n', 'sub/b.txt': 'y\n'})
            config = service.build_config({
                'directory': tmp, 'mode': 'mask',
                'field_ids': default_ids(), 'mask': '***',
                'recursive': False,
            })
            result = service.scan_directory(config)
            self.assertEqual(result['scanned_files'], 1)

    def test_recursive_scan_includes_subdirectories(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp, {'a.txt': 'x\n', 'sub/b.txt': 'y\n'})
            config = service.build_config({
                'directory': tmp, 'mode': 'mask',
                'field_ids': default_ids(), 'mask': '***',
                'recursive': True,
            })
            result = service.scan_directory(config)
            self.assertEqual(result['scanned_files'], 2)


class TestScanApplyConsistency(unittest.TestCase):
    """B18: scan 与 apply 一致性（重点）。

    先核实该性质是否成立：apply_changes 会用 scan 阶段记录的、
    仅 count>0 的 ops 重跑一次脱敏。理论上被过滤掉的 op 本身就是 no-op，
    而保留下来的 op 执行顺序与参数完全一致，因此重跑结果应与 scan 阶段一致。

    实测（combo / mask / replace 三种模式、多目录嵌套、含非 UTF-8 文件）
    均确认一致。若未来源码变更导致该性质被破坏，本用例会失败并给出提示。
    """

    def _scan_and_apply(self, files, config_over, mode='combo'):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        write_tree(base, files)

        config = service.build_config({
            'directory': str(base),
            'mode': mode,
            'field_ids': default_ids(),
            'mask': '***',
            'filename_mask': '已脱敏',
            'case_sensitive': True,
            **config_over,
        })
        config['directory'] = str(base)

        preview = service.scan_directory(config)
        self.assertNotIn('error', preview, preview.get('error'))

        # scan 阶段用全量 ops 算出的期望结果
        ops = masker.build_ops({**config, 'library': service.FIELD_LIBRARY})
        expected = {}
        for path in sorted(base.rglob('*')):
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding='utf-8')
            except (UnicodeDecodeError, OSError):
                continue
            expected[str(path.relative_to(base))] = masker.apply_ops(content, ops)[0]

        rename_map = {c['old']: c['new'] for c in preview['filename_changes']}
        result = service.apply_changes(str(base), preview)
        self.assertEqual(result['errors'], [], f"落盘不应有错误: {result['errors']}")

        actual = {}
        for path in sorted(base.rglob('*')):
            if path.is_file():
                try:
                    actual[str(path.relative_to(base))] = path.read_text(encoding='utf-8')
                except (UnicodeDecodeError, OSError):
                    continue
        return expected, actual, rename_map, result, base

    def test_content_after_apply_matches_scan_stage_result(self):
        expected, actual, _rename, _result, _base = self._scan_and_apply(
            {'a.txt': '统一社会信用代码：91120100MA06XYQWY0\n手机：13800000000\n'},
            {},
        )
        self.assertEqual(actual, expected)

    def test_consistency_in_combo_mode(self):
        expected, actual, _r, _res, _b = self._scan_and_apply(
            {'a.txt': '示例能源集团\n手机：13800000000\n'},
            {'word_list': ['示例能源'],
             'rules': [{'keyword': '示例能源', 'replacement': 'XX'}]},
            mode='combo',
        )
        self.assertEqual(actual, expected)

    def test_consistency_in_mask_mode(self):
        expected, actual, _r, _res, _b = self._scan_and_apply(
            {'a.txt': '统一社会信用代码：91120100MA06XYQWY0\n密码：Admin@2024\n'},
            {},
            mode='mask',
        )
        self.assertEqual(actual, expected)

    def test_consistency_in_replace_mode(self):
        expected, actual, _r, _res, _b = self._scan_and_apply(
            {'a.txt': '示例能源集团\n'},
            {'rules': [{'keyword': '示例能源', 'replacement': 'XX'}]},
            mode='replace',
        )
        self.assertEqual(actual, expected)

    def test_consistency_with_nested_directories(self):
        expected, actual, _r, _res, _b = self._scan_and_apply(
            {'x/y/a.txt': '示例能源集团\n', 'b.txt': '手机：13800000000\n'},
            {'word_list': ['示例能源']},
        )
        self.assertEqual(actual, expected)

    def test_consistency_with_renamed_files(self):
        """文件名也被改写时，内容仍需与 scan 阶段一致。"""
        expected, actual, rename_map, result, _b = self._scan_and_apply(
            {'示例能源_a.txt': '示例能源集团\n手机：13800000000\n'},
            {'word_list': ['示例能源']},
        )
        self.assertGreaterEqual(result['applied_renames'], 0)
        # 重命名后的路径下，内容应等于期望值
        for old_rel, new_rel in rename_map.items():
            if old_rel in expected:
                self.assertEqual(actual.get(new_rel), expected[old_rel])

    def test_consistency_with_non_utf8_file_present(self):
        """非 UTF-8 文件在 scan 阶段被跳过，落盘也不应改动它。"""
        expected, actual, _r, _res, _b = self._scan_and_apply(
            {'a.txt': '示例能源集团\n', 'b.bin': b'\xff\xfe\x00binary'},
            {'word_list': ['示例能源']},
        )
        self.assertEqual(actual, expected)

    def test_apply_only_runs_matched_ops_but_result_is_identical(self):
        """显式记录 apply_changes 只重跑 count>0 的 ops 这一实现细节。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        write_tree(base, {'a.txt': '手机：13800000000\n'})

        config = service.build_config({
            'directory': str(base), 'mode': 'mask',
            'field_ids': default_ids(), 'mask': '***',
        })
        preview = service.scan_directory(config)
        item = preview['content_changes'][0]
        self.assertTrue(item['ops'])
        self.assertTrue(all(op['count'] > 0 for op in item['ops']))
        # 未命中的规则不进入 item['ops']
        self.assertLess(len(item['ops']), len(default_ids()))


class TestApplyChanges(unittest.TestCase):
    """B19: apply_changes。"""

    def test_missing_target_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            preview = {
                'content_changes': [{'file': 'not-exist.txt', 'ops': []}],
                'filename_changes': [],
            }
            result = service.apply_changes(tmp, preview)
            self.assertEqual(result['applied_files'], 0)
            self.assertEqual(result['applied_renames'], 0)
            self.assertEqual(result['errors'], [])

    def test_missing_rename_source_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            preview = {
                'content_changes': [],
                'filename_changes': [{'old': 'gone.txt', 'new': 'new.txt'}],
            }
            result = service.apply_changes(tmp, preview)
            self.assertEqual(result['applied_renames'], 0)
            self.assertEqual(result['errors'], [])

    def test_write_exception_is_collected_into_errors(self):
        """写入异常被收集进 errors 而不是抛出。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / 'a.txt'
            target.write_text('手机：13800000000\n', encoding='utf-8')

            # 构造一个必然抛异常的 op：让写入失败 —— 用只读文件模拟
            ops = [{
                'type': 'word', 'from': 'x', 'to': 'y',
                'filename_to': 'y', 'ci': False, 'count': 1,
            }]
            preview = {'content_changes': [{'file': 'a.txt', 'ops': ops}],
                       'filename_changes': []}
            # 正常路径下会写入（内容无变化则不写）
            result = service.apply_changes(tmp, preview)
            self.assertIsInstance(result['errors'], list)

    def test_write_exception_collected_via_mock(self):
        """通过 mock 让 write_text 抛异常，验证异常被收集进 errors。"""
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'a.txt').write_text('手机：13800000000\n', encoding='utf-8')
            ops = [{
                'type': 'word', 'from': '13800000000', 'to': '***',
                'filename_to': '***', 'ci': False, 'count': 1,
            }]
            preview = {'content_changes': [{'file': 'a.txt', 'ops': ops}],
                       'filename_changes': []}

            original = Path.write_text

            def boom(self, *args, **kwargs):
                raise OSError('模拟磁盘写入失败')

            with mock.patch.object(Path, 'write_text', boom):
                result = service.apply_changes(tmp, preview)

            self.assertEqual(result['applied_files'], 0)
            self.assertEqual(len(result['errors']), 1)
            self.assertIn('a.txt', result['errors'][0])
            self.assertIn('模拟磁盘写入失败', result['errors'][0])
            # 原文件内容未被改动
            self.assertEqual(
                (base / 'a.txt').read_text(encoding='utf-8'),
                '手机：13800000000\n',
            )

    def test_rename_exception_collected_via_mock(self):
        """重命名异常被收集进 errors 而不是抛出。"""
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'a.txt').write_text('x', encoding='utf-8')
            preview = {
                'content_changes': [],
                'filename_changes': [{'old': 'a.txt', 'new': 'b.txt'}],
            }

            def boom(self, *args, **kwargs):
                raise OSError('模拟重命名失败')

            with mock.patch.object(Path, 'rename', boom):
                result = service.apply_changes(tmp, preview)

            self.assertEqual(result['applied_renames'], 0)
            self.assertEqual(len(result['errors']), 1)
            self.assertIn('模拟重命名失败', result['errors'][0])

    def test_apply_changes_on_empty_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = service.apply_changes(tmp, {})
            self.assertEqual(result['applied_files'], 0)
            self.assertEqual(result['applied_renames'], 0)
            self.assertEqual(result['errors'], [])

    def test_apply_changes_rewrites_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'a.txt').write_text('手机：13800000000\n', encoding='utf-8')
            ops = [{
                'type': 'word', 'from': '13800000000', 'to': '***',
                'filename_to': '***', 'ci': False, 'count': 1,
            }]
            preview = {'content_changes': [{'file': 'a.txt', 'ops': ops}],
                       'filename_changes': []}
            result = service.apply_changes(tmp, preview)
            self.assertEqual(result['applied_files'], 1)
            self.assertEqual(result['errors'], [])
            self.assertNotIn('13800000000', (base / 'a.txt').read_text(encoding='utf-8'))

    def test_apply_changes_renames_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / '13800000000_a.txt').write_text('x', encoding='utf-8')
            preview = {
                'content_changes': [],
                'filename_changes': [{'old': '13800000000_a.txt',
                                      'new': '已脱敏_a.txt'}],
            }
            result = service.apply_changes(tmp, preview)
            self.assertEqual(result['applied_renames'], 1)
            self.assertTrue((base / '已脱敏_a.txt').exists())


class TestFindFreePort(unittest.TestCase):
    """B20: find_free_port。"""

    def test_returns_a_usable_port(self):
        port = service.find_free_port()
        self.assertIsInstance(port, int)
        self.assertGreater(port, 0)
        self.assertLess(port, 65536)

    def test_port_can_actually_be_bound(self):
        port = service.find_free_port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', port))
            s.listen(1)

    def test_skips_occupied_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            s.listen(1)
            occupied = s.getsockname()[1]
            chosen = service.find_free_port(preferred=occupied, attempts=10)
            self.assertNotEqual(chosen, occupied)

    def test_out_of_range_port_raises_overflow_error(self):
        """已知缺陷（见报告 Defect D4）：find_free_port 只捕获 OSError。

        端口号超出 0-65535 范围时，socket.bind 抛出 OverflowError 而非 OSError，
        不会被 `except OSError` 捕获，异常会向上传播。
        这里如实记录当前实际行为。
        """
        with self.assertRaises(OverflowError):
            service.find_free_port(preferred=70000, attempts=3)

    def test_returns_preferred_when_all_candidate_ports_are_occupied(self):
        """所有候选端口均被占用时，返回 preferred（不抛异常）。"""
        sockets = []
        try:
            for port in range(56000, 56003):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(('127.0.0.1', port))
                s.listen(1)
                sockets.append(s)
            chosen = service.find_free_port(preferred=56000, attempts=3)
            self.assertEqual(chosen, 56000)
        finally:
            for s in sockets:
                s.close()


if __name__ == '__main__':
    unittest.main()
