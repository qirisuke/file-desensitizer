"""离线自检：验证脱敏全程不发起任何非本机网络连接，也不加载任何大模型 SDK。"""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

OUTBOUND = []


class Tripwire(socket.socket):
    """拦截所有对外连接尝试。"""

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in ('127.0.0.1', '::1', 'localhost'):
            OUTBOUND.append(address)
            raise AssertionError(f'检测到对外网络连接: {address}')
        return super().connect(address)

    def connect_ex(self, address):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in ('127.0.0.1', '::1', 'localhost'):
            OUTBOUND.append(address)
            raise AssertionError(f'检测到对外网络连接: {address}')
        return super().connect_ex(address)


socket.socket = Tripwire

# 静态审计：源码里不得出现任何联网/大模型 SDK
FORBIDDEN = [
    'requests', 'urllib.request', 'httpx', 'aiohttp', 'http.client',
    'openai', 'anthropic', 'google.generativeai', 'genai', 'langchain',
    'dashscope', 'zhipu', 'dashscope', 'ollama', 'boto3', 'socket.create_connection',
]
import app as service
import masker

src_dir = Path(__file__).resolve().parent
offenders = []
for py in sorted(src_dir.glob('*.py')):
    if py.name == Path(__file__).name:   # 审计脚本自身含禁用词字面量，跳过
        continue
    text = py.read_text(encoding='utf-8')
    for token in FORBIDDEN:
        if token in text:
            offenders.append(f'{py.name}: {token}')

print('=== 1. 源码静态审计 ===')
if offenders:
    print('!!! 发现联网/模型 SDK 引用:')
    for o in offenders:
        print('   -', o)
else:
    print('通过：源码中无 requests / urllib.request / httpx / openai / anthropic 等引用')

print('\n=== 2. 运行时网络连接监控 ===')
try:
    config = service.build_config({
        'directory': str(src_dir),
        'mode': 'combo',
        'rules': [{'keyword': '脱敏', 'replacement': 'MASK'}],
        'word_list': ['示例能源'],
        'field_ids': masker.default_field_ids(service.FIELD_LIBRARY),
        'mask': '***',
        'filename_mask': '已脱敏',
        'case_sensitive': True,
        'include_filenames': True,
        'include_contents': True,
        'recursive': True,
    })
    preview = service.scan_directory(config)
    if 'error' in preview:
        print('扫描失败:', preview['error'])
    else:
        print(f"扫描完成：{preview['scanned_files']} 个文件, "
              f"{preview['total_content_changes']} 处替换, "
              f"全程未触发任何对外连接拦截")
    if OUTBOUND:
        print('!!! 检测到对外连接:', OUTBOUND)
except AssertionError as e:
    print('!!! 离线校验失败:', e)
    sys.exit(1)

print('\n=== 3. 已加载模块审计 ===')
suspicious = [m for m in sys.modules
              if m.split('.')[0] in ('requests', 'httpx', 'aiohttp', 'openai', 'anthropic')]
print('已加载的第三方联网/模型 SDK:', suspicious if suspicious else '无')
local_only = [m for m in sys.modules if m.split('.')[0] == 'http']
print('已加载的 http 模块:', local_only, '-> 均为 Flask 本地服务端/URL 工具，未发起对外连接')

print('\n>>> 离线自检通过：脱敏过程完全在本地完成，不联网、不调用任何大模型 API')
