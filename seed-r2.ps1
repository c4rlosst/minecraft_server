# Start the Cloudflare R2 bucket from the world rebuilt on this PC (desktop\recovery\v21_staging), without reading from Backblaze.
#   .\seed-r2.ps1             asks for the Cloudflare account ID, the new bucket, the access key ID and (hidden) the secret
# Before you start: SMP Host is closed, nobody is hosting, and the R2 bucket exists and is EMPTY (HOSTING.md has the steps).
# It uploads the world, checks every file arrived, points this PC at R2 and marks the old Backblaze bucket as moved.
param([string]$AccountId = "", [string]$Bucket = "", [string]$AccessKey = "")
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root "controller\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Run .\install-controller.ps1 first." }
$fresh = Join-Path $root "desktop\recovery\v21_staging\world\level.dat"
$laidOut = Join-Path $root "desktop\recovery\v21_bucket\data\world\level.dat"     # a first run already moved it here
if (-not ((Test-Path $fresh) -or (Test-Path $laidOut))) {
    throw "The rebuilt world (desktop\recovery) isn't here. Nothing was changed."
}
if (-not $AccountId) { $AccountId = (Read-Host "Cloudflare account ID").Trim() }
if (-not $Bucket) { $Bucket = (Read-Host "New R2 bucket name").Trim() }
if (-not $AccessKey) { $AccessKey = (Read-Host "Access Key ID").Trim() }

Push-Location (Join-Path $root "controller")
try {
    & $python -m app.desktop.seed --account-id $AccountId --bucket $Bucket --access-key $AccessKey
    if ($LASTEXITCODE -ne 0) { throw "It didn't finish. Nothing was deleted anywhere." }
} finally { Pop-Location }
