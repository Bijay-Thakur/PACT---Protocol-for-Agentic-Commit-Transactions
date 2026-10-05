$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pidFile = Join-Path $root '.local\servers.json'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host 'No PACT local server record found.'; return }
$servers = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
foreach ($name in @('frontend','backend')) {
    $record = $servers.$name
    $process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if ($process -and $process.StartTime.ToUniversalTime().ToString('o') -eq $record.started) {
        Stop-Process -Id $process.Id -Force
        Write-Host "Stopped $name."
    }
}
Remove-Item -LiteralPath $pidFile
