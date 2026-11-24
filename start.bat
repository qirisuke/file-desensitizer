@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   文件脱敏工具 - 启动
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python。
    echo 请安装 Python 3.8+ 并加入 PATH，或直接使用 dist\文件脱敏工具.exe
    pause
    exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [错误] Python 版本过低，需要 3.8 及以上
    python --version
    pause
    exit /b 1
)

REM 优先用已打包的 EXE
if exist "dist\文件脱敏工具.exe" (
    echo 使用已打包的 EXE 启动 ...
    start "" "dist\文件脱敏工具.exe"
    exit /b 0
)

REM 源码启动：Flask 有就用 Flask，没有就自动回退到标准库服务器
python app.py
if errorlevel 1 (
    echo.
    echo [提示] 上面启动失败。尝试零依赖的标准库服务器 ...
    python server_stdlib.py
)
pause
