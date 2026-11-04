@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist "dist\文件脱敏工具.exe" (
    echo 启动已打包的 EXE ...
    start "" "dist\文件脱敏工具.exe"
) else (
    echo 未找到打包产物, 从源码启动 ...
    python app.py
)
