# AidTrace demo launcher (synthetic data only).
#   Right-click > "Run with PowerShell", or:  powershell -ExecutionPolicy Bypass -File run_demo.ps1
# Opens http://localhost:8000 . Stop with Ctrl+C in this window.
$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot
$py   = Join-Path $env:USERPROFILE ".venvs\aidtrace\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Python environment not found at $py. See README.md > Setup." }

# Build the two front-ends once if their builds are missing.
$env:Path = [Environment]::GetEnvironmentVariable('Path','User') + ';' + [Environment]::GetEnvironmentVariable('Path','Machine')
$env:NODE_OPTIONS = "--use-system-ca"
foreach ($app in "web", "recipient") {
    if (-not (Test-Path (Join-Path $repo "$app\dist\index.html"))) {
        Write-Host "Building $app ..."
        Push-Location (Join-Path $repo $app)
        if (-not (Test-Path node_modules)) { npm install --no-audit --no-fund }
        npm run build
        Pop-Location
    }
}

$env:AIDTRACE_MODE = "demo"          # separate demo database and keys in ~/.aidtrace/demo (seeded on first run)
Write-Host ""
Write-Host "AidTrace demo starting at http://localhost:8000  (synthetic data only; Ctrl+C to stop)"
Write-Host "Recipient demo phone: http://localhost:8000/recipient/   demo PINs: normal 2468, safety 1357"
Start-Job -ScriptBlock { Start-Sleep 6; Start-Process "http://localhost:8000" } | Out-Null
& $py -I -m aidtrace_server.cli serve --port 8000
