# Set up taking turns hosting (one world, several PCs) through Backblaze B2 cloud storage. Run it once on each PC.
#   .\setup-sharing.ps1          asks for the bucket name and THIS PC's B2 key, then sets everything up
# Options:
#   -Remote <rclone remote>      use storage you already set up in rclone instead of B2 (e.g. myremote:folder)
#   -HostName <name>             this PC's name while it hosts (must be different on every PC)
#   -Upload / -NoUpload          answer the "upload this PC's world?" question in advance
param([string]$Remote = "", [string]$HostName = "", [switch]$Upload, [switch]$NoUpload)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$envFile = Join-Path $root "controller.env"
if (-not (Test-Path $envFile)) { throw "Run .\setup.ps1 and .\install-controller.ps1 first." }

function Get-EnvValue([string]$key) {
    $line = Get-Content $envFile | Where-Object { $_ -match "^$key=" } | Select-Object -First 1
    if ($line) { return $line.Substring($key.Length + 1).Trim() } else { return "" }
}
function Set-EnvValue([string]$key, [string]$value) {
    $lines = @(Get-Content $envFile)
    if ($lines | Where-Object { $_ -match "^$key=" }) {
        $lines = $lines | ForEach-Object { if ($_ -match "^$key=") { "$key=$value" } else { $_ } }
    } else { $lines += "$key=$value" }
    [IO.File]::WriteAllLines($envFile, [string[]]$lines, (New-Object Text.UTF8Encoding($false)))
}
function Invoke-Rclone {
    # rclone prints notices on stderr; don't let PowerShell treat them as errors
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $out = & $script:rclone @args 2>&1 | ForEach-Object { "$_" } | Where-Object { $_ -notmatch "Config file .* not found" }
    $code = $LASTEXITCODE; $ErrorActionPreference = $old
    return [pscustomobject]@{ Code = $code; Out = ($out -join "`n").Trim() }
}

# --- 1. rclone ---------------------------------------------------------------------------------------
$cmd = Get-Command rclone -ErrorAction SilentlyContinue
if (-not $cmd) {
    Write-Host "Installing rclone..."
    winget install --id Rclone.Rclone --exact --silent --accept-package-agreements --accept-source-agreements | Out-Null
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    $cmd = Get-Command rclone -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "rclone didn't install. Run: winget install Rclone.Rclone   then run this script again." }
}
$script:rclone = $cmd.Source

# --- 2. the storage --------------------------------------------------------------------------------
if (-not $Remote) {
    Write-Host "`nBackblaze B2 (see HOSTING.md). You need the bucket name and THIS PC's own application key."
    $bucket = (Read-Host "Bucket name").Trim()
    $keyId = (Read-Host "keyID").Trim()
    $secure = Read-Host "applicationKey (hidden while you paste it)" -AsSecureString
    $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if (-not ($bucket -and $keyId -and $key)) { throw "The bucket name, keyID and applicationKey are all needed." }
    Invoke-Rclone config delete smp | Out-Null      # running the script again replaces the old key
    $r = Invoke-Rclone config create smp b2 "account=$keyId" "key=$key" "hard_delete=true" --non-interactive
    if ($r.Code -ne 0) { throw "Couldn't save the rclone settings: $($r.Out)" }
    $Remote = "smp:$bucket"
}
Write-Host "Checking access to $Remote ..."
$r = Invoke-Rclone lsf $Remote --max-depth 1
if ($r.Code -ne 0) {
    throw "Can't open $Remote. Check the bucket name, and that the key may read and write that bucket.`n$($r.Out)"
}
if ($Remote -like "smp:*") {
    # keep only the newest version of each file, so old copies don't fill the free 10 GB (the controller keeps its own history)
    $r = Invoke-Rclone backend lifecycle $Remote -o daysFromHidingToDeleting=1
    if ($r.Code -ne 0) {
        Write-Host "Note: on the B2 website, set the bucket's Lifecycle Settings to 'Keep only the last version of the file'." -ForegroundColor Yellow
    }
}

# --- 3. controller.env -----------------------------------------------------------------------------
if (-not $HostName) {
    $current = Get-EnvValue "HOST_NAME"
    if (-not $current) { $current = $env:USERNAME }
    $typed = (Read-Host "This PC's name while it hosts, different on every PC [$current]").Trim()
    $HostName = if ($typed) { $typed } else { $current }
}
$oldDir = Get-EnvValue "SHARED_DIR"
if ($oldDir) { Set-EnvValue "SHARED_DIR" "" }
Set-EnvValue "SHARED_REMOTE" $Remote
Set-EnvValue "HOST_NAME" $HostName
Write-Host "controller.env: SHARED_REMOTE=$Remote, HOST_NAME=$HostName" -ForegroundColor Green

# --- 4. the shared world ---------------------------------------------------------------------------
$status = (& (Join-Path $root "shared-world.ps1") status | Out-String).Trim()
if ($status -match "brand new") {
    Write-Host "`nThe shared world is still empty. Only ONE person (the owner, with the real world) uploads it, once."
    $answer = if ($Upload) { "y" } elseif ($NoUpload) { "n" } else { (Read-Host "Upload THIS PC's world as the shared world? (y/n)").Trim() }
    if ($answer -eq "y") {
        Write-Host "Uploading (a few minutes the first time)..."
        & (Join-Path $root "shared-world.ps1") upload
        if ($LASTEXITCODE -ne 0) { throw "The upload didn't finish; run .\shared-world.ps1 upload to try again." }
    }
} else {
    Write-Host "`n$status"
}

Write-Host "`nDone. If the controller is open, close its window and start it again (your Start shortcut)." -ForegroundColor Green
Write-Host "From now on: whoever starts first hosts; to switch, use 'Hand over hosting' in the panel or /jarvis handover."
