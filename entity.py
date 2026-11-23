"""实体泛化引擎。

把「公司名 / 人名」泛化成「客户A / 供应商B / 人员C」这类标签，而不是简单遮蔽。

核心语义
--------
- 同一实体在整个目录树内始终映射到同一个泛化名（跨文件一致）。
- 序号按实体首次出现顺序分配，类别由上下文线索判定。
- 机构名与项目名走可配置别名表（最长匹配优先）。

架构：两阶段
------------
第一阶段在**原文**上收集所有替换区间（别名 + 实体），第二阶段统一应用。
这样别名替换产生的结果（��如「某某农商银行」）不会再被实体识别二次处理。

上下文线索优先级：
    显式类别列 > 上下文表头 > 上下文短语 > 名称特征推断
"""
import json
import re
from pathlib import Path

# ------------------------------------------------------------ 类别定义

CATEGORY_CUSTOMER = '客户'
CATEGORY_SUPPLIER = '供应商'
CATEGORY_PERSON = '人员'

_GENERIC_LABELS = {
    CATEGORY_CUSTOMER: '客户',
    CATEGORY_SUPPLIER: '供应商',
    CATEGORY_PERSON: '人员',
}

# ------------------------------------------------------------ 组织机构名

# 长后缀在前，避免「有限公司」先于「股份有限公司」命中
ORG_SUFFIXES = [
    '股份有限公司', '有限责任公司', '集团有限公司', '有限公司',
    '合作社', '农商银行', '农商行', '农村商业银行',
    '银行', '支行', '分行', '营业部', '事务所', '集团', '公司',
]

_ORG_RE = re.compile(
    r'(?!与|及|和|跟|同|其|该)'
    r'([一-龥A-Za-z0-9]{2,18}?(?:' + '|'.join(ORG_SUFFIXES) + r'))(?![一-龥])'
)

# 这些词出现时说明不是「某个客户/供应商」，而是通用机构称呼
ORG_STOPWORDS = (
    '中国银行业监督管理委员会', '中国银保监会', '国家金融监督管理总局',
    '人民银行', '银行业保险监督管理', '监管局', '监管委员会',
)

# 以这些词开头说明是介词/动词短语，不是机构名
ORG_BAD_PREFIX = ('向', '由', '至', '到', '及', '和', '与', '并', '或',
                  '在', '对', '将', '把', '从', '为', '是', '的', '其', '该')

# 不应被当作机构名的词尾（普通名词）
ORG_EXCLUDE_TAIL = ('条例', '办法', '规定', '通知', '意见', '制度', '体系',
                    '建设', '项目', '工程', '会议', '报告', '名单', '情况',
                    '管理', '服务', '银行家', '集团军', '分公司')

# 供应商的强特征词（弱特征如「科技」「工程」会误判，不采用）
SUPPLIER_HINTS = ('农资', '种苗', '物资', '原材料', '包装', '物流', '运输')

# ------------------------------------------------------------ 人名

PERSON_LABELS = (
    '法定代表人', '法人代表', '企业法人', '董事长', '副董事长', '总经理',
    '副总经理', '总会计师', '财务负责人', '总工程师', '党委书记', '监事长',
    '监事会主席', '记录人', '主持人', '审批人', '联系人', '负责人', '经办人',
    '股东代表', '主要人员',
)

# 标签 + 冒号 + 人名。标签后可能带括号说明，如「法定代表人（签字）：陈国华」
# 末尾的 (?![一-龥A-Za-z]) 很重要：泛化结果「人员A」里的「人员」不该再被当成人名，
# 否则二次运行会变成「人员AA」。
_PERSON_LABEL_RE = re.compile(
    r'(' + '|'.join(PERSON_LABELS) + r')([（(][^）)]{0,6}[）)])?[ \t]*[:：=][ \t]*'
    r'([一-龥]{2,3})(?![一-龥A-Za-z])'
)

# 「客户联系人吴会彬家庭住址：」——人名后面必须跟着另一个标签才认
_PERSON_INLINE_RE = re.compile(
    r'((?:客户|供应商|企业)?(?:联系人|经办人|负责人))[ \t]*([一-龥]{2,3})(?=[一-龥]{2,6}[:：])'
)

# ------------------------------------------------------------ 上下文线索

CATEGORY_HINTS = {
    CATEGORY_SUPPLIER: ('供应商', '供货商', '厂商', '卖方'),
    CATEGORY_CUSTOMER: ('客户', '顾客', '买方'),
    CATEGORY_PERSON: ('姓名', '审批人', '员工', '高管', '股东', '个人'),
}

