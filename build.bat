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
REM 打包出来的 EXE 内置 Flask，不依赖目标机环境。
REM 若目标机禁止运行 EXE，可改用源码部署：python server_stdlib.py（零依赖）。
python -m PyInstaller --noconfirm --clean --onefile --noupx ^
    --name "文件脱敏工具" ^
    --add-data "templates;templates" ^
    --add-data "sensitive_fields.json;." ^
    --add-data "entity_aliases.json;." ^
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
echo   部署方式一（推荐）: 把 dist\文件脱敏工具.exe 拷到目标机，双击运行。
echo   部署方式二（EXE 被禁用的环境）: 拷贝整个项目目录到目标机，
echo       运行 python server_stdlib.py —— 纯标准库，零 pip 依赖。
echo ============================================================
pause
