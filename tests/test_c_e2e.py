"""C 组：端到端样例测试。

 21. 用真实敏感值文档跑一次全量脱敏，断言输出中不含关键敏感值原文，且行数守恒。

补充：
 - examples/ 下的 before/after 样例文件一致性检查；
 - 完整「扫描 -> 落盘」链路的内容与文件名双重校验。
"""
import tempfile
import unittest
from pathlib import Path

from .helpers import (
    AFTER_SAMPLE, BEFORE_SAMPLE, KEY_SECRETS, RAW_DOC,
    default_ids, load_library, write_tree,
)
import masker
import app as service


def _mask_ops():
    """内置字段库 + 示例敏感词的全量脱敏操作。"""
    library = load_library()
    return masker.build_ops({
        'mode': masker.MODE_MASK,
        'word_list': ['示例能源', '星海项目'],
        'field_ids': masker.default_field_ids(library),
        'mask': '***',
        'filename_mask': '已脱敏',
        'case_sensitive': True,
        'library': library,
    })


class TestEndToEndSample(unittest.TestCase):
    """C21: 真实敏感值文档全量脱敏。"""

    def test_no_key_secret_survives(self):
        out, _results = masker.apply_ops(RAW_DOC, _mask_ops())
        for secret in KEY_SECRETS:
            self.assertNotIn(secret, out, f'敏感值未被清除: {secret}')

    def test_line_count_is_conserved(self):
        out, _results = masker.apply_ops(RAW_DOC, _mask_ops())
        self.assertEqual(
            len(out.splitlines()), len(RAW_DOC.splitlines()),
            '脱敏后行数必须与原文一致（不得吞掉换行符）',
        )

    def test_newline_character_count_is_conserved(self):
        out, _results = masker.apply_ops(RAW_DOC, _mask_ops())
        self.assertEqual(out.count('\n'), RAW_DOC.count('\n'))

    def test_labels_are_preserved(self):
        """标签型规则保留标签前缀，只替换值。"""
        out, _results = masker.apply_ops(RAW_DOC, _mask_ops())
        self.assertIn('统一社会信用代码：***', out)
        self.assertIn('手机：***', out)
        self.assertIn('身份证号：***', out)

    def test_match_counts_are_positive(self):
        _out, results = masker.apply_ops(RAW_DOC, _mask_ops())
        matched = [r for r in results if r['count'] > 0]
        self.assertGreater(len(matched), 5)
        self.assertTrue(all(r['count'] > 0 for r in matched))

    def test_every_line_contains_no_secret(self):
        out, _results = masker.apply_ops(RAW_DOC, _mask_ops())
        for index, line in enumerate(out.splitlines(), start=1):
            for secret in KEY_SECRETS:
                self.assertNotIn(secret, line, f'第 {index} 行仍含敏感值: {secret}')


class TestExampleFiles(unittest.TestCase):
    """examples/ 目录下的 before/after 样例文件。"""

    def test_example_files_exist(self):
        self.assertTrue(BEFORE_SAMPLE.exists(), f'缺少样例文件: {BEFORE_SAMPLE}')
        self.assertTrue(AFTER_SAMPLE.exists(), f'缺少样例文件: {AFTER_SAMPLE}')

    def test_example_files_have_same_line_count(self):
        before = BEFORE_SAMPLE.read_text(encoding='utf-8')
        after = AFTER_SAMPLE.read_text(encoding='utf-8')
        self.assertEqual(len(before.splitlines()), len(after.splitlines()))

    def test_example_after_file_contains_no_secrets(self):
        after = AFTER_SAMPLE.read_text(encoding='utf-8')
        for secret in KEY_SECRETS:
            self.assertNotIn(secret, after, f'after 样例仍含敏感值: {secret}')

    def test_example_before_file_is_already_masked(self):
        """已知现状：examples/before 中的文件实际已是脱敏后内容。

        这与文件名 "before" 的字面含义不符，是文档/样例维护上的问题；
        本用例如实记录当前行为，不做修改。
        """
        before = BEFORE_SAMPLE.read_text(encoding='utf-8')
        after = AFTER_SAMPLE.read_text(encoding='utf-8')
        self.assertEqual(
            before, after,
            '当前 examples/before 与 examples/after 内容完全相同（before 已是脱敏态）',
        )

    def test_example_after_file_is_idempotent_under_desensitization(self):
        """对已脱敏样例再跑一次脱敏，结果不应改变（幂等性）。"""
        after = AFTER_SAMPLE.read_text(encoding='utf-8')
        out, _results = masker.apply_ops(after, _mask_ops())
        self.assertEqual(out, after, '对已脱敏内容再次脱敏不应产生新的改动')