# 表头出现这些词 -> 该列是公司名（子串判定，用于「行标签」这类宽松上下文）
COMPANY_COLUMN_HINTS = ('客户名称', '单位名称', '企业名称', '公司名称',
                         '客户', '单位', '企业', '机构名称', '公司')

# 精确匹配的列名（用于表格里「标签 + 裸值」的场景，避免子串误判）
COMPANY_COLUMN_EXACT = frozenset({
    '客户名称', '单位名称', '企业名称', '公司名称', '机构名称',
    '客户', '单位', '企业', '公司', '股东名称', '往来单位',
})

# 甲乙方：甲方通常是本机构（走别名表），乙方是客户
PARTY_LABELS = ('甲方', '乙方', '丙方')

# 标签出现这些词 -> 后面的公司名归为客户
CUSTOMER_LABEL_HINTS = ('客户名称', '单位名称', '企业名称', '公司名称',
                         '建设单位', '客户', '需求方', '采购人', '招标人',
                         '乙方', '受托人')

# 标签出现这些词 -> 后面的公司名归为供应商
SUPPLIER_LABEL_HINTS = ('供应商名称', '供应商', '供货商', '厂商', '卖方')

# 名称前紧跟这些标签时，该名称是「公司类实体」
_LABELED_COMPANY_RE = re.compile(
    r'(客户名称|单位名称|企业名称|公司名称|建设单位|客户|采购人|招标人|'
    r'供应商名称|供应商|受托人)[ \t]*[:：=]?[ \t]*$'
)

# 甲乙方标签允许带修饰词，如「乙方全称：」「甲方（盖章）：」
_PARTY_BEFORE_RE = re.compile(
    r'(甲方|乙方|丙方)([（(][^）)]{0,4}[）)])?[ \t]*[:：=]?[ \t]*$'
)
_PARTY_AFTER_RE = re.compile(r'^[（(](甲方|乙方|丙方)[）)]')

# 甲乙方 + 修饰词（「乙方全称」「甲方盖章」等）
_PARTY_MODIFIER_RE = re.compile(
    r'(甲方|乙方|丙方)[一-龥]{0,4}([（(][^）)]{0,4}[）)])?[ \t]*[:：=]?[ \t]*$'
)


def load_aliases(path=None):
    if path is None:
        path = Path(__file__).resolve().parent / 'entity_aliases.json'
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {'organizations': {}, 'projects': {}}


