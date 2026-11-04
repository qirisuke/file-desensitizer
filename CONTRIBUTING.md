# 贡献指南

感谢你对这个工具的关注。

## 开发环境

```bash
git clone <repo-url>
cd desensitize-service
pip install -r requirements.txt
python app.py
```

依赖只有 Flask。修改前端不需要构建步骤——直接编辑 `templates/index.html`，刷新浏览器即可。

## 提交前必跑测试

```bash
PYTHONIOENCODING=utf-8 python test_masker.py    # 引擎：命中统计、残留检查、行数守恒
PYTHONIOENCODING=utf-8 python test_e2e.py       # 端到端：扫描 -> 落盘 -> 断言
PYTHONIOENCODING=utf-8 python test_offline.py   # 离线保证：拦截并审计对外连接
```

三个脚本都必须以退出码 0 结束。Windows 下建议设 `PYTHONIOENCODING=utf-8`，否则中文输出会乱码。

## 代码约定

- **保持零网络依赖**：不要引入 `requests` / `httpx` / `urllib.request` 或任何大模型 SDK。
  `test_offline.py` 会静态审计源码并拦截一切非本机连接，新增依赖会让该测试失败。
- **扫描与落盘必须共用同一条执行路径**：预览结果要和实际写入结果完全一致。
  新增规则时，请同时在 `masker.apply_ops` 里实现，不要在 `scan_directory` 和 `apply_changes` 里各写一份。
- **字段库改动只改 `sensitive_fields.json`**：不要把正则硬编码进 Python。
- **注释解释「为什么」，不解释「是什么」**。

## 扩充敏感字段库

`sensitive_fields.json` 是纯数据文件，结构如下：

```json
{
  "id": "字段唯一标识",
  "label": "界面显示名",
  "pattern": "正则（带标签形式，优先匹配标签型规则）",
  "mask": "替换模板，\\1 表示第 1 个捕获组",
  "apply_to_filename": true,
  "default_enabled": false
}
```

要点：

- **空白用 `[ \t]*` 而不是 `\s*`**：`\s*` 会跨行吞掉换行符，把两行内容并成一行。
- **长前缀放前面**：正则交替分支按顺序匹配，`注册资本金|注册资本` 的顺序不能反。
- **掩码模板引用捕获组（`\\1`）= 标签型规则**，会先于纯值型规则执行。
- **纯值型规则要加边界断言**，如 `(?<![0-9])`，避免匹配到长数字串的一部分。

新增规则后请在 `test_masker.py` 的样例文档里加一行验证。

## 提交信息

用中文或英文均可，格式参考：

```
feat: 新增 XXX 敏感字段规则

fix: 修复 YYY 问题

docs: 补充 ZZZ 文档
```

## 打包

```bash
build.bat
```

产物为 `dist/文件脱敏工具.exe`（PyInstaller 单文件）。
**不要提交 `dist/` 和 `build/`**——已在 `.gitignore` 中排除。

## 提交 PR 前自查

- [ ] 三个测试脚本全部通过
- [ ] 没有引入新的 pip 依赖（除非先在 issue 里讨论）
- [ ] 没有把真实敏感数据、词表、内网地址写进代码
- [ ] `dist/`、`build/`、`__pycache__/` 未被加入暂存区
