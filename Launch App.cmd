@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0run_app.py"
) else (
    echo Live/Dead Cell Counter needs its local Python environment.
    echo For the ready-to-use app, extract the portable ZIP and open Live-Dead Cell Counter.exe.
    echo For source setup, follow APP_GUIDE.md in this folder.
    pause
    exit /b 1
)
if errorlevel 1 pause