class EntityMapper:
    """整个目录树共享一个实例，保证跨文件泛化名一致。"""

    def __init__(self, aliases=None):
        if aliases is None:
            aliases = load_aliases()
        self.aliases = aliases
        self.entity_to_generic = {}
        self.generic_to_entity = {}
        self._counters = {}
        self._alias_pairs = self._build_alias_pairs()

    # ------------------------------------------------------ 别名表

    def _build_alias_pairs(self):
        pairs = []
        for group in ('organizations', 'projects'):
            for src, dst in (self.aliases.get(group) or {}).items():
                if src and src != dst:
                    pairs.append((src, dst))
        pairs.sort(key=lambda p: len(p[0]), reverse=True)
        return pairs

    # ------------------------------------------------------ 泛化名分配

    def generic_for(self, entity: str, category: str) -> str:
        entity = entity.strip()
        if not entity:
            return entity
        existing = self.entity_to_generic.get(entity)
        if existing:
            return existing
        n = self._counters.get(category, 0) + 1
        self._counters[category] = n
        letter = chr(ord('A') + n - 1) if n <= 26 else f'A{n}'
        generic = f'{_GENERIC_LABELS[category]}{letter}'
        self.entity_to_generic[entity] = generic
        self.generic_to_entity[generic] = entity
        return generic

    # ------------------------------------------------------ 类别判定

    @staticmethod
    def category_from_context(context: str) -> str:
        ctx = context or ''
        for cat in (CATEGORY_SUPPLIER, CATEGORY_CUSTOMER, CATEGORY_PERSON):
            for hint in CATEGORY_HINTS[cat]:
                if hint in ctx:
                    return cat
        return ''

    @staticmethod
    def category_of_company(name: str) -> str:
        for hint in SUPPLIER_HINTS:
            if hint in name:
                return CATEGORY_SUPPLIER
        return CATEGORY_CUSTOMER

    @staticmethod
    def category_from_label(label: str) -> str:
        """从标签（如「建设单位」「乙方」）推断公司类别。"""
        for hint in SUPPLIER_LABEL_HINTS:
            if hint in label:
                return CATEGORY_SUPPLIER
        for hint in CUSTOMER_LABEL_HINTS:
            if hint in label:
                return CATEGORY_CUSTOMER
        return ''

    @staticmethod
    def _is_company_context(context: str) -> bool:
        """上下文是否是「公司名类」（表头像公司名列）。"""
        ctx = context or ''
        if not ctx:
            return False
        for cat, hints in CATEGORY_HINTS.items():
            for hint in hints:
                if hint in ctx:
                    return False   # 明确是人名类
        return any(hint in ctx for hint in COMPANY_COLUMN_HINTS)

    # ------------------------------------------------------ 实体筛选

    def _looks_like_org(self, name: str) -> bool:
        name = name.strip()
        if len(name) < 4:
            return False
        if any(stop in name for stop in ORG_STOPWORDS):
            return False
        if any(name.endswith(t) for t in ORG_EXCLUDE_TAIL):
            return False
        if name[0] in ORG_BAD_PREFIX:
            return False
        if name.startswith('某某') or name.startswith('__'):
            return False
        # 必须是「前缀 + 后缀」，且前缀本身像名称（不能是单字）
        m = _ORG_RE.fullmatch(name)
        if not m:
            return False
        prefix = name[:-len(_suffix_of(name))]
        if len(prefix) < 2:
            return False
        return True

    # ------------------------------------------------------ 主入口

    def map_text(self, text: str, context: str = '', prev_context: str = '') -> str:
        """泛化文本中的实体。

        context      当前单元的上下文（表头 / 标签），例如 '客户名称'、'单位名称 类别=供应商'
        prev_context 上一单元延续下来的上下文（段落级），例如 '供应商名单'
        """
        if not text:
            return text

        # 候选区间：(start, end, kind, payload, priority)
        # kind='alias' -> payload 是替换文本
        # kind='entity' -> payload 是 (名称, 类别)
        spans = []

        for src, dst in self._alias_pairs:
            start = 0
            while True:
                i = text.find(src, start)
                if i < 0:
                    break
                spans.append((i, i + len(src), 'alias', dst, 100 + len(src)))
                start = i + len(src)

        for m in _PERSON_LABEL_RE.finditer(text):
            spans.append((m.start(3), m.end(3), 'entity',
                          (m.group(3), CATEGORY_PERSON), 50))

        for m in _PERSON_INLINE_RE.finditer(text):
            spans.append((m.start(2), m.end(2), 'entity',
                          (m.group(2), CATEGORY_PERSON), 50))

        ctx_cat = self.category_from_context(context)
        carry_cat = self.category_from_context(prev_context)
        ctx_is_company = self._is_company_context(context)
        for m in _ORG_RE.finditer(text):
            name = m.group(1)
            if not self._looks_like_org(name):
                continue
            cat = ctx_cat or self._label_category(text, m.start(1)) or carry_cat
            if not cat:
                cat = self._party_category(text, m.start(1), m.end(1))
            if not cat:
                if not ctx_is_company:
                    # 没有公司类上下文 —— 只让别名表处理，不登记为客户/供应商
                    continue
                cat = self.category_of_company(name)
            spans.append((m.start(1), m.end(1), 'entity', (name, cat), 10))

        # 去重叠后，按文本顺序分配泛化名（保证编号顺序稳定）
        chosen = self._select_spans(spans)
        for start, end, kind, payload, _p in sorted(chosen, key=lambda s: s[0]):
            if kind == 'entity':
                name, cat = payload
                payload = self.generic_for(name, cat)
                chosen = [(s, e, k, p, pr) if not (s == start and e == end) else (s, e, k, payload, pr)
                          for (s, e, k, p, pr) in chosen]
        return self._apply_spans(text, chosen)

    @staticmethod
    def _label_category(text: str, name_start: int) -> str:
        """看名称前面的文字，判断它是否跟在公司类标签后面。"""
        prefix = text[max(0, name_start - 14):name_start]
        m = _LABELED_COMPANY_RE.search(prefix)
        if not m:
            return ''
        return EntityMapper.category_from_label(m.group(1))

    @staticmethod
    def _party_category(text: str, name_start: int, name_end: int) -> str:
        """甲乙方标注：甲方是本机构（走别名表），乙方算客户。"""
        before = text[max(0, name_start - 16):name_start]
        for pattern in (_PARTY_MODIFIER_RE, _PARTY_BEFORE_RE):
            m = pattern.search(before)
            if m:
                return CATEGORY_CUSTOMER if m.group(1) == '乙方' else ''
        after = text[name_end:name_end + 8]
        m = _PARTY_AFTER_RE.match(after)
        if m:
            return CATEGORY_CUSTOMER if m.group(1) == '乙方' else ''
        return ''

    @staticmethod
    def _select_spans(spans: list) -> list:
        """按优先级去重叠。优先级高者胜；同级起点靠前者胜。"""
        if not spans:
            return []
        spans = sorted(spans, key=lambda s: (-s[4], s[0], -(s[1] - s[0])))
        chosen = []
        occupied = []
        for span in spans:
            start, end = span[0], span[1]
            if any(start < e and s < end for s, e in occupied):
                continue
            chosen.append(span)
            occupied.append((start, end))
        return chosen

    @staticmethod
    def _apply_spans(text: str, spans: list) -> str:
        """应用替换区间。"""
        if not spans:
            return text
        chosen = sorted(spans, key=lambda s: s[0])
        out = []
        pos = 0
        for start, end, kind, payload, _prio in chosen:
            if start < pos:
                continue
            out.append(text[pos:start])
            out.append(payload if kind == 'alias' else payload)
            pos = end
        out.append(text[pos:])
        return ''.join(out)

    # ------------------------------------------------------ 表格感知

    def map_value(self, value: str, label: str = '') -> str:
        """按「标签 + 裸值」泛化（表格里标签与值分列时的用法）。

        label 是该值所在列/行的标签，例如 '法定代表人'、'客户名称'、'审批人'。
        标签判定用**精确匹配**而非子串包含 —— 否则「客户总数」这种指标名
        会被误当成「客户」列，把整行值都当公司名处理。
        """
        if not value:
            return value
        label = (label or '').strip()

        # 人名类标签 -> 值当人名处理
        if label in PERSON_LABELS:
            return self.generic_for(value.strip(), CATEGORY_PERSON)

        # 公司类标签 -> 值当公司处理
        if label in COMPANY_COLUMN_EXACT or label in PARTY_LABELS:
            cat = self.category_from_label(label) or self.category_from_context(label)
            if not cat:
                cat = self.category_of_company(value)
            return self.generic_for(value.strip(), cat)

        # 类别列 -> 值按该类别处理
        cat = self.category_from_context(label)
        if cat and cat != CATEGORY_PERSON and self._looks_like_org(value):
            return self.generic_for(value.strip(), cat)

        return value

    def map_row(self, header, values, row_dict=None):
        """按表头逐列泛化一行。"""
        out = []
        for i, value in enumerate(values):
            col = header[i] if i < len(header) else ''
            ctx = col
            if row_dict:
                extra = self._category_from_row(row_dict)
                if extra:
                    ctx = f'{col} 类别={extra}'
            out.append(self.map_text(value, ctx))
        return out

    @staticmethod
    def _category_from_row(row_dict) -> str:
        for key, value in (row_dict or {}).items():
            if key and ('类别' in key or '类型' in key or '身份' in key):
                v = (value or '').strip()
                for cat, hints in CATEGORY_HINTS.items():
                    for hint in hints:
                        if hint in v:
                            return cat
        return ''

    # ------------------------------------------------------ 映射表导入导出

    def export_map(self) -> dict:
        return {
            'entities': dict(self.entity_to_generic),
            'counters': dict(self._counters),
        }

    def import_map(self, data: dict) -> None:
        for entity, generic in (data.get('entities') or {}).items():
            self.entity_to_generic[entity] = generic
            self.generic_to_entity[generic] = entity
            cat = self._category_of_generic(generic)
            letter = generic[len(_GENERIC_LABELS.get(cat, '')):]
            if letter.isalpha():
                n = ord(letter.upper()) - ord('A') + 1
            elif letter.isdigit():
                n = int(letter)
            else:
                n = 1
            self._counters[cat] = max(self._counters.get(cat, 0), n)

    @staticmethod
    def _category_of_generic(generic: str) -> str:
        for cat, label in _GENERIC_LABELS.items():
            if generic.startswith(label):
                return cat
        return CATEGORY_CUSTOMER

    def reset(self) -> None:
        self.entity_to_generic.clear()
        self.generic_to_entity.clear()
        self._counters.clear()


def _suffix_of(name: str) -> str:
    """返回名称匹配到的后缀。"""
    for s in ORG_SUFFIXES:
        if name.endswith(s):
            return s
    return ''
