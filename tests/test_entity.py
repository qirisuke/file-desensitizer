"""实体泛化引擎自检。

验证：
1. 单元行为：给定文本+上下文，输出正确
2. 跨文件一致性：同一实体在 6 个测试文件里得到同一泛化名
3. 别名最长优先：不会被短串抢命中
4. 类别归属正确：客户 / 供应商 / 人员

用法：PYTHONIOENCODING=utf-8 python tests/test_entity.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import entity
import office_io

DATA = Path(r'C:\Users\cheng\Desktop\程富雕AI\测试文档\脱敏测试数据\原始文档')

FAILURES = []


def check(cond, msg, detail=''):
    if cond:
        print(f'  [OK]   {msg}')
    else:
        print(f'  [FAIL] {msg}')
        if detail:
            print(f'         {detail}')
        FAILURES.append(msg)


def test_unit():
    print('\n--- 单元测试 ---')
    m = entity.EntityMapper()

    r = m.map_text('法定代表人：张三丰', '法定代表人')
    check('人员A' in r, '标签型人名被泛化', f'实际: {r}')

    r = m.map_text('客户联系人吴会彬家庭住址：文昌市文城镇城东路33号')
    check('人员' in r and '吴会彬' not in r, '内联人名被泛化', f'实际: {r}')

    r = m.map_text('海口椰风农产品有限公司', '客户名称')
    check(r == '客户A', '表头给出客户类别时归为客户', f'实际: {r}')

    m2 = entity.EntityMapper()
    r = m2.map_text('海南农资供应链有限公司', '单位名称 类别=供应商')
    check(r == '供应商A', '类别列给出供应商时归为供应商', f'实际: {r}')

    m3 = entity.EntityMapper()
    r = m3.map_text('海南热带种苗科技有限公司', '单位名称')
    check(r == '供应商A', '名称特征推断为供应商', f'实际: {r}')

    m4 = entity.EntityMapper()
    r = m4.map_text('澄迈热带果蔬合作社', '客户名称')
    check(r == '客户A', '合作社归为客户', f'实际: {r}')


def test_alias():
    print('\n--- 别名测试 ---')
    m = entity.EntityMapper()
    r = m.map_text('海南农商银行海口支行')
    check(r == '某某农商银行某某支行', '长名优先，未被短串抢命中', f'实际: {r}')

    m2 = entity.EntityMapper()
    r = m2.map_text('海南农商银行')
    check(r == '某某农商银行', '普通机构名按别名表替换', f'实际: {r}')

    m3 = entity.EntityMapper()
    r = m3.map_text('农信社海口营业部')
    check(r == '某某信用社海口营业部', '农信社营业部按别名替换', f'实际: {r}')

    m4 = entity.EntityMapper()
    r = m4.map_text('澄迈县椰果供应链建设项目')
    check(r == '某某重点项目', '项目名按别名替换', f'实际: {r}')

    m5 = entity.EntityMapper()
    r = m5.map_text('中国银行业监督管理委员会海南监管局')
    check(r == '中国银行业监督管理委员会海南监管局', '监管机构不被误伤', f'实际: {r}')

    m6 = entity.EntityMapper()
    r = m6.map_text('《海南省农村金融条例》')
    check('条例' in r and '海南省农村金融条例' in r, '法规名称不被误伤', f'实际: {r}')


def test_cross_file():
    print('\n--- 跨文件一致性 ---')
    m = entity.EntityMapper()
    seen = {}          # 原始实体 -> 泛化名
    conflicts = []
    order = []

    for f in sorted(DATA.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() in ('.xlsx', '.docx'):
            units = office_io.read_units(f)
            for u in units:
                text = u[-1]
                ctx = _infer_context(u)
                new = m.map_text(text, ctx)
                _track(m, seen, conflicts, order, text, new)
        else:
            text = f.read_text(encoding='utf-8')
            new = m.map_text(text, '')
            _track(m, seen, conflicts, order, text, new)

    check(not conflicts, '同一实体在所有文件里泛化名一致', '\n         '.join(conflicts[:5]))

    # 类别正确性
    wrong = []
    for generic, entity_text in m.generic_to_entity.items():
        cat = m._category_of_generic(generic)
        if cat == entity.CATEGORY_SUPPLIER and not any(
                h in entity_text for h in entity.SUPPLIER_HINTS):
            wrong.append(f'{generic}={entity_text} 判为供应商但无供应特征')
        if cat == entity.CATEGORY_CUSTOMER and any(
                h in entity_text for h in entity.SUPPLIER_HINTS):
            wrong.append(f'{generic}={entity_text} 判为客户但有供应特征')
    check(not wrong, '客户/供应商类别判定正确', '\n         '.join(wrong[:5]))

    print('\n  最终映射表:')
    for generic in sorted(m.generic_to_entity, key=lambda g: (m._category_of_generic(g), g)):
        print(f'    {generic:<8} = {m.generic_to_entity[generic]}')


def _track(m, seen, conflicts, order, text, new):
    """对比原文中出现的实体与泛化结果是否自洽。"""
    for generic in m.generic_to_entity:
        if generic in new:
            src = m.generic_to_entity[generic]
            if src in seen and seen[src] != generic:
                conflicts.append(f'{src}: {seen[src]} vs {generic}')
            seen.setdefault(src, generic)
            if generic not in order:
                order.append(generic)


def _infer_context(unit):
    """从 xlsx/docx 单元推断上下文。"""
    kind = unit[0]
    if kind == 'cell':
        return ''   # 表格上下文由集成层按列提供
    return ''


def main():
    if not DATA.exists():
        print('找不到测试数据:', DATA)
        return 1
    test_unit()
    test_alias()
    test_cross_file()
    print()
    print('=' * 60)
    if FAILURES:
        print(f'失败 {len(FAILURES)} 项')
        for f in FAILURES:
            print('  -', f)
        return 1
    print('全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
