"""desensitize-service 单元测试包。

设计约束:
- 仅使用标准库 unittest（不依赖 pytest），保证 `python -m unittest discover` 可直接运行；
- 同时兼容 pytest 收集（unittest 风格的 TestCase 被 pytest 原生支持）。
- 所有涉及文件系统的用例一律使用 tempfile.TemporaryDirectory，不污染项目目录。
"""
import sys
from pathlib import Path

# 让测试可以 import 到项目根目录的 masker / app 模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
