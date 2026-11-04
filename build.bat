@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo   文件脱敏工具 - 离线打包脚本
echo   产物: dist\文件脱敏工具.exe  (单文件, 目标机无需装 Python)
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python, 请先安装 Python 3.8+ 并加入 PATH
    pause
    exit /b 1
)

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [1/3] 安装 PyInstaller ...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo [错误] PyInstaller 安装失败
        pause
        exit /b 1
    )
) else (
    echo [1/3] PyInstaller 已就绪
)

echo [2/3] 清理旧构建产物 ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "文件脱敏工具.spec" del /q "文件脱敏工具.spec"

echo [3/3] 打包中, 首次运行需解压, 约需 1-2 分钟 ...
python -m PyInstaller --noconfirm --clean --onefile --noupx ^
    --name "文件脱敏工具" ^
    --add-data "templates;templates" ^
    --add-data "sensitive_fields.json;." ^
    --hidden-import=flask ^
    --hidden-import=jinja2 ^
    app.py

if errorlevel 1 (
    echo.
    echo [错误] 打包失败
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   打包完成: dist\文件脱敏工具.exe
echo.
echo   部署方式: 把 dist\文件脱敏工具.exe 单独拷贝到目标机器,
echo             双击即可运行。完全断网可用, 不需要 Python,
echo             不联网, 不调用任何大模型 API。
echo ============================================================
pause
