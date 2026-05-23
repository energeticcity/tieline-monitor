@echo off
title Tieline Monitor — Uninstall

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Please right-click and "Run as administrator"
    pause
    exit /b 1
)

set "SERVICE_EXE=%~dp0TielineMonitor.exe"

echo Stopping and removing Tieline Monitor service...
"%SERVICE_EXE%" stop
"%SERVICE_EXE%" uninstall

echo.
echo Done. The service has been removed.
echo Your settings.json and log files remain in this folder.
echo You can delete this folder manually to fully remove the program.
echo.
pause
