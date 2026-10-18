# Move the shared world from Backblaze B2 to Cloudflare R2, once, from the owner's PC (the one with SMP Host set up).
#   .\move-to-r2.ps1                      asks for the Cloudflare account ID, the new bucket and an access key
# Before you start: nobody is hosting, SMP Host is closed, and the R2 bucket exists and is empty (HOSTING.md has the steps).
# It copies the world, checks every file arrived, points this PC at R2 and marks the old storage as moved.
param([string]$AccountId = "", [string]$Bucket = "", [string]$AccessKey = "", [switch]$Force)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root "controller\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Run .\install-controller.ps1 first." }
if (-not (Get-Command rclone -ErrorAction SilentlyContinue)) {
    throw "rclone isn't installed. Run: winget install Rclone.Rclone   then open a new PowerShell window and try again."
}
if (-not $AccountId) { $AccountId = (Read-Host "Cloudflare account ID (the long code in the R2 page's S3 API address)").Trim() }
if (-not $Bucket) { $Bucket = (Read-Host "New R2 bucket name").Trim() }
if (-not $AccessKey) { $AccessKey = (Read-Host "Access Key ID").Trim() }

Push-Location (Join-Path $root "controller")
try {
    $extra = @(); if ($Force) { $extra += "--force" }
    & $python -m app.desktop.migrate --account-id $AccountId --bucket $Bucket --access-key $AccessKey @extra
    if ($LASTEXITCODE -ne 0) { throw "The move didn't finish. Nothing was changed on this PC." }
} finally { Pop-Location }
