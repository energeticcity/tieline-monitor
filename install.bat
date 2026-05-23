@echo off
setlocal EnableDelayedExpansion
title Tieline Monitor — Install

echo.
echo  Tieline Monitor — Windows Setup
echo  ================================
echo.

:: Must run as admin for service install
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo  ERROR: Please right-click install.bat and choose "Run as administrator"
    pause
    exit /b 1
)

set "INSTALL_DIR=%~dp0"
set "PYTHON_DIR=%INSTALL_DIR%python"
set "PYTHON_EXE=%PYTHON_DIR%\python.exe"
set "SERVICE_EXE=%INSTALL_DIR%TielineMonitor.exe"
set "SERVICE_XML=%INSTALL_DIR%TielineMonitor.xml"
set "SERVICE_NAME=TielineMonitor"

:: ── Step 1: Download Python embeddable if not present ─────────────────────────
if exist "%PYTHON_EXE%" (
    echo  [OK] Python already present at %PYTHON_DIR%
) else (
    echo  [1/4] Downloading Python 3.12 embeddable package...
    powershell -Command "& { Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.9/python-3.12.9-embed-amd64.zip' -OutFile '%INSTALL_DIR%python-embed.zip' -UseBasicParsing }"
    if not exist "%INSTALL_DIR%python-embed.zip" (
        echo  ERROR: Failed to download Python. Check your internet connection.
        pause
        exit /b 1
    )
    echo  Extracting Python...
    powershell -Command "Expand-Archive -Path '%INSTALL_DIR%python-embed.zip' -DestinationPath '%PYTHON_DIR%' -Force"
    del "%INSTALL_DIR%python-embed.zip"

    :: Enable site-packages in the embedded Python
    for %%f in ("%PYTHON_DIR%\python3*._pth") do (
        powershell -Command "(Get-Content '%%f') -replace '#import site','import site' | Set-Content '%%f'"
    )

    :: Download and install pip
    echo  Installing pip...
    powershell -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%INSTALL_DIR%get-pip.py' -UseBasicParsing"
    "%PYTHON_EXE%" "%INSTALL_DIR%get-pip.py" --quiet
    del "%INSTALL_DIR%get-pip.py"
    echo  [OK] Python ready
)

:: ── Step 2: Install Python dependencies ───────────────────────────────────────
echo  [2/4] Installing Python packages (this may take a minute)...
"%PYTHON_EXE%" -m pip install -r "%INSTALL_DIR%requirements.txt" --quiet
if %errorLevel% neq 0 (
    echo  ERROR: pip install failed. Check your internet connection.
    pause
    exit /b 1
)
echo  [OK] Packages installed

:: ── Step 3: Download WinSW service manager if not present ─────────────────────
if exist "%SERVICE_EXE%" (
    echo  [OK] Service manager already present
) else (
    echo  [3/4] Downloading WinSW service manager...
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe' -OutFile '%SERVICE_EXE%' -UseBasicParsing"
    if not exist "%SERVICE_EXE%" (
        echo  ERROR: Failed to download service manager. Check your internet connection.
        pause
        exit /b 1
    )
    echo  [OK] Service manager ready
)

:: ── Step 4: Create or update settings.json ────────────────────────────────────
if not exist "%INSTALL_DIR%settings.json" (
    echo  Creating default settings.json...
    (
        echo {
        echo   "transmitter_ip": "192.168.3.20",
        echo   "studio_ip": "192.168.1.150",
        echo   "snmp_community": "public",
        echo   "poll_interval": 30,
        echo   "station_name": "Moose FM",
        echo   "twilio_account_sid": "",
        echo   "twilio_auth_token": "",
        echo   "twilio_from_number": "",
        echo   "alert_numbers": [],
        echo   "web_port": 8181,
        echo   "web_password": "admin"
        echo }
    ) > "%INSTALL_DIR%settings.json"
) else (
    echo  Updating web_port in existing settings.json...
    powershell -Command "$s = Get-Content '%INSTALL_DIR%settings.json' | ConvertFrom-Json; $s.web_port = 8181; $s | ConvertTo-Json | Set-Content '%INSTALL_DIR%settings.json'"
)

:: ── Open firewall for web UI port ──────────────────────────────────────────────
echo  Opening firewall for port 8181...
netsh advfirewall firewall delete rule name="Tieline Monitor Web UI" >nul 2>&1
netsh advfirewall firewall add rule name="Tieline Monitor Web UI" dir=in action=allow protocol=TCP localport=8181

:: ── Step 5: Write WinSW service config ────────────────────────────────────────
(
    echo ^<service^>
    echo   ^<id^>%SERVICE_NAME%^</id^>
    echo   ^<name^>Tieline Monitor^</name^>
    echo   ^<description^>Polls Tieline codecs via SNMP and sends SMS alerts on audio source changes.^</description^>
    echo   ^<executable^>%PYTHON_EXE%^</executable^>
    echo   ^<arguments^>%INSTALL_DIR%monitor.py^</arguments^>
    echo   ^<workingdirectory^>%INSTALL_DIR%^</workingdirectory^>
    echo   ^<startmode^>Automatic^</startmode^>
    echo   ^<log mode='none'/^>
    echo ^</service^>
) > "%SERVICE_XML%"

:: ── Step 6: Install and start the Windows service ─────────────────────────────
echo  [4/4] Installing Windows service...

:: Remove existing service if present
"%SERVICE_EXE%" stop >nul 2>&1
"%SERVICE_EXE%" uninstall >nul 2>&1

"%SERVICE_EXE%" install
if %errorLevel% neq 0 (
    echo  ERROR: Service install failed. Check monitor_err.log for details.
    pause
    exit /b 1
)

"%SERVICE_EXE%" start
if %errorLevel% neq 0 (
    echo  ERROR: Service failed to start. Check monitor_err.log for details.
    pause
    exit /b 1
)

echo.
echo  ============================================================
echo   Installation complete!
echo.
echo   Service:  %SERVICE_NAME% (starts automatically at boot)
echo   Web UI:   http://192.168.3.30:8080
echo   Password: admin  (change this in Settings)
echo   Log file: %INSTALL_DIR%monitor.log
echo  ============================================================
echo.
pause
