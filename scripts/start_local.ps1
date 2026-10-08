param(
    [switch]$SetupOperator
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $root '.env'
$python = Join-Path $root '.venv\Scripts\python.exe'
$next = Join-Path $root 'frontend\node_modules\next\dist\bin\next'
$local = Join-Path $root '.local'

if (-not (Test-Path -LiteralPath $envFile)) { throw "Missing $envFile. Copy .env.example and configure PACT_DATABASE_URL." }
if (-not (Test-Path -LiteralPath $python)) { throw 'Missing Python venv. Run: py -3.13 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e "backend[dev]"' }
if (-not (Test-Path -LiteralPath $next)) { throw 'Missing frontend dependencies. Run: cd frontend; npm.cmd ci' }

foreach ($line in Get-Content -LiteralPath $envFile) {
    if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}
$env:PACT_DEMO_MODE = 'true' # This launcher explicitly enables local simulator scenarios.
if ($env:PACT_PLANNER_PROVIDER -eq 'groq' -and -not ($env:PACT_PLANNER_API_KEY -or $env:GROQ_API_KEY)) {
    throw 'Groq planner selected, but neither PACT_PLANNER_API_KEY nor GROQ_API_KEY is set.'
}

function Set-UpOperator {
    Push-Location (Join-Path $root 'backend')
    try {
        Write-Host 'Create or refresh the local operator account. Choose a private password when prompted.'
        & $python -m app.cli create-operator local operator
        if ($LASTEXITCODE -ne 0) { throw 'Operator setup failed.' }
    } finally { Pop-Location }
}

New-Item -ItemType Directory -Path $local -Force | Out-Null
$pidFile = Join-Path $local 'servers.json'
if (Test-Path -LiteralPath $pidFile) {
    $recorded = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $matching = @('backend', 'frontend') | Where-Object {
        $record = $recorded.$_
        $process = if ($record) { Get-Process -Id $record.pid -ErrorAction SilentlyContinue } else { $null }
        $process -and $process.StartTime.ToUniversalTime().ToString('o') -eq $record.started
    }
    if ($matching.Count -eq 2) {
        try {
            $api = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/healthz' -TimeoutSec 2
            $page = Invoke-WebRequest -Uri 'http://127.0.0.1:3000/' -TimeoutSec 2 -UseBasicParsing
            if ($api.status -eq 'ok' -and $page.StatusCode -eq 200) {
                if ($SetupOperator) { Set-UpOperator }
                Write-Host 'PACT is already running: http://localhost:3000 (console), http://localhost:8000/docs (API).'
                return
            }
        } catch { }
    }
    Write-Host 'Recovering stale or partial PACT server record...'
    & (Join-Path $PSScriptRoot 'stop_local.ps1')
}

function Assert-Port-Free([int]$port) {
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $result = $client.BeginConnect('127.0.0.1', $port, $null, $null)
        if ($result.AsyncWaitHandle.WaitOne(500)) {
            try { $client.EndConnect($result); throw "Port $port is already in use." } catch [Net.Sockets.SocketException] { }
        }
    } finally { $client.Dispose() }
}
Assert-Port-Free 8000
Assert-Port-Free 3000

Push-Location (Join-Path $root 'backend')
try {
    Write-Host 'Applying PACT database migrations...'
    & $python -m app.cli migrate
    if ($LASTEXITCODE -ne 0) { throw 'Migration failed. Check PACT_DATABASE_URL and PostgreSQL.' }
    if ($SetupOperator) { Set-UpOperator }
} finally { Pop-Location }

$backend = $null
$frontend = $null
try {
    $backend = Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000') `
        -WorkingDirectory (Join-Path $root 'backend') -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $local 'backend.out.log') -RedirectStandardError (Join-Path $local 'backend.err.log')
    $healthy = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/healthz' -TimeoutSec 1
            if ($response.status -eq 'ok') { $healthy = $true; break }
        } catch { }
        if ($backend.HasExited) { break }
    }
    if (-not $healthy) { throw "Backend failed to start. See $local\backend.err.log" }

    $node = (Get-Command node.exe -ErrorAction Stop).Source
    $frontend = Start-Process -FilePath $node -ArgumentList @($next,'dev','-H','127.0.0.1','-p','3000') `
        -WorkingDirectory (Join-Path $root 'frontend') -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $local 'frontend.out.log') -RedirectStandardError (Join-Path $local 'frontend.err.log')
    $ready = $false
    for ($i = 0; $i -lt 80; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $response = Invoke-WebRequest -Uri 'http://127.0.0.1:3000/' -TimeoutSec 2 -UseBasicParsing
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
        if ($frontend.HasExited) { break }
    }
    if (-not $ready) { throw "Frontend failed to start. See $local\frontend.err.log" }

    [pscustomobject]@{
        backend = [pscustomobject]@{ pid = $backend.Id; started = $backend.StartTime.ToUniversalTime().ToString('o') }
        frontend = [pscustomobject]@{ pid = $frontend.Id; started = $frontend.StartTime.ToUniversalTime().ToString('o') }
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $pidFile -Encoding utf8
    Write-Host 'PACT is running: http://localhost:3000 (console), http://localhost:8000/docs (API).'
    Write-Host 'Sign in as tenant "local", username "operator" after running this script with -SetupOperator.'
    Write-Host 'Stop both servers with .\scripts\stop_local.ps1. Logs are in .local/.'
} catch {
    if ($frontend -and -not $frontend.HasExited) { Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue }
    if ($backend -and -not $backend.HasExited) { Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue }
    throw
}
