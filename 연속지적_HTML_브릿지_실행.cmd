@echo off
setlocal
cd /d "%~dp0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8788 .*LISTENING"') do (
    taskkill /PID %%P /F >nul 2>nul
)
set "PY=C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"
start "연속지적 HTML 브릿지" "%PY%" "%~dp0cadastre_bridge_server.py" 8788
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8788/"
