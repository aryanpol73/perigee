# The CelesTrak data job, run on this PC by Windows Task Scheduler (every hour;
# the fetch log decides what is actually due). Same steps as the data-refresh
# workflow: pull state and the latest raw snapshots from the private Hugging Face
# dataset, rebuild the tables, fetch what is due, push the new snapshots and state.
#
# It runs here, not on GitHub, because GitHub's runners share IP addresses with
# other projects: CelesTrak answers a second download of the same data from one IP
# with 403 and firewalls IPs that collect them (docs/data-job.md).
#
# Set up once with scripts\install_local_fetch.ps1. Logs: %LOCALAPPDATA%\Perigee\logs.
$ErrorActionPreference = "Continue" # Python writes its JSON logs to stderr; exit codes decide
$repo = Split-Path -Parent $PSScriptRoot
$base = Join-Path $env:LOCALAPPDATA "Perigee"
$work = Join-Path $base "fetch"
$logs = Join-Path $base "logs"
$python = Join-Path $repo ".venv\Scripts\python.exe"
New-Item -ItemType Directory -Force $work, $logs | Out-Null
$log = Join-Path $logs ("fetch-{0:yyyy-MM-dd}.log" -f (Get-Date))
Get-ChildItem $logs -Filter "fetch-*.log" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item -Force

function Write-Log([string]$text) { Add-Content -Path $log -Encoding UTF8 -Value ((Get-Date).ToUniversalTime().ToString("u") + " " + $text) }
# Python's output (its JSON logs go to stderr), as UTF-8 text lines.
function Out-Log { process { Add-Content -Path $log -Encoding UTF8 -Value "$_" } }

# The previous run's result: alert once when something goes wrong, not every hour.
$previous = try { (Get-Content (Join-Path $base "last-status.json") -Raw | ConvertFrom-Json).exit_code } catch { 0 }

function Show-Alert([int]$code, [string]$text) {
  # A tray notification, so a stopped job is noticed without opening GitHub.
  if ($code -eq $previous) { return }
  try {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $icon = New-Object System.Windows.Forms.NotifyIcon
    $icon.Icon = [System.Drawing.SystemIcons]::Warning
    $icon.Visible = $true
    $icon.ShowBalloonTip(15000, "Perigee data fetch", $text, [System.Windows.Forms.ToolTipIcon]::Warning)
    Start-Sleep -Seconds 16
    $icon.Dispose()
  } catch { Write-Log "could not show a notification: $_" }
}

function Save-Status([int]$code, [string]$text) {
  @{ at = (Get-Date).ToUniversalTime().ToString("o"); exit_code = $code; message = $text } |
    ConvertTo-Json | Set-Content -Path (Join-Path $base "last-status.json") -Encoding utf8
}

# One run at a time (the fetch log and STOP file must not be written twice).
$mutex = New-Object System.Threading.Mutex($false, "Local\PerigeeDataFetch")
if (-not $mutex.WaitOne(0)) { Write-Log "another run is in progress; skipped"; exit 0 }

try {
  $config = Get-Content (Join-Path $base "config.json") -Raw | ConvertFrom-Json
  $secure = (Get-Content (Join-Path $base "hf_token.dat") -Raw).Trim() | ConvertTo-SecureString # readable only by this Windows user
  $env:HF_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
  $env:PERIGEE_HF_DATASET = $config.dataset
  # A fresh copy every run, like a CI runner: the dataset is the only state.
  $data = Join-Path $work "data"
  if (Test-Path $data) { Remove-Item -Recurse -Force $data }
  $env:PERIGEE_DATA_DIR = $data
  Set-Location $repo

  Write-Log "start"
  & $python -m src.ops.archive_sync pull 2>&1 | Out-Log
  if ($LASTEXITCODE -ne 0) { throw "pulling from the dataset failed (exit $LASTEXITCODE)" }
  & $python -m src.main data --offline 2>&1 | Out-Log
  if ($LASTEXITCODE -ne 0) { throw "rebuilding the tables failed (exit $LASTEXITCODE)" }
  & $python -m src.main data 2>&1 | Out-Log
  $code = $LASTEXITCODE
  # Always push: the fetch log, any STOP file and quarantined downloads must reach the dataset.
  & $python -m src.ops.archive_sync push 2>&1 | Out-Log
  if ($LASTEXITCODE -ne 0) { throw "pushing to the dataset failed (exit $LASTEXITCODE)" }

  if ($code -eq 0) {
    Write-Log "done"
    Save-Status 0 "ok"
  } elseif ($code -eq 2) {
    $stop = Get-Content (Join-Path $data "state\STOP_CELESTRAK") -Raw -ErrorAction SilentlyContinue
    $text = "CelesTrak refused a request. Downloads are stopped until you delete state/STOP_CELESTRAK in the dataset (docs/data-job.md)."
    Write-Log "$text $stop"
    Save-Status 2 $text
    Show-Alert 2 $text
  } else {
    $text = "A download was rejected (exit $code); the last good data is kept. Log: $log"
    Write-Log $text
    Save-Status $code $text
    Show-Alert $code $text
  }
  exit $code
} catch {
  $text = "The data fetch failed: $_. Log: $log"
  Write-Log $text
  Save-Status 1 $text
  Show-Alert 1 $text
  exit 1
} finally {
  Remove-Item Env:HF_TOKEN -ErrorAction SilentlyContinue
  $mutex.ReleaseMutex()
}
