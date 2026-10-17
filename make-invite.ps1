# Make an SMP Host invite from this PC's settings (controller.env + the playit identity), for yourself or a friend.
# Each person gets their own Backblaze B2 application key (that bucket only, Read and Write). Send the invite privately:
# anyone holding it can host the world.
#   .\make-invite.ps1                     asks for the bucket, keyID and applicationKey
param([string]$Bucket = "", [string]$KeyId = "")
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$envFile = Join-Path $root "controller.env"
$python = Join-Path $root "controller\.venv\Scripts\python.exe"
if (-not (Test-Path $envFile)) { throw "Run .\setup.ps1 first." }
if (-not (Test-Path $python)) { throw "Run .\install-controller.ps1 first." }

$playit = "C:\ProgramData\playit_gg\playit.toml"
if (-not (Test-Path $playit)) { throw "The playit identity file ($playit) isn't on this PC. Run this on the PC whose tunnel you use." }

if (-not $Bucket) {
    $remote = (Get-Content $envFile | Where-Object { $_ -match '^SHARED_REMOTE=smp:' } | Select-Object -First 1)
    $default = if ($remote) { $remote.Split(":", 2)[1] } else { "" }
    $typed = (Read-Host "Bucket name$(if ($default) { " [$default]" })").Trim()
    $Bucket = if ($typed) { $typed } else { $default }
}
if (-not $KeyId) { $KeyId = (Read-Host "keyID for this person").Trim() }
$secure = Read-Host "applicationKey for this person (hidden)" -AsSecureString
$key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))

Push-Location (Join-Path $root "controller")
try {
    $code = & $python -m app.desktop.invite make --env $envFile --playit $playit --bucket $Bucket --key-id $KeyId --key $key
    if ($LASTEXITCODE -ne 0) { throw "Couldn't make the invite." }
} finally { Pop-Location }
Set-Clipboard -Value $code
Write-Host "`nThe invite is copied to the clipboard. Paste it into SMP Host, or send it PRIVATELY (a direct message)." -ForegroundColor Green
