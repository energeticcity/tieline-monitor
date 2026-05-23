# Tieline Monitor

Monitors a Tieline Bridge-IT II (transmitter) and Tieline Merlin Plus (studio) via SNMP. Sends SMS alerts via Twilio when the audio source changes or a device goes offline. Includes a password-protected web UI for managing settings and phone numbers.

Built for **Moose Media** — Fort St. John, BC.

---

## What It Does

The Bridge-IT II at the transmitter has three audio sources in priority order:

| Source | Meaning | SMS Label |
|---|---|---|
| `main::connection.0` | Live STL link from studio | Normal |
| `main::input.1` | Backup computer audio input | Backup |
| `main::file.2` | SD card file playback | SD Card |
| (none active or device offline) | No audio source | Loss |

The monitor polls both Tieline codecs every 30 seconds (configurable). When the active audio source changes — in either direction — it sends an SMS to all configured numbers immediately.

### Example SMS Messages

```
ALERT - Moose FM
STL link lost, switched to BACKUP audio
2:34 PM Sat May 23
```

```
RESOLVED - Moose FM
STL link restored, back to normal
2:41 PM Sat May 23
```

```
ALERT - Moose FM
STL link lost, switched to SD CARD
2:34 PM Sat May 23
```

```
ALERT - Moose FM
No audio source active - transmitter may be silent
2:34 PM Sat May 23
```

```
ALERT - Moose FM
Transmitter codec not responding
Check: 192.168.3.20
2:34 PM Sat May 23
```

```
RESOLVED - Moose FM
Studio codec back online
2:41 PM Sat May 23
```

Every state change triggers an SMS — including return to normal. The audio failover on the Bridge-IT II is automatic and immediate; SMS is notification only.

---

## Architecture

```
[Tieline Bridge-IT II]  192.168.3.20  ←─ SNMP poll every 30s ─┐
[Tieline Merlin Plus ]  192.168.1.150 ←─ SNMP poll every 30s ─┤
                                                                 │
                                              [Windows PC]  192.168.3.30
                                              monitor.py running as
                                              Windows Service (NSSM)
                                                 │
                                                 ├── Web UI  :8181
                                                 └── SMS via Twilio
```

- **SNMP community:** `public` (read-only, no device configuration required)
- **Polling:** every 30 seconds (configurable 10–300s)
- **SMS:** Twilio — approximately $0.01 CAD per message
- **Web UI:** Flask + Waitress on port 8181, password protected

---

## Device Details

| Device | IP | Model | Firmware |
|---|---|---|---|
| Transmitter | 192.168.3.20 | Tieline Bridge-IT II | v3.12.38 |
| Studio | 192.168.1.150 | Tieline Merlin Plus | v2.22.64 |

### Key SNMP OIDs (Bridge-IT II)

| OID | Description | Values |
|---|---|---|
| `1.3.6.1.4.1.37196.2.7.20.2.1.5.0` | Source 0 state (STL link) | 1=active, 2=standby, 0=off |
| `1.3.6.1.4.1.37196.2.7.20.2.1.5.1` | Source 1 state (backup input) | 1=active, 2=standby, 0=off |
| `1.3.6.1.4.1.37196.2.7.20.2.1.5.2` | Source 2 state (SD card) | 1=active, 2=standby, 0=off |
| `1.3.6.1.4.1.37196.2.3.2.1.7.0` | Connection 0 state | 3=connected |
| `1.3.6.1.4.1.37196.2.7.10.0` | Alarm count | Gauge32 |

---

## Installation (Windows)

### Requirements
- Windows 10 or later (64-bit)
- Internet access during install (downloads Python and NSSM automatically)
- Static IP on the monitoring PC (192.168.3.30)
- Network access to both 192.168.3.x and 192.168.1.x subnets

### Steps

1. Copy the project folder to the Windows PC (e.g. `C:\tieline-monitor\`)
2. Right-click `install.bat` → **Run as administrator**
3. Wait for it to finish — it downloads Python 3.12 embeddable, installs all packages, and registers a Windows service automatically
4. Open a browser on any machine and go to `http://192.168.3.30:8181`
5. Log in with password `admin`
6. Go to **Settings** and enter your Twilio credentials and alert phone numbers
7. Click **Send Test SMS** to confirm everything is working

The service is named `TielineMonitor` in Windows Services and starts automatically at boot.

### What `install.bat` Does

1. Downloads Python 3.12 embeddable (no system Python required)
2. Installs pip into the embedded Python
3. Installs all Python packages (`pysnmp`, `twilio`, `flask`, `waitress`)
4. Downloads WinSW (Windows Service Wrapper) from GitHub
5. Registers `TielineMonitor` as an auto-start Windows service
6. Starts the service

---

## Moving to a New PC

1. On the old PC: right-click `uninstall.bat` → Run as administrator
2. Copy the entire folder to the new PC (your `settings.json` carries over)
3. On the new PC: right-click `install.bat` → Run as administrator

---

## Web UI

Accessible at `http://192.168.3.30:8181` from any browser on the network.

| Page | Description |
|---|---|
| Dashboard | Live audio source status, device reachability, recent events |
| Settings | Twilio credentials, alert numbers, poll interval, device IPs, password |
| Test SMS | Sends a test message to verify Twilio is configured correctly |

The dashboard auto-refreshes every 20 seconds without a full page reload.

---

## Managing the Service

From an administrator command prompt in the install folder:

```
TielineMonitor.exe start
TielineMonitor.exe stop
TielineMonitor.exe restart
TielineMonitor.exe status
```

Or use **Windows Services** (`services.msc`) and look for "Tieline Monitor".

Log files are written to the install folder:
- `monitor.log` — normal operation and state changes
- `monitor_err.log` — Python errors (check this if the service won't start)

---

## Twilio Setup

1. Sign up at [twilio.com](https://twilio.com)
2. Buy a Canadian phone number (~$1.50/month)
3. Copy your **Account SID** and **Auth Token** from the Twilio console
4. Enter them in the web UI Settings page along with the From number
5. Add alert numbers (one per line, with country code e.g. `+17805551234`)

Each SMS costs approximately $0.01 CAD.

---

## Configuration (`settings.json`)

Created automatically on first run. Edit via the web UI — do not edit the file directly while the service is running.

| Key | Default | Description |
|---|---|---|
| `transmitter_ip` | `192.168.3.20` | Bridge-IT II IP address |
| `studio_ip` | `192.168.1.150` | Merlin Plus IP address |
| `snmp_community` | `public` | SNMP community string |
| `poll_interval` | `30` | Seconds between polls (10–300) |
| `station_name` | `Moose FM` | Appears in all SMS messages |
| `twilio_account_sid` | — | From Twilio console |
| `twilio_auth_token` | — | From Twilio console |
| `twilio_from_number` | — | Your Twilio phone number |
| `alert_numbers` | `[]` | List of numbers to alert |
| `web_port` | `8181` | Web UI port |
| `web_password` | `admin` | Web UI login password |

---

## Development

```bash
# Clone and set up
git clone https://github.com/energeticcity/tieline-monitor
cd tieline-monitor
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# Run locally
venv/bin/python3 monitor.py
# Web UI at http://localhost:8181  (password: admin)
```

Requires `snmpget` (from `net-snmp`) on macOS/Linux for the subprocess fallback.

---

## Support

Moose Media — [areaburn@moosefm.ca](mailto:areaburn@moosefm.ca)
