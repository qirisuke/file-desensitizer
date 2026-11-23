"""端到端测试：扫描 -> 预览 -> 落盘，验证文件确实被改写和重命名。"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import masker
import app as service

ROOT = Path(__file__).resolve().parent / '_e2e_tmp'
DOC = """示例能源集团有限公司2024年度经营情况报告
统一社会信用代码：91120100MA06XYQWY0
法定代表人：张三丰
注册资本：500000万元人民币
联系电话：022-58889900
手机：13800000000
投标报价：3,850.00万元
项目名称：示例能源220kV送出工程
内部代号：星海项目
密码：Admin@2024
"""


def setup():
    if ROOT.exists():
        shutil.rmtree(ROOT)
    d = ROOT / '示例能源_2024年度报告'
    d.mkdir(parents=True)
    (d / '示例能源_经营情况.txt').write_text(DOC, encoding='utf-8')
    (d / '13800000000_联系人.txt').write_text('联系人：李四\n身份证号：110101200001010011\n', encoding='utf-8')
    return d


def main():
    setup()
    config = service.build_config({
        'directory': str(ROOT),
        'mode': 'combo',
        'rules': [{'keyword': '张三丰', 'replacement': '张某'}],
        'word_list': ['示例能源', '星海项目'],
        'field_ids': masker.default_field_ids(service.FIELD_LIBRARY),
        'mask': '***',
        'case_sensitive': True,
        'include_filenames': True,
        'include_contents': True,
        'recursive': True,
    })

    preview = service.scan_directory(config)
    if 'error' in preview:
        print('扫描失败:', preview['error'])
        return 1
    print(f"扫描: {preview['scanned_files']} 个文件, "
          f"文件名变更 {preview['total_filename_changes']}, "
          f"内容替换 {preview['total_content_changes']} 处")

    print('\n预览中的文件名变更:')
    for c in preview['filename_changes']:
        print(f"  {c['old']}\n    -> {c['new']}")

    result = service.apply_changes(config, preview)
    print(f"\n落盘: 修改内容 {result['applied_files']} 个, 重命名 {result['applied_renames']} 个, 错误 {len(result['errors'])}")
    for e in result['errors']:
        print('  错误:', e)

    print('\n落盘后的实际目录结构:')
    for f in sorted(ROOT.rglob('*')):
        if f.is_file():
            print('  ', f.relative_to(ROOT))

    print('\n落盘后的文件内容:')
    for f in sorted(ROOT.rglob('*.txt')):
        print(f'--- {f.name} ---')
        print(f.read_text(encoding='utf-8').rstrip())

    # 断言
    failures = []
    names = [f.name for f in ROOT.rglob('*') if f.is_file()]
    if '13800000000_联系人.txt' in names:
        failures.append('手机号未被从文件名中清除')
    if not any('已脱敏_联系人.txt' == n for n in names):
        failures.append('联系人文件未按预期重命名')
    if not any('已脱敏_经营情况.txt' == n for n in names):
        failures.append('经营情况文件未按预期重命名')
    if any('示例能源' in n for n in names):
        failures.append('敏感词未从文件名中清除')

    all_text = '\n'.join(f.read_text(encoding='utf-8') for f in ROOT.rglob('*.txt'))
    for secret in ['13800000000', '91120100MA06XYQWY0', '110101200001010011',
                   '3,850.00万元', 'Admin@2024', '星海项目', '示例能源',
                   '张三丰', '022-58889900', '8,640']:
        if secret in all_text:
            failures.append(f'内容残留敏感值: {secret}')

    print()
    if failures:
        print('!!! 测试失败:')
        for f in failures:
            print('   -', f)
        return 1
    print('>>> 端到端测试全部通过：文件名与内容均已脱敏，无残留')
    return 0


if __name__ == '__main__':
    sys.exit(main())
