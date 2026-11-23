"""集成测试：用完整流水线处理测试数据，输出到评测目录。

用法：PYTHONIOENCODING=utf-8 python tests/test_pipeline_e2e.py [输出目录]
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import entity
import masker
import office_io
import pipeline
import app as service

DATA = Path(r'C:\Users\cheng\Desktop\程富雕AI\测试文档\脱敏测试数据')
ORIGINAL = DATA / '原始文档'
DEFAULT_OUT = DATA / '_评测输出'


def run(out_dir):
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    mapper = entity.EntityMapper()
    config = service.build_config({
        'directory': str(ORIGINAL),
        'mode': 'combo',
        'rules': [],
        'word_list': [],
        'field_ids': masker.default_field_ids(service.FIELD_LIBRARY),
        'mask': '***',
        'filename_mask': '已脱敏',
        'case_sensitive': True,
        'include_filenames': False,   # 期望结果文件名不变
        'include_contents': True,
        'recursive': True,
    })

    pipe = pipeline.Pipeline(config, service.FIELD_LIBRARY, mapper)

    for src in sorted(ORIGINAL.iterdir()):
        if not src.is_file():
            continue
        dst = out_dir / src.name
        suffix = src.suffix.lower()

        if suffix in ('.xlsx', '.docx'):
            if suffix == '.xlsx':
                changes, matched = pipe.process_xlsx(src)
            else:
                changes, matched = pipe.process_docx(src)
            office_io.write_units(src, dst, changes)
        elif suffix == '.csv':
            text = src.read_text(encoding='utf-8')
            new_text, matched = pipe.process_csv(text)
            dst.write_text(new_text, encoding='utf-8')
        else:
            text = src.read_text(encoding='utf-8')
            new_text, matched = pipe.process_plain_text(text)
            dst.write_text(new_text, encoding='utf-8')

        hits = sum(m.get('count', 0) for m in matched)
        print(f'  {src.name:<45} 命中 {hits} 处')

    print()
    print('泛化映射表:')
    for generic in sorted(mapper.generic_to_entity,
                           key=lambda g: (mapper._category_of_generic(g), g)):
        print(f'  {generic:<8} = {mapper.generic_to_entity[generic]}')

    print(f'\n输出目录: {out_dir}')
    return out_dir


if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    print('=== 集成流水线运行 ===')
    run(target)
