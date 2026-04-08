@echo off
chcp 65001 > nul
title Canon Monitor - Phone Server

echo.
echo ========================================
echo   Canon Monitor Phone Server
echo ========================================
echo.

:: 프로젝트 루트 (이 파일 위치에서 2단계 위)
cd /d "%~dp0..\.."

:: 가상환경 활성화 (있으면)
if exist "canon_env\Scripts\activate.bat" (
    call canon_env\Scripts\activate.bat
)

:: 서버 시작
echo [1] FastAPI 서버 시작 중 (포트 8765)...
start "Canon API Server" cmd /k "cd /d %~dp0..\.. && uvicorn connect_phone.server.app:app --host 0.0.0.0 --port 8765"

timeout /t 3 /nobreak > nul

:: Cloudflare Tunnel 시작
echo.
echo [2] Cloudflare Tunnel 시작 중...
echo     (cloudflared.exe 가 없으면 아래 URL에서 다운로드)
echo     https://github.com/cloudflare/cloudflared/releases/latest
echo.

where cloudflared >nul 2>&1
if %errorlevel% == 0 (
    echo cloudflared 감지됨. 터널 시작...
    echo.
    echo ★ 아래 출력되는 trycloudflare.com URL 을 앱 설정에 입력하세요 ★
    echo.
    cloudflared tunnel --url http://localhost:8765
) else (
    echo [경고] cloudflared.exe 를 찾을 수 없습니다.
    echo        같은 WiFi 환경이면 로컬 IP로도 연결 가능합니다:
    ipconfig | findstr "IPv4"
    echo.
    pause
)
