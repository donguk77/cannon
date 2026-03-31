@echo off
title Canon AI Vision v5.0
color 0B
echo ==========================================
echo  Canon AI Vision v5.0 - Starting...
echo ==========================================
echo.
cd /d "%~dp0"
if not exist "canon_env\Scripts\python.exe" (
    echo [ERROR] canon_env not found. Please check setup guide.
    pause
    exit /b 1
)
set QT_QPA_PLATFORM_PLUGIN_PATH=%~dp0canon_env\Lib\site-packages\PyQt5\Qt5\plugins\platforms
set PYTHONPATH=%~dp0
canon_env\Scripts\python.exe gui\main_window.py
if %errorlevel% neq 0 (
    echo [ERROR] Launch failed. Check error log.
    pause
)
echo.
echo Terminated.
pause
