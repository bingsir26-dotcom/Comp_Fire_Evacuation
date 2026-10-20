@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Fire Escape Web Server - Auto Port

echo ======================================
echo    Fire Escape Web Server
echo ======================================
echo.

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo [ERROR] Python 3 was not found.
    echo Please run setup_new_computer.bat first, or install Python 3.11 or newer.
    echo.
    pause
    exit /b 1
)

echo Installing dependencies...
%PYTHON_CMD% -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo.
    echo [ERROR] Unable to install required Python packages.
    pause
    exit /b 1
)

echo.
echo Starting server...
echo Local: http://localhost:8080
echo Phone on same Wi-Fi: use the QR code shown on the webpage.
echo.
echo Press Ctrl+C to stop the server.
echo ======================================
echo.
%PYTHON_CMD% server.py
set "SERVER_EXIT=%ERRORLEVEL%"
echo.
echo Server stopped (exit code %SERVER_EXIT%).
pause
exit /b %SERVER_EXIT%