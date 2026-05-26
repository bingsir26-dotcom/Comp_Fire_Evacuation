@echo off
chcp 65001 >nul
title Fire Escape Web Server - Port 8080
echo ======================================
echo    Fire Escape Web Server
echo ======================================
echo.
echo   Installing dependencies...
pip install -r requirements.txt --quiet
echo.
echo   Starting server...
echo   Phone: http://[YOUR-PC-IP]:8080
echo   Local: http://localhost:8080
echo.
echo   Press Ctrl+C to stop
echo ======================================
echo.
cd /d "%~dp0"
python server.py
pause