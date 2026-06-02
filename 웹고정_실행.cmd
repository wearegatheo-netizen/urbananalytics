@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

REM ============================================================
REM  연속지적 위치도 — Tailscale Funnel 고정 URL (한국 PC에서 실행)
REM  고정 주소: https://<기기이름>.<tailnet>.ts.net
REM ============================================================

REM ===== VWorld 인증 도메인 (키에 '등록된' 도메인) =====
REM  기본값은 이미 등록·검증된 도메인이라 서버측 WFS가 바로 동작합니다.
REM  [지적도 오버레이/위성영상까지] 원하면: VWorld 키 서비스URL에 본인
REM  ts.net 주소(예: pc-name.tailxxxx.ts.net)를 등록하고, 아래 값을 그 주소로 변경하세요.
set "VWORLD_DOMAIN=urbananalytics-qqbh.onrender.com"

where tailscale >nul 2>nul
if errorlevel 1 (
  echo [오류] Tailscale 이 설치/로그인되어 있지 않습니다.
  echo   1) https://tailscale.com/download/windows 에서 설치(관리자)
  echo   2) 트레이의 Tailscale 로 로그인
  echo   3) 관리자콘솔에서 HTTPS + Funnel 활성화
  echo.
  pause
  exit /b 1
)

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8788 .*LISTENING"') do taskkill /PID %%P /F >nul 2>nul

set "PY=C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
if not exist "%PY%" set "PY=C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/2] 로컬 브릿지 시작 (VWORLD_DOMAIN=%VWORLD_DOMAIN%)
start "Cadastre Bridge" "%PY%" "%~dp0cadastre_bridge_server.py" 8788
timeout /t 2 /nobreak >nul

echo [2/2] Tailscale Funnel 시작 — 고정 주소(https://...ts.net)로 공개합니다.
echo        (이 창을 닫으면 공개가 중단됩니다. 주소는 'tailscale funnel status' 로도 확인)
echo.
tailscale funnel 8788
