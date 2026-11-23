"""测试公共辅助：构造临时目录、样例文档、内置字段库加载。"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import masker  # noqa: E402

EXAMPLES_DIR = PROJECT_ROOT / 'examples'
BEFORE_SAMPLE = EXAMPLES_DIR / 'before' / '示例能源_经营情况.txt'
AFTER_SAMPLE = EXAMPLES_DIR / 'after' / '已脱敏_经营情况.txt'
FIELD_LIBRARY_PATH = PROJECT_ROOT / 'sensitive_fields.json'

# 一份包含真实敏感值的原始文档（对齐 examples/before 的业务语义）
RAW_DOC = """示例能源集团有限公司2024年度经营情况报告

一、企业基本情况
企业名称：示例能源集团有限公司
统一社会信用代码：91120100MA06XYQWY0
法定代表人：张三丰
注册资本：500000万元人民币

二、联系方式
手机：13800000000
电子邮箱：zhangsan@example-energy.com
银行账号：6222021234567890120

三、凭证信息
密码：Admin@2024
api_key: sk-live-abcdef1234567890abcdef
数据库密码 = RootPass123

四、身份证
身份证号：110101200010010011
"""

# 关键敏感值：端到端测试断言输出中不得出现原文
KEY_SECRETS = [
    '13800000000',
    '110101200010010011',
    '91120100MA06XYQWY0',
    'sk-live-',
    'RootPass123',
    '6222021234567890120',
    'zhangsan@example-energy.com',
    'Admin@2024',
]


def load_library():
    """加载项目内置的敏感字段库。"""
    return masker.load_field_library(FIELD_LIBRARY_PATH)


def default_ids(library=None):
    """内置字段库中默认启用的字段 id 列表。"""
    return masker.default_field_ids(library if library is not None else load_library())


def write_tree(root, files):
    """在 root 下写入 {相对路径: 文本内容} 字典（自动建父目录）。"""
    for rel, content in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding='utf-8')
    return Path(root)