class TestFullPipeline(unittest.TestCase):
    """完整链路：临时目录 -> 扫描 -> 落盘 -> 校验。"""

    def test_scan_apply_and_verify_no_leaks(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write_tree(base, {
                '示例能源_经营情况.txt': RAW_DOC,
                '13800000000_联系人.txt': '联系人：李四\n身份证号：110101200010010011\n',
                '子目录/示例能源_明细.txt': '开户银行：中国工商银行天津滨海分行\n密码：Admin@2024\n',
            })

            config = service.build_config({
                'directory': str(base),
                'mode': 'combo',
                'rules': [{'keyword': '张三丰', 'replacement': '张某'}],
                'word_list': ['示例能源', '星海项目'],
                'field_ids': default_ids(),
                'mask': '***',
                'filename_mask': '已脱敏',
                'case_sensitive': True,
                'include_filenames': True,
                'include_contents': True,
                'recursive': True,
            })

            preview = service.scan_directory(config)
            self.assertNotIn('error', preview, preview.get('error'))
            self.assertEqual(preview['scanned_files'], 3)

            result = service.apply_changes(str(base), preview)
            self.assertEqual(result['errors'], [], f'落盘不应有错误: {result["errors"]}')
            self.assertEqual(result['applied_files'], 3)

            # 内容层面：所有敏感值都应消失
            for path in base.rglob('*'):
                if not path.is_file():
                    continue
                content = path.read_text(encoding='utf-8')
                for secret in KEY_SECRETS:
                    self.assertNotIn(secret, content, f'{path.name} 仍含敏感值: {secret}')

            # 文件名层面：手机号与敏感词不应残留
            names = [p.name for p in base.rglob('*') if p.is_file()]
            self.assertFalse(
                any('13800000000' in n for n in names),
                f'文件名仍含手机号: {names}',
            )
            self.assertFalse(
                any('示例能源' in n for n in names),
                f'文件名仍含敏感词: {names}',
            )
            self.assertTrue(
                any('已脱敏' in n for n in names),
                f'未产生预期的脱敏文件名: {names}',
            )

    def test_line_count_conserved_after_persisting_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write_tree(base, {'a.txt': RAW_DOC})
            config = service.build_config({
                'directory': str(base), 'mode': 'mask',
                'field_ids': default_ids(), 'mask': '***',
            })
            preview = service.scan_directory(config)
            self.assertNotIn('error', preview)
            service.apply_changes(str(base), preview)
            content = (base / 'a.txt').read_text(encoding='utf-8')
            self.assertEqual(
                len(content.splitlines()), len(RAW_DOC.splitlines()),
                '落盘后行数必须与原文一致',
            )

    def test_running_twice_is_stable(self):
        """第二次扫描不再产生内容变更（幂等）。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write_tree(base, {'a.txt': RAW_DOC})
            config = service.build_config({
                'directory': str(base), 'mode': 'mask',
                'field_ids': default_ids(), 'mask': '***',
                'filename_mask': '已脱敏',
            })
            first = service.scan_directory(config)
            service.apply_changes(str(base), first)
            second = service.scan_directory(config)
            self.assertEqual(second['total_content_changes'], 0)


if __name__ == '__main__':
    unittest.main()
