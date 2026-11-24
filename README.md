# 文件脱敏工具

> 本地离线运行的文本文件脱敏服务 —— 关键词全文替换 + 敏感词屏蔽 + 内置国企敏感字段库。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![Offline](https://img.shields.io/badge/运行-完全离线-success.svg)](SECURITY.md)

**完全离网运行**：只做本地文件读写，不发起任何对外网络请求，不调用任何大模型 API。
打包成单文件 EXE 后，目标机器无需安装 Python、无需联网。

---

## 特性

| 能力 | 说明 |
|---|---|
| 🔄 关键词替换 | 关键词 → 替代词，可配多条，按顺序执行 |
| 🛡️ 敏感词屏蔽 | 自定义词表（可导入文件）+ 内置国企字段库，固定掩码 |
| 🏷️ 实体泛化 | 公司名、人名 → `客户A`、`人员B`，**跨文件全局一致** |
| 🔗 别名替换 | 机构名、项目名 → `某某农商银行`、`某某重点项目`，可编辑 |
| 📁 文件名处理 | 文件名与文件内容**同时**脱敏，文件名掩码自动适配 Windows 非法字符 |
| 📊 Office 支持 | xlsx（多 sheet）、docx（段落+表格），**零 pip 依赖**，保真读写 |
| 🏢 国企字段库 | 内置 **8 大类 74 项**敏感字段规则，纯 JSON 可编辑扩充 |
| 👁️ 预览后执行 | 先扫描列出全部命中项，确认后才真正改写文件 |
| 📦 免安装 EXE | PyInstaller 单文件，拷贝即用 |
| 🔒 零联网 | 有自检脚本证明不发起任何对外连接 |

---

## 快速开始

### 方式 A：直接运行 EXE（推荐，零依赖）

下载 `文件脱敏工具.exe`，双击即可。控制台会打印访问地址并自动打开浏览器。

> 若 5000 端口被占用，会自动顺延到 5001、5002……
> 服务只监听 `127.0.0.1`，局域网内其他机器无法访问。

### 方式 B：源码运行（**内网断网环境用这个**）

只要有 **Python 3.8+** 就能跑，**不需要 pip install 任何东西**：

```bash
python server_stdlib.py
```

后端用 Python 标准库（`http.server`）实现，接口与 Flask 版完全一致。

直接 `python app.py` 也行 —— 检测不到 Flask 时会自动回退到标准库服务器。

### 方式 C：有 Flask 时

```bash
pip install -r requirements.txt
python app.py
```

Flask 版会额外自动打开浏览器。

### 方式 D：自己打包

```bash
build.bat
```

产物为 `dist/文件脱敏工具.exe`（约 16 MB 单文件）。

> **内网若禁止运行 EXE**，用方式 B：把项目目录拷过去运行
> `python server_stdlib.py`。只需要 11 个文件：
> `app.py` `core.py` `masker.py` `entity.py` `office_io.py` `pipeline.py`
> `server_stdlib.py` `sensitive_fields.json` `entity_aliases.json`
> `templates/index.html`

### 排查

访问 `http://127.0.0.1:<端口>/api/health` 可查看后端类型、版本、Python 版本：

```json
{
  "ok": true,
  "version": "1.2.0",
  "backend": "stdlib",
  "python": "3.13.11",
  "flask_available": false,
  "field_count": 72,
  "categories": 8
}
```

页面顶部也显示同样的信息。若那里显示「⚠ 后端未就绪」，
说明浏览器访问的地址与服务实际监听的地址不是同一个 —— 请用控制台打印的地址。

---

## 使用流程

1. **填写目标目录** —— 支持绝对路径；可选是否递归子目录、是否替换文件名、是否替换文件内容
2. **选择处理模式**

   | 模式 | 行为 |
   |---|---|
   | 组合（先屏蔽后替换） | 两套规则都启用 |
   | 仅关键词替换 | 只做关键词 → 替代词 |
   | 仅敏感词屏蔽 | 只做敏感内容固定掩码 |

3. **配置规则**
   - *关键词替换规则*：关键词 → 替代词，可添加多条
   - *敏感词屏蔽*：
     - 内置国企敏感字段库（可逐项勾选）
     - 自定义敏感词表（每行一个，可导入 `.txt` / `.csv`）
     - 内容掩码（默认 `***`）、文件名掩码（默认 `已脱敏`）、是否区分大小写
   - *实体泛化*：把公司名、人名替换成 `客户A` / `人员B`，同一实体在整个目录树内始终一致
   - *别名表*：机构名、项目名的固定映射，每行一条 `原文 = 泛化名`
4. **扫描预览** —— 列出所有命中项、每处替换次数、脱敏样例、实体映射表，**不改任何文件**
5. **确认执行** —— 真正改写文件并重命名

执行顺序：

```
别名替换 → 实体泛化 → 自定义敏感词表 → 内置字段库（标签型）→ 内置字段库（纯值型）→ 关键词替换
```

### 支持的文件格式

| 格式 | 处理方式 |
|---|---|
| `.txt` `.md` `.csv` | 按行处理；Markdown / CSV 表格做表格感知（表头、行标签都能识别） |
| `.xlsx` | 逐 sheet 处理，保留 sheet 名与顺序、单元格类型、列宽、样式 |
| `.docx` | 逐段落与表格处理，保留段落样式、表格结构、页眉页脚 |

Office 读写只用标准库 `zipfile` + `xml.etree` 实现，**不需要 openpyxl / python-docx**。

### 脱敏前后对照

`examples/` 下有完整对照样例，由工具自身生成。

**脱敏前**

```text
企业名称：示例能源集团有限公司
统一社会信用代码：91120100MA06XYQWY0
法定代表人：张三丰
手机：13800000000
投标报价：3,850.00万元
客户名单：示例集团A、示例集团B
```

**脱敏后**

```text
企业名称：***
统一社会信用代码：***
法定代表人：人员A
手机：***
投标报价：***
客户名单：***
```

实体泛化示例（同一实体在所有文件里始终一致）：

```text
海口椰风农产品有限公司  →  客户A
临春椰子深加工有限公司  →  客户B
海南农资供应链有限公司  →  供应商A
陈国华                →  人员A
```

---

## 内置国企敏感字段库

定义在 `sensitive_fields.json`，纯 JSON，改完重启即生效，不需要动代码。

| 分类 | 字段数 | 覆盖内容 |
|---|---:|---|
| 国企主体标识信息 | 14 | 统一社会信用代码、纳税人识别号、组织机构代码、法定代表人、董监高、注册资本、成立日期、注册地址、企业名称、股票代码、股票简称、主管部门、经营范围、企业性质 |
| 联系方式与银行账户 | 8 | 手机号、联系电话、传真、邮箱、银行账号、开户行、社保/公积金账号、邮编、即时通讯账号 |
| 商业秘密与经营数据 | 13 | 报价/中标价、成本价、毛利率、底价、客户名单、供应商名单、研发投入、技术方案、图纸、技术参数、产能产量、库存量、战略规划 |
| 项目与合同信息 | 7 | 项目名称、合同编号、合同金额、招标编号、工期、投标/中标主体、招投标文件 |
| 人员薪酬与个人信息 | 8 | 身份证号、护照号、银行卡号、年薪月薪、绩效奖金、员工名单、社保基数 |
| 密级与内部标识 | 3 | 密级标注、绝密/机密/内部资料等字样、页眉水印编号 |
| 账号密码与密钥 | 3 | 密码/口令、API Key / Token、私钥与证书密码 |
| 银行业经营指标与授信 | 18 | 存款余额、涉农贷款余额、不良贷款率、营业收入、净利润、资本充足率、客户总数、授信额度、放款余额、合作额度、社保基数、采购成本、毛利率、出生日期、家庭住址、企业微信号、银行卡号（裸值）、社保账号（裸值） |

字段默认全部勾选，可在界面上逐项取消。扩充规则的方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 离线保证

`test_offline.py` 从三个层面证明脱敏全程不联网：

1. **静态审计源码** —— 确认无 `requests` / `urllib.request` / `httpx` / `aiohttp` / `openai` / `anthropic` 等引用
2. **运行时拦截** —— 用 tripwire 替换 `socket.socket.connect`，任何指向非 `127.0.0.1` 的连接立即抛错
3. **模块审计** —— 确认运行时未加载任何第三方联网 / 模型 SDK

```bash
PYTHONIOENCODING=utf-8 python test_offline.py
```

---

## 测试

```bash
PYTHONIOENCODING=utf-8 python test_masker.py           # 引擎：命中统计、残留检查、行数守恒
PYTHONIOENCODING=utf-8 python test_e2e.py              # 端到端：扫描 -> 落盘 -> 断言
PYTHONIOENCODING=utf-8 python test_offline.py          # 离线保证
PYTHONIOENCODING=utf-8 python tests/test_office_io.py  # Office 读写保真
PYTHONIOENCODING=utf-8 python tests/test_entity.py     # 实体泛化：识别、一致性、别名
PYTHONIOENCODING=utf-8 python tests/test_service_e2e.py # 服务级：HTTP API 全流程
```

所有脚本都必须以退出码 0 结束。

> Windows 下建议设 `PYTHONIOENCODING=utf-8`，否则中文输出会乱码。

---

## API

服务提供 HTTP API，便于接入自动化流程。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/` | 前端界面 |
| `GET` | `/api/health` | 健康检查：后端类型、版本、Python 版本 |
| `GET` | `/api/field-library` | 返回内置字段库与默认勾选项 |
| `GET` | `/api/aliases` | 返回默认别名表 |
| `POST` | `/api/scan` | 扫描预览，返回命中明细、样例、实体映射表 |
| `POST` | `/api/apply` | 按预览结果落盘（改写内容 + 重命名） |

`POST /api/scan` 请求体：

```json
{
  "directory": "D:\\data",
  "mode": "combo",
  "rules": [{ "keyword": "示例能源", "replacement": "示例集团" }],
  "word_list": ["星海项目"],
  "field_ids": ["usci", "legal_rep"],
  "mask": "***",
  "filename_mask": "已脱敏",
  "case_sensitive": true,
  "include_filenames": true,
  "include_contents": true,
  "recursive": true,
  "generalize_entities": true,
  "aliases": {
    "organizations": { "海南农商银行": "某某农商银行" },
    "projects": { "澄迈县椰果供应链建设项目": "某某重点项目" }
  }
}
```

---

## 已知约束

- **内容掩码与文件名掩码分开**：Windows 文件名不允许 `* ? " / \ < > | :`。若用 `***` 做文件名掩码，重命名会失败（`WinError 123`）。因此界面提供两个独立输入框，文件名掩码中的非法字符会自动替换为下划线。
- **只重命名文件，不重命名目录**：目标目录本身不会改名，否则路径失效。目录树中各层级的**文件**名都会被处理。
- **直接改写原文件**：执行前请确认预览结果，重要文件建议先备份。
- **正则匹配的边界**：少数文档（标签与值分两行书写）可能不被命中，可用自定义敏感词表补充。
- **泛化名的编号顺序**：按实体首次出现顺序分配，同一实体跨文件一致。期望编号若与既有台账不符，可用 `EntityMapper.import_map()` 导入映射表对齐。
- **「密级与内部标识」分类会改章节标题**：它会屏蔽「商业秘密」「仅供内部使用」等字样。不需要时取消勾选该分类。

---

## 项目结构

```
文件脱敏工具/
├── app.py                      # 服务入口（有 Flask 用 Flask，没有就自动回退）
├── server_stdlib.py            # 纯标准库 HTTP 服务器（零 pip 依赖）
├── core.py                     # 业务逻辑（扫描 / 落盘），两种后端共用
├── masker.py                   # 脱敏引擎（扫描与落盘共用同一执行路径）
├── pipeline.py                 # 集成流水线（表格语义 + 各格式调度）
├── entity.py                   # 实体泛化引擎（公司名、人名、别名）
├── entity_aliases.json         # 默认别名表
├── office_io.py                # xlsx / docx 读写（纯标准库）
├── sensitive_fields.json       # 内置国企敏感字段库（可编辑扩充）
├── templates/
│   └── index.html              # 前端界面
├── examples/                   # 脱敏前后对照样例（由工具自身生成）
├── tests/                      # 各模块自检脚本
├── build.bat                   # 打包脚本（PyInstaller 单文件 EXE）
├── start.bat                   # 启动脚本（优先用已打包 EXE）
├── requirements.txt            # Flask 为可选依赖
├── test_masker.py              # 引擎自检
├── test_e2e.py                 # 端到端测试
└── test_offline.py             # 离线自检

---

## 贡献

欢迎提交 Issue 和 PR。请先读 [CONTRIBUTING.md](CONTRIBUTING.md)——尤其是「零网络依赖」和「扫描与落盘共用执行路径」两条硬约束。

安全问题请见 [SECURITY.md](SECURITY.md)。

---

## 许可证

[MIT](LICENSE) © 2026 文件脱敏工具贡献者
