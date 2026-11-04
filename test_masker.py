"""脱敏引擎自检：验证内置字段库对国企样本文档的命中情况。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import masker

SAMPLE = """示例能源集团有限公司2024年度经营情况报告

一、企业基本情况
企业名称：示例能源集团有限公司
统一社会信用代码：91120100MA06XYQWY0
法定代表人：张三丰
董事长：陈志强
注册资本：500000万元人民币
成立日期：2005-03-18
注册地址：天津市滨海新区海河大道1888号
纳税人识别号：91120100MA06XYQWY0
企业性质：国有控股
所属行业：电力生产与供应
主管部门：天津市国有资产监督管理委员会
股票代码：600XXX
经营范围：发电、输电、供电及相关服务

二、联系方式
联系人：李四
联系电话：022-58889900
手机：13800000000
电子邮箱：zhangsan@example-energy.com
传真：022-58889901
开户银行：中国工商银行天津滨海分行
银行账号：6222021234567890120
邮政编码：300384

三、商业秘密
投标报价：3,850.00万元
中标价：3,720.50万元
单位成本：2,950.00万元
毛利率：18.5%
底价：3,600.00万元
客户名单：示例集团A、示例集团B、示例集团C
供应商名单：示例电缆厂、示例钢管厂
研发投入：12,800.00万元
技术方案：采用超超临界二次再热技术方案
设计图纸：DH-2024-001
产能：设计产能1200万吨
设计产能：1200万吨
库存量：85.6万吨

四、项目与合同
项目名称：示例能源220kV送出工程
合同编号：HT-2024-SD-0092
合同金额：8,640.00万元
招标编号：ZB-2024-0451
工期：18个月
中标单位：示例电力建设集团有限公司

五、人员信息
身份证号：110101200001010011
护照号：E12345678
银行卡号：6222021234567890120
年薪：85.00万元
绩效奖金：30.00万元
员工名单：张三丰、李四、王强、赵敏
社保基数：35,000元

六、凭证信息
密码：Admin@2024
api_key: sk-live-abcdef1234567890abcdef
数据库密码 = RootPass123

七、内部标识
密级：机密
绝密
仅供内部使用
本报告仅供内部参考，请勿外传
"""


def main():
    library = masker.load_field_library(Path(__file__).parent / 'sensitive_fields.json')
    ops = masker.build_ops({
        'mode': masker.MODE_MASK,
        'word_list': ['示例能源', '星海项目'],
        'field_ids': masker.default_field_ids(library),
        'mask': '***',
        'case_sensitive': True,
        'library': library,
    })

    out, results = masker.apply_ops(SAMPLE, ops)

    print('=' * 70)
    print('脱敏后内容：')
    print('=' * 70)
    print(out)
    print('=' * 70)
    print('命中统计：')
    total = 0
    for r in results:
        if r['count']:
            total += r['count']
            print(f"  [{r['source']:<8}] {r['label']:<24} {r['count']} 处")
    print(f"\n合计 {total} 处命中，未命中 {len([r for r in results if not r['count']])} 条规则")
    print(f"操作总数: {len(ops)}")

    leaks = [line for line in out.splitlines()
             if '13800000000' in line or '110101200001010011' in line
             or '91120100MA06XYQWY0' in line or 'sk-live-' in line
             or 'RootPass123' in line or '6222021234567890120' in line]
    if leaks:
        print('\n!!! 存在残留泄漏:')
        for l in leaks:
            print('   ', l)
    else:
        print('\n>>> 关键敏感值全部清除，无残留')

    src_lines = len(SAMPLE.splitlines())
    out_lines = len(out.splitlines())
    print(f'>>> 行数守恒检查: 原始 {src_lines} 行 / 输出 {out_lines} 行', end=' ')
    if src_lines == out_lines:
        print('-> OK（无换行被吞）')
    else:
        print('-> !!! 失败：换行符被吞掉')


if __name__ == '__main__':
    main()
