"""基准语料生成器：为 bench.py 构造可复现的真实规模数据。

- 大文件：把示例能源_经营情况.txt 的"脱敏前"版本扩展到约 200-500 KB
- 小目录：500-1000 个文本文件

用法:
    python bench_corpus.py            # 生成到 .bench_corpus/
    python bench_corpus.py --force    # 强制重建
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORPUS_DIR = ROOT / '.bench_corpus'

# 脱敏前的真实样式例（与 examples/README.md 描述一致：号码为虚构值）
BASE_DOC = """示例能源集团有限公司2024年度经营情况报告

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


def build_big_file(target_bytes: int) -> Path:
    """把 BASE_DOC 重复扩展到接近 target_bytes。"""
    out = CORPUS_DIR / 'big'
    out.mkdir(parents=True, exist_ok=True)
    path = out / '示例能源_经营情况_大文件.txt'

    chunks = []
    total = 0
    unit = BASE_DOC
    # 加编号让每份副本内容不完全相同（更接近真实重复文档）
    i = 0
    while total < target_bytes:
        body = unit.replace('2024年度', f'{2024 + i % 5}年度')
        body = f'【第 {i + 1} 份副本】\n{body}'
        chunks.append(body)
        total += len(body.encode('utf-8'))
        i += 1
    path.write_text(''.join(chunks), encoding='utf-8')
    return path


def build_small_dir(count: int) -> Path:
    """构造 count 个小文件，分布在若干子目录中。"""
    out = CORPUS_DIR / 'small'
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    templates = [
        ('{d}/示例能源_经营情况_{n}.txt', BASE_DOC),
        ('{d}/13800000000_联系人_{n}.txt',
         '联系人：李四\n身份证号：110101200001010011\n手机：13800000000\n'),
        ('{d}/合同_{n}.md',
         '# 合同摘要\n合同编号：HT-2024-SD-{n:04d}\n合同金额：8,640.00万元\n'
         '投标报价：3,850.00万元\n甲方：示例能源集团有限公司\n'),
        ('{d}/台账_{n}.csv',
         '序号,项目名称,合同金额,负责人\n1,示例能源220kV送出工程,8640.00万元,张三丰\n'
         '2,示例能源110kV线路工程,3920.50万元,陈志强\n'),
    ]
    for i in range(count):
        rel, content = templates[i % len(templates)]
        sub = f'sub{i % 7:02d}'
        p = out / rel.format(d=sub, n=i)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding='utf-8')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--big-bytes', type=int, default=300_000)
    ap.add_argument('--small-count', type=int, default=700)
    args = ap.parse_args()

    if args.force and CORPUS_DIR.exists():
        shutil.rmtree(CORPUS_DIR)
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    big = build_big_file(args.big_bytes)
    small = build_small_dir(args.small_count)

    n_files = sum(1 for p in small.rglob('*') if p.is_file())
    print(f'语料目录: {CORPUS_DIR}')
    print(f'  大文件: {big}  ({big.stat().st_size} bytes)')
    print(f'  小文件: {n_files} 个, {small}')


if __name__ == '__main__':
    sys.exit(main())
