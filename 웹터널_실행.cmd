@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

REM ============================================================
REM  연속지적 위치도 — Cloudflare 터널로 웹 공개 (한국 PC에서 실행)
REM  로컬 브릿지를 그대로 공개 HTTPS URL로 노출 → VWorld 호출이
REM  한국 IP에서 나가므로 정상 동작합니다.
REM ============================================================

REM ===== VWorld 인증 도메인 (이 키에 '등록된' 도메인이어야 함) =====
REM  WFS(지적/용도지역)는 이 값이 키 등록 도메인과 일치해야 동작합니다.
REM  기본값은 이미 등록·검증된 도메인입니다. 필요하면 본인이 등록한 값으로 변경하세요.
set "VWORLD_DOMAIN=urbananalytics-qqbh.onrender.com"

REM 기존 8788 포트 정리
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8788 .*LISTENING"') do taskkill /PID %%P /F >nul 2>nul

set "PY=C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
if not exist "%PY%" set "PY=C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/2] 로컬 브릿지 시작 (VWORLD_DOMAIN=%VWORLD_DOMAIN%)
start "Cadastre Bridge" "%PY%" "%~dp0cadastre_bridge_server.py" 8788
timeout /t 2 /nobreak >nul

where cloudflared >nul 2>nul
if errorlevel 1 (
  echo.
  echo [오류] cloudflared 가 없습니다. 아래 중 하나로 설치 후 다시 실행하세요.
  echo   - winget install --id Cloudflare.cloudflared
  echo   - https://github.com/cloudflare/cloudflared/releases 에서
  echo     cloudflared-windows-amd64.exe 다운로드 후 PATH에 두기
  echo.
  pause
  exit /b 1
)

echo [2/2] Cloudflare 터널 시작 — 아래 출력되는 https://xxxx.trycloudflare.com 주소로 접속하세요.
echo        (이 창을 닫으면 터널이 종료됩니다)
echo.
cloudflared tunnel --url http://127.0.0.1:8788
