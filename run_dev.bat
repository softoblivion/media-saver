@echo off
rem Run the app from source with dev tools: run_dev.bat [--fake] [--ui-dir PATH]
rem Without .venv, run build\build.ps1 -SkipBuild once (or see docs\BUILD.md).
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%.venv\Scripts\python.exe" (
    echo .venv not found. Create it: py -3.14 -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements.txt
    exit /b 1
)
cd /d "%ROOT%"
"%ROOT%.venv\Scripts\python.exe" -m app.main --debug %*
