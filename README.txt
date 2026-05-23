Tieline Monitor
===============
Monitors Tieline Bridge-IT II (transmitter) and Merlin Plus (studio) via SNMP.
Sends SMS via Twilio when the audio source changes or a device goes offline.
Includes a web UI for managing settings and phone numbers.


INSTALL (Windows)
-----------------
1. Copy this entire folder to the Windows PC (e.g. C:\tieline-monitor\)
2. Right-click install.bat → Run as administrator
3. Wait for it to finish — it downloads Python and sets up the service automatically
4. Open a browser and go to http://192.168.3.30:8181
5. Log in with password: admin
6. Go to Settings and enter your Twilio credentials and alert numbers
7. Click "Send Test SMS" to confirm it's working


MOVING TO A NEW PC
------------------
1. Stop the service on the old PC:   right-click uninstall.bat → Run as administrator
2. Copy the entire folder to the new PC
3. Right-click install.bat → Run as administrator on the new PC
   Your settings.json is preserved — Twilio credentials and phone numbers carry over


MANAGING THE SERVICE
--------------------
From an admin command prompt in the install folder:
  Start:   TielineMonitor.exe start
  Stop:    TielineMonitor.exe stop
  Restart: TielineMonitor.exe restart
  Status:  TielineMonitor.exe status

Or use Windows Services (services.msc) — look for "Tieline Monitor"


WEB UI
------
URL:      http://192.168.3.30:8181  (or the IP of whatever PC it's running on)
Password: set in Settings page (default: admin)

Dashboard — live audio source status, device reachability, recent events
Settings  — Twilio credentials, alert numbers, poll interval, device IPs


LOG FILES
---------
monitor.log      — info and warnings from the monitor
monitor_err.log  — Python errors (check this if the service won't start)


AUDIO STATES
------------
Normal   — Live STL link from studio is active (green)
Backup   — Backup computer audio input is active (yellow)
SD Card  — SD card file playback is active (yellow)
Loss     — No active audio source or transmitter unreachable (red)

SMS is sent on EVERY state change in both directions, including return to Normal.
Example messages:
  "Moose FM ALERT — Audio source changed — From: Normal, To: Backup"
  "Moose FM RESOLVED — STL link restored — Now: Normal, Was: Backup"
  "Moose FM ALERT — Transmitter codec (192.168.3.20) is UNREACHABLE"
  "Moose FM RESOLVED — Transmitter codec (192.168.3.20) is back online"


TWILIO SETUP (if you don't have an account)
--------------------------------------------
1. Sign up at https://www.twilio.com
2. Buy a Canadian phone number (~$1.50/month)
3. From the console, copy your Account SID and Auth Token
4. Enter them in Settings along with the From Number
5. Each SMS costs approximately $0.01 CAD


SUPPORT
-------
Moose Media — areaburn@moosefm.ca
