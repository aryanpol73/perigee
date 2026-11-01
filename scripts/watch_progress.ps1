# Follow a Perigee JSON log as readable lines (Ctrl-C to stop).
#   .\scripts\watch_progress.ps1 data\probe\train.log
param([Parameter(Mandatory = $true)][string]$Log)

$skip = 'ts', 'level', 'run_id', 'logger', 'msg'
Get-Content $Log -Wait -Tail 50 | ForEach-Object {
    try { $e = $_ | ConvertFrom-Json } catch { Write-Host $_; return }
    $time = ([datetime]$e.ts).ToLocalTime().ToString('HH:mm:ss')
    $details = ($e.PSObject.Properties | Where-Object { $skip -notcontains $_.Name } |
        ForEach-Object { "$($_.Name)=$($_.Value)" }) -join '  '
    $color = if ($e.level -eq 'WARNING') { 'Yellow' } elseif ($e.level -eq 'ERROR') { 'Red' } else { 'Gray' }
    Write-Host "$time  " -NoNewline
    Write-Host $e.msg -ForegroundColor Cyan -NoNewline
    Write-Host "  $details" -ForegroundColor $color
}
