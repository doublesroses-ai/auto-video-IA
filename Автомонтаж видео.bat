@echo off
cd /d "%~dp0"
rem Портативный режим (.venv) или установленная версия (вшитый python)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" app.pyw
) else (
    start "" "python\pythonw.exe" app.pyw
)
