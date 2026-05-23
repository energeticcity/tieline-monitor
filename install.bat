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
set "NSSM=%INSTALL_DIR%nssm.exe"
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

:: ── Step 3: Download NSSM if not present ──────────────────────────────────────
if exist "%NSSM%" (
    echo  [OK] NSSM already present
) else (
    echo  [3/4] Downloading NSSM service manager...
    powershell -Command "Invoke-WebRequest -Uri 'https://nssm.cc/release/nssm-2.24.zip' -OutFile '%INSTALL_DIR%nssm.zip' -UseBasicParsing"
    if not exist "%INSTALL_DIR%nssm.zip" (
        echo  ERROR: Failed to download NSSM. Check your internet connection.
        pause
        exit /b 1
    )
    powershell -Command "Add-Type -Assembly 'System.IO.Compression.FileSystem'; $z = [IO.Compression.ZipFile]::OpenRead('%INSTALL_DIR%nssm.zip'); $entry = $z.Entries | Where-Object { $_.FullName -like '*/win64/nssm.exe' } | Select-Object -First 1; [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, '%NSSM%', $true); $z.Dispose()"
    del "%INSTALL_DIR%nssm.zip"
    echo  [OK] NSSM ready
)

:: ── Step 4: Create settings.json if missing ───────────────────────────────────
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
        echo   "web_port": 8080,
        echo   "web_password": "admin"
        echo }
    ) > "%INSTALL_DIR%settings.json"
)

:: ── Step 5: Install and start the Windows service ─────────────────────────────
echo  [4/4] Installing Windows service...

:: Remove existing service if present
"%NSSM%" stop %SERVICE_NAME% >nul 2>&1
"%NSSM%" remove %SERVICE_NAME% confirm >nul 2>&1

"%NSSM%" install %SERVICE_NAME% "%PYTHON_EXE%" "%INSTALL_DIR%monitor.py"
"%NSSM%" set %SERVICE_NAME% AppDirectory "%INSTALL_DIR%"
"%NSSM%" set %SERVICE_NAME% DisplayName "Tieline Monitor"
"%NSSM%" set %SERVICE_NAME% Description "Polls Tieline codecs via SNMP and sends SMS alerts on audio source changes."
"%NSSM%" set %SERVICE_NAME% Start SERVICE_AUTO_START
"%NSSM%" set %SERVICE_NAME% AppStdout "%INSTALL_DIR%monitor.log"
"%NSSM%" set %SERVICE_NAME% AppStderr "%INSTALL_DIR%monitor_err.log"
"%NSSM%" set %SERVICE_NAME% AppRestartDelay 5000

"%NSSM%" start %SERVICE_NAME%
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
