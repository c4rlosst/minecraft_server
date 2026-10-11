# Run the SMP from ANY friend's PC. The world lives in a shared cloud folder; this script takes a lock,
# pulls the latest world, runs the server, then pushes the world back and releases the lock.
#   .\host.ps1            start hosting
#   .\host.ps1 -Status    show who is hosting (or that it's free)
#   .\host.ps1 -Force     take over after a crash / stale lock (may lose the crashed host's latest progress)
# Always shut the server down with "stop" in the console so the world gets saved and uploaded.
param([string]$Ram = "6G", [switch]$Status, [switch]$Force)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$server = Join-Path $root "server"
$configPath = Join-Path $root "host.config.ps1"
if (-not (Test-Path $configPath)) { throw "Copy host.config.example.ps1 to host.config.ps1 and fill it in." }
. $configPath   # sets $SharedDir, $HostName, $PlayitExe, $PlayitArgs
if (-not (Test-Path $SharedDir)) { throw "Shared folder not found: $SharedDir (is your cloud app installed and syncing?)" }

$statePath = Join-Path $SharedDir "state.json"
$dataDir = Join-Path $SharedDir "data"
$syncDirs = "world", "mods", "config"
$syncFiles = "whitelist.json", "ops.json", "banned-players.json", "banned-ips.json", "server.properties"
$staleMinutes = 5

function Read-State { if (Test-Path $statePath) { Get-Content $statePath -Raw | ConvertFrom-Json } }
function Write-State($s) {
    $tmp = "$statePath.tmp"
    $s | ConvertTo-Json | Set-Content $tmp -Encoding utf8
    Move-Item $tmp $statePath -Force
}
function Get-Manifest($base) {
    $list = @()
    foreach ($d in $syncDirs) {
        $p = Join-Path $base $d
        if (Test-Path $p) { $list += Get-ChildItem $p -Recurse -File | Where-Object Name -ne "session.lock" }
    }
    foreach ($f in $syncFiles) {
        $p = Join-Path $base $f
        if (Test-Path $p) { $list += Get-Item $p }
    }
    [pscustomobject]@{ count = $list.Count; bytes = [long]($list | Measure-Object Length -Sum).Sum }
}
function Sync-Tree($from, $to) {
    foreach ($d in $syncDirs) {
        $src = Join-Path $from $d
        if (Test-Path $src) {
            robocopy $src (Join-Path $to $d) /MIR /R:2 /W:2 /XF session.lock /NFL /NDL /NJH /NJS /NP | Out-Null
            if ($LASTEXITCODE -ge 8) { throw "Copy failed for '$d' (robocopy code $LASTEXITCODE)." }
        }
    }
    foreach ($f in $syncFiles) {
        $src = Join-Path $from $f
        if (Test-Path $src) { New-Item -ItemType Directory -Force $to | Out-Null; Copy-Item $src (Join-Path $to $f) -Force }
    }
}
function Get-AgeMinutes($s) { ((Get-Date).ToUniversalTime() - ([datetime]$s.lastSeen).ToUniversalTime()).TotalMinutes }

$state = Read-State

if ($Status) {
    if (-not $state) { "No world shared yet. Whoever runs .\host.ps1 first creates it." }
    elseif ($state.status -eq "hosting") { "{0} is hosting (since {1}, last heartbeat {2:N0} min ago)." -f $state.host, $state.since, (Get-AgeMinutes $state) }
    else { "Free. Last hosted by $($state.lastHost) (world version $($state.version))." }
    return
}

# --- 1. Is someone else hosting? ----------------------------------------------------------
if ($state -and $state.status -eq "hosting") {
    $age = Get-AgeMinutes $state
    if ($age -lt $staleMinutes -and -not $Force) {
        throw "$($state.host) is already hosting (since $($state.since)). Join their server instead."
    }
    Write-Warning "$($state.host) was hosting but hasn't checked in for $([int]$age) min (crashed or still uploading?)."
    Write-Warning "Taking over uses the last UPLOADED world; any progress that PC didn't upload is lost."
    if ((Read-Host "Type 'takeover' to continue") -ne "takeover") { return }
}
if (-not (Test-Path (Join-Path $server "fabric-server-launch.jar"))) { throw "Run .\setup.ps1 first." }

