@echo off
setlocal
cd /d "%~dp0"

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8788 .*LISTENING"') do (
    taskkill /PID %%P /F >nul 2>nul
)

set "PY=C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
if not exist "%PY%" set "PY=C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"

REM VWorld WFS는 호출 도메인을 검증 → 이 키에 등록된 도메인을 전달(지번 선택/분석에 필요)
set "VWORLD_DOMAIN=urbananalytics-qqbh.onrender.com"

start "Cadastre Map Bridge" "%PY%" "%~dp0cadastre_bridge_server.py" 8788
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8788/"
