# Sets up the CelesTrak data job on this PC (scripts\local_fetch.ps1), once:
#   - asks for the Hugging Face token (hidden) and stores it encrypted with Windows
#     DPAPI, readable only by this Windows user;
#   - registers the task "Perigee data fetch": every hour while you are signed in,
#     and as soon as possible after a missed run (PC asleep or off).
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_local_fetch.ps1
#   ...  -NewToken      replace the stored token
#   ...  -Uninstall     remove the task, the token and the job's files
param([switch]$NewToken, [switch]$Uninstall, [string]$Dataset = "")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$base = Join-Path $env:LOCALAPPDATA "Perigee"
$tokenFile = Join-Path $base "hf_token.dat"
$configFile = Join-Path $base "config.json"
$taskName = "Perigee data fetch"

if ($Uninstall) {
  Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
  if (Test-Path $base) { Remove-Item -Recurse -Force $base }
  "Removed the task and $base."
  return
}

if (-not (Test-Path (Join-Path $repo ".venv\Scripts\python.exe"))) {
  throw "No .venv in ${repo}: create it and run pip install -r requirements-dev.txt first (README)."
}
New-Item -ItemType Directory -Force $base | Out-Null

if (-not $Dataset) {
  $saved = if (Test-Path $configFile) { (Get-Content $configFile -Raw | ConvertFrom-Json).dataset } else { "" }
  $Dataset = Read-Host "Hugging Face dataset (owner/name) [$saved]"
  if (-not $Dataset) { $Dataset = $saved }
}
if ($Dataset -notmatch '^[\w.-]+/[\w.-]+$') { throw "Expected owner/name, got '$Dataset'." }
@{ dataset = $Dataset } | ConvertTo-Json | Set-Content -Path $configFile -Encoding utf8

if ($NewToken -or -not (Test-Path $tokenFile)) {
  $secure = Read-Host -AsSecureString "Hugging Face token with write access to $Dataset (input hidden)"
  if ($secure.Length -lt 20) { throw "That token is too short ($($secure.Length) characters): paste it with right-click, then press Enter." }
  $secure | ConvertFrom-SecureString | Set-Content -Path $tokenFile
  "Token stored encrypted in $tokenFile (only your Windows account can read it)."
}

$action = New-ScheduledTaskAction -Execute "conhost.exe" `
  -Argument ('--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f (Join-Path $repo "scripts\local_fetch.ps1")) `
  -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date -Minute 17 -Second 0) -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
  -Description "Perigee: downloads due CelesTrak data and pushes it to the private Hugging Face dataset (scripts\local_fetch.ps1)." -Force | Out-Null
"Registered the task '$taskName' (hourly at :17). Run it now with: Start-ScheduledTask -TaskName '$taskName'"
"Logs: $(Join-Path $base 'logs')   Last result: $(Join-Path $base 'last-status.json')"
