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

echo [INFO] Checking required packages...
canon_env\Scripts\python.exe -c "import dotenv" 2>nul
if %errorlevel% neq 0 (
    echo [INFO] Installing python-dotenv...
    canon_env\Scripts\pip.exe install python-dotenv --quiet
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install python-dotenv.
        pause
        exit /b 1
    )
    echo [INFO] python-dotenv installed.
)

set QT_QPA_PLATFORM_PLUGIN_PATH=%~dp0canon_env\Lib\site-packages\PyQt5\Qt5\plugins\platforms
set PYTHONPATH=%~dp0
set PATH=%~dp0canon_env\Lib\site-packages\torch\lib;%PATH%

echo [INFO] Launching application...
echo.
canon_env\Scripts\python.exe gui\main_window.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Launch failed. Check error log above.
    pause
)
echo.
echo Terminated.
pause
