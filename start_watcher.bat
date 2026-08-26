@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" watcher.py
) else (
    "python\python.exe" watcher.py
)
pause
