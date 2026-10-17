# Look at, or seed, the shared world by hand (shared hosting only; needs SHARED_REMOTE or SHARED_DIR in controller.env).
#   .\shared-world.ps1 status    who is hosting, which upload is stored, and whether it matches the record
#   .\shared-world.ps1 upload    put THIS PC's world into the shared storage (the first time, or to repair it).
#                                Close the controller first.
param([ValidateSet("status", "upload")][string]$Action = "status")
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$envFile = Join-Path $root "controller.env"
$python = Join-Path $root "controller\.venv\Scripts\python.exe"
if (-not (Test-Path $envFile)) { throw "Run .\setup.ps1 first." }
if (-not (Test-Path $python)) { throw "Run .\install-controller.ps1 first." }

$env:BACKEND = "process"
$env:SERVER_DIR = Join-Path $root "server"
$env:STATE_DIR = Join-Path $root "state"
foreach ($line in Get-Content $envFile) {
    $line = $line.Trim()
    if (-not $line -or $line.StartsWith("#")) { continue }
    $key, $value = $line -split "=", 2
    Set-Item -Path "env:$($key.Trim())" -Value $value.Trim()
}

Push-Location (Join-Path $root "controller")
try { & $python -m app.sharedtool $Action; $code = $LASTEXITCODE }
finally { Pop-Location }
exit $code
