$ErrorActionPreference = 'Stop'
$root = 'C:\tieline-monitor'
$package = $PSScriptRoot
$python = Join-Path $root 'python\python.exe'
$service = Get-CimInstance Win32_Service -Filter "Name='TielineMonitor'"
if (!$service -or $service.PathName.Trim('"') -ne (Join-Path $root 'TielineMonitor.exe')) { throw 'Unexpected service target' }
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Open PowerShell as administrator; installation has not started' }
$expectedOld = '9CC61A77D66F9262070F8FBAC62928B2600A1B340304DB5780BD52D045A4E9E7'
if ((Get-FileHash (Join-Path $root 'monitor.py') -Algorithm SHA256).Hash -ne $expectedOld) { throw 'Installed source changed; preserve it and review before installation' }
$hashes = @{
 'monitor.py' = '307599A52A89E0548BB34703F9EF8CA8EE5D39ACBFA0852E61243DE3BE035176'
 'reachability_alerts.py' = '4EC7E797776AA03F77C41F5E4489C7BC27E0A9F747EFEE4652F99DE254B466C4'
 'test_monitor_alerts.py' = '546341EF0A0387B37F64743A18A09DDB4BB20F11367029F1A854346A9A6E2A57'
 'test_reachability_alerts.py' = '34F1B29C3D0190C9041CD636EE0E3F7DC08DF26E76F2D6B9D8CB5E51EF28177E'
}
foreach ($name in $hashes.Keys) {
 if ((Get-FileHash (Join-Path $package $name) -Algorithm SHA256).Hash -ne $hashes[$name]) { throw "Package hash mismatch: $name" }
}
$configPath = Join-Path $root 'settings.json'
$configHash = (Get-FileHash $configPath -Algorithm SHA256).Hash
$config = Get-Content $configPath -Raw | ConvertFrom-Json
if ($config.studio_ip -ne '192.168.1.150' -or $config.transmitter_ip -ne '192.168.3.20' -or $config.web_port -ne 8181) { throw 'Unexpected live monitor configuration' }
if (@($config.alert_numbers).Count -ne 0) { throw 'Keep SMS paused during installation' }
if (Test-Path (Join-Path $root 'reachability_alerts.py')) { throw 'Existing helper requires review before replacement' }
Push-Location $package
try {
 & $python -m unittest discover -v *> (Join-Path $package 'windows-tests.log')
 if ($LASTEXITCODE -ne 0) { throw 'Native Windows regression tests failed; no files installed' }
} finally { Pop-Location }
$backup = Join-Path $root ('alert-fix-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup | Out-Null
Copy-Item (Join-Path $root 'monitor.py') (Join-Path $backup 'monitor.py')
$changed = $false
try {
 Stop-Service TielineMonitor
 (Get-Service TielineMonitor).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
 $changed = $true
 Copy-Item (Join-Path $package 'reachability_alerts.py') (Join-Path $root 'reachability_alerts.py')
 Copy-Item (Join-Path $package 'monitor.py') (Join-Path $root 'monitor.py') -Force
 Push-Location $root
 try {
  & $python -c "import sys; sys.path.insert(0, r'C:\tieline-monitor'); import monitor; from reachability_alerts import ReachabilityAlerts; print('Native imports OK')"
  if ($LASTEXITCODE -ne 0) { throw 'Installed imports failed' }
 } finally { Pop-Location }
 Start-Service TielineMonitor
 (Get-Service TielineMonitor).WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
 $accepted = $false
 for ($i=0; $i -lt 24; $i++) {
  Start-Sleep -Seconds 5
  try {
   $null = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8181/login' -Method POST -Body @{password=$config.web_password} -SessionVariable monitorSession -TimeoutSec 5
   $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8181/api/status' -WebSession $monitorSession -TimeoutSec 5
   $status = $response.Content | ConvertFrom-Json
   if ($status.reachability_alert_policy.version -eq 1 -and $status.last_poll_time -and $status.tx_reachable -eq $true -and $status.studio_reachable -eq $true -and $status.audio_state -eq 'normal' -and !$status.reachability_alert_error) { $accepted = $true; break }
  } catch { }
 }
 if (!$accepted) { throw 'No fresh healthy sample from updated monitor' }
 if ((Get-FileHash $configPath -Algorithm SHA256).Hash -ne $configHash) { throw 'Configuration changed during installation' }
 foreach ($name in @('monitor.py','reachability_alerts.py')) {
  if ((Get-FileHash (Join-Path $root $name) -Algorithm SHA256).Hash -ne $hashes[$name]) { throw 'Installed file hash mismatch' }
 }
 [ordered]@{state='installed-and-healthy';at=(Get-Date).ToUniversalTime().ToString('o');service='TielineMonitor';backup=$backup;oldSource=$expectedOld;newSource=$hashes['monitor.py'];helper=$hashes['reachability_alerts.py'];windowsTests=25;configurationUnchanged=$true;smsRecipients=0;lastPoll=$status.last_poll_time;audio=$status.audio_state;transmitterReachable=$status.tx_reachable;studioReachable=$status.studio_reachable;policy=$status.reachability_alert_policy} | ConvertTo-Json -Depth 5 | Tee-Object -FilePath (Join-Path $package 'installation-receipt.json')
} catch {
 if ($changed) {
  Stop-Service TielineMonitor -ErrorAction SilentlyContinue
  Copy-Item (Join-Path $backup 'monitor.py') (Join-Path $root 'monitor.py') -Force
  Start-Service TielineMonitor
 }
 [ordered]@{state='installation-failed';rolledBack=$changed;errorType=$_.Exception.GetType().Name;smsRemainsPaused=$true;backup=$backup} | ConvertTo-Json | Set-Content (Join-Path $package 'installation-receipt.json')
 throw
}
