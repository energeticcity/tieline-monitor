@echo off
title Tieline Monitor — Uninstall

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Please right-click and "Run as administrator"
    pause
    exit /b 1
)

set "NSSM=%~dp0nssm.exe"
set "SERVICE_NAME=TielineMonitor"

echo Stopping and removing Tieline Monitor service...
"%NSSM%" stop %SERVICE_NAME%
"%NSSM%" remove %SERVICE_NAME% confirm

echo.
echo Done. The service has been removed.
echo Your settings.json and log files remain in this folder.
echo You can delete this folder manually to fully remove the program.
echo.
pause