# --- 2. Wait for the cloud app to finish downloading the last upload ----------------------
if ($state -and $state.version -gt 0) {
    $deadline = (Get-Date).AddMinutes(20)
    while ($true) {
        $m = Get-Manifest $dataDir
        if ($m.count -eq $state.count -and $m.bytes -eq $state.bytes) { break }
        if ((Get-Date) -gt $deadline) {
            if ($Force) { Write-Warning "Sync never completed; continuing because of -Force."; break }
            throw "Cloud folder still doesn't match the last upload. Check your cloud app finished syncing, then retry."
        }
        Write-Host "Waiting for cloud sync ($($m.count)/$($state.count) files, $([math]::Round($m.bytes/1MB))/$([math]::Round($state.bytes/1MB)) MB)..."
        Start-Sleep 10
    }
}

# --- 3. Claim the lock (re-check after a pause in case two people started at once) --------
$prev = $state
$token = [guid]::NewGuid().ToString()
$now = (Get-Date).ToUniversalTime().ToString("o")
$claim = [ordered]@{
    status = "hosting"; host = $HostName; token = $token; since = $now; lastSeen = $now
    version = if ($prev) { $prev.version } else { 0 }
    count = if ($prev) { $prev.count } else { 0 }
    bytes = if ($prev) { $prev.bytes } else { 0 }
    lastHost = if ($prev) { $prev.lastHost } else { $null }
}
Write-State $claim
Write-Host "Lock claimed, double-checking nobody else did at the same time..."
Start-Sleep 20
$check = Read-State
$conflicts = Get-ChildItem $SharedDir -File | Where-Object { $_.Name -like "state*" -and $_.Name -ne "state.json" }
if ($check.token -ne $token -or $conflicts) {
    throw "Someone else grabbed the lock at the same moment (or a sync conflict file exists in $SharedDir). Ask in chat who is hosting, delete any 'state (conflict)' files, and retry."
}

$hb = $null; $playit = $null; $ran = $false
try {
    # --- 4. Pull the latest world -----------------------------------------------------------
    if ($prev -and $prev.version -gt 0) {
        if (Test-Path (Join-Path $server "world")) { try { & (Join-Path $root "backup.ps1") } catch { Write-Warning "Local pre-pull backup failed: $_" } }
        Write-Host "Pulling latest world (version $($prev.version)) from the shared folder..."
        Sync-Tree $dataDir $server
    }

    # --- 5. Heartbeat so others can tell a crash from a live host ---------------------------
    $hb = Start-Job -ArgumentList $statePath, $token -ScriptBlock {
        param($sp, $tok)
        while ($true) {
            Start-Sleep 60
            try {
                $s = Get-Content $sp -Raw | ConvertFrom-Json
                if ($s.token -eq $tok) {
                    $s.lastSeen = (Get-Date).ToUniversalTime().ToString("o")
                    $s | ConvertTo-Json | Set-Content $sp -Encoding utf8
                }
            } catch { }
        }
    }

    # --- 6. Tunnel (same playit.gg address no matter who hosts) -----------------------------
    $playitCmd = if ($PlayitExe) { Get-Command $PlayitExe -ErrorAction SilentlyContinue }
    if ($playitCmd) {
        if ($PlayitSecret) { $env:SECRET_KEY = $PlayitSecret }   # the agent reads its shared key from SECRET_KEY
        $playit = Start-Process $playitCmd.Source -PassThru -WindowStyle Minimized
        Write-Host "playit.gg agent started. Friends connect to your shared tunnel address."
    } else {
        Write-Warning "playit agent not found (PlayitExe='$PlayitExe'): start your tunnel yourself so friends can connect."
    }

    # --- 7. Run the server ------------------------------------------------------------------
    $ran = $true
    & (Join-Path $root "start.ps1") -Ram $Ram
}
finally {
    if ($hb) { Stop-Job $hb -ErrorAction SilentlyContinue; Remove-Job $hb -Force -ErrorAction SilentlyContinue }
    if ($playit -and -not $playit.HasExited) { Stop-Process $playit -Force -ErrorAction SilentlyContinue }

    if ($ran) {
        Write-Host "`nUploading world to the shared folder - do NOT close this window or shut down yet..."
        Sync-Tree $server $dataDir
        $m = Get-Manifest $dataDir
        Write-State ([ordered]@{
            status = "idle"; host = $null; token = $null; since = $null
            lastSeen = (Get-Date).ToUniversalTime().ToString("o")
            version = [int]$claim.version + 1; count = $m.count; bytes = $m.bytes; lastHost = $HostName
        })
        Write-Host "Done. World version $([int]$claim.version + 1) saved. Let your cloud app finish syncing before turning off the PC."
    } else {
        # Failed before the server ever ran: put the previous state back so nobody stays locked out.
        if ($prev) { Write-State $prev } else { Remove-Item $statePath -Force -ErrorAction SilentlyContinue }
    }
}
