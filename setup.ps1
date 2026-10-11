# One-time setup: downloads the Fabric server, writes server.properties, installs mods from mods.txt.
# Re-run any time to add mods from mods.txt or update them. Existing world/config is never touched.
param(
    [string]$McVersion = "26.3",
    [int]$MaxPlayers = 10,
    [string]$Motd = "Our SMP"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$server = Join-Path $root "server"
$headers = @{ "User-Agent" = "minecraft_server-smp/1.0 (private friends server)" }
New-Item -ItemType Directory -Force $server, (Join-Path $server "mods") | Out-Null

# --- Java check ---------------------------------------------------------------------------
$javaLine = (& java -version 2>&1 | Select-Object -First 1).ToString()
if ($javaLine -notmatch '"(\d+)') { throw "Java not found. Install a current JDK (Temurin) and re-run." }
Write-Host "Java: $javaLine"
if ([int]$Matches[1] -lt 25) { Write-Warning "Minecraft $McVersion likely needs Java 25+. Update Java if the server fails to start." }

# --- Fabric server launcher ---------------------------------------------------------------
$loader = (Invoke-RestMethod "https://meta.fabricmc.net/v2/versions/loader" | Where-Object stable | Select-Object -First 1).version
$installer = (Invoke-RestMethod "https://meta.fabricmc.net/v2/versions/installer" | Where-Object stable | Select-Object -First 1).version
$jar = Join-Path $server "fabric-server-launch.jar"
Write-Host "Downloading Fabric server launcher (MC $McVersion, loader $loader)..."
Invoke-WebRequest "https://meta.fabricmc.net/v2/versions/loader/$McVersion/$loader/$installer/server/jar" -OutFile $jar -Headers $headers

# --- EULA ---------------------------------------------------------------------------------
$eula = Join-Path $server "eula.txt"
if (-not (Select-String -Path $eula -Pattern "eula=true" -Quiet -ErrorAction SilentlyContinue)) {
    $answer = Read-Host "Do you accept the Minecraft EULA (https://aka.ms/MinecraftEULA)? Type 'yes'"
    if ($answer -ne "yes") { throw "EULA not accepted; the server cannot run without it." }
    Set-Content $eula "eula=true" -Encoding ascii
}

# --- server.properties (only created once, so your edits are kept) -------------------------
$props = Join-Path $server "server.properties"
if (-not (Test-Path $props)) {
    @"
motd=$Motd
max-players=$MaxPlayers
white-list=true
enforce-whitelist=true
online-mode=true
difficulty=normal
view-distance=10
simulation-distance=8
spawn-protection=0
enable-command-block=false
"@ | Set-Content $props -Encoding ascii
}

# --- Mods from Modrinth -------------------------------------------------------------------
$modsDir = Join-Path $server "mods"
$slugs = Get-Content (Join-Path $root "mods.txt") | ForEach-Object { ($_ -split '#')[0].Trim() } | Where-Object { $_ }
foreach ($slug in $slugs) {
    $q = "loaders=" + [uri]::EscapeDataString('["fabric"]') + "&game_versions=" + [uri]::EscapeDataString("[`"$McVersion`"]")
    try { $versions = Invoke-RestMethod "https://api.modrinth.com/v2/project/$slug/version?$q" -Headers $headers }
    catch { Write-Warning "${slug}: not found on Modrinth, skipped."; continue }
    if (-not $versions) { Write-Warning "${slug}: no Fabric build for $McVersion yet, skipped."; continue }
    $file = ($versions[0].files | Where-Object primary | Select-Object -First 1)
    if (-not $file) { $file = $versions[0].files[0] }
    Get-ChildItem $modsDir -Filter "$slug*.jar" -ErrorAction SilentlyContinue | Remove-Item   # drop older copy
    Invoke-WebRequest $file.url -OutFile (Join-Path $modsDir $file.filename) -Headers $headers
    Write-Host "  + $slug ($($versions[0].version_number))"
}

Write-Host "`nDone. Start the server with .\start.ps1"
