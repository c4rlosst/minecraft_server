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
if (-not (Get-Command java -ErrorAction SilentlyContinue)) { throw "Java not found. Install a current JDK (Temurin) and re-run." }
# java prints its version on stderr; run it through cmd so PowerShell 5.1 doesn't treat that as an error
$javaLine = (cmd /c "java -version 2>&1" | Select-Object -First 1).ToString()
if ($javaLine -notmatch '"(\d+)') { throw "Couldn't read the Java version: $javaLine" }
Write-Host "Java: $javaLine"
if ([int]$Matches[1] -lt 25) { Write-Warning "Minecraft $McVersion likely needs Java 25+. Update Java if the server fails to start." }

# --- Fabric server launcher ---------------------------------------------------------------
# Assign first, then filter: Windows PowerShell 5.1 doesn't unpack a JSON list piped straight from the request
$loaders = Invoke-RestMethod "https://meta.fabricmc.net/v2/versions/loader"
$installers = Invoke-RestMethod "https://meta.fabricmc.net/v2/versions/installer"
$loader = ($loaders | Where-Object { $_.stable -eq $true } | Select-Object -First 1).version
$installer = ($installers | Where-Object { $_.stable -eq $true } | Select-Object -First 1).version
if (-not $loader -or -not $installer) { throw "Couldn't read the latest Fabric loader/installer versions from meta.fabricmc.net." }
$jar = Join-Path $server "fabric-server-launch.jar"
Write-Host "Downloading Fabric server launcher (MC $McVersion, loader $loader)..."
Invoke-WebRequest "https://meta.fabricmc.net/v2/versions/loader/$McVersion/$loader/$installer/server/jar" -OutFile $jar -Headers $headers

# --- EULA ---------------------------------------------------------------------------------
$eula = Join-Path $server "eula.txt"
$eulaAccepted = (Test-Path $eula) -and (Select-String -Path $eula -Pattern "eula=true" -Quiet)
if (-not $eulaAccepted) {
    $answer = Read-Host "Do you accept the Minecraft EULA (https://aka.ms/MinecraftEULA)? Type 'yes'"
    if ($answer -ne "yes") { throw "EULA not accepted; the server cannot run without it." }
    Set-Content $eula "eula=true" -Encoding ascii
}

# --- server.properties (only created once, so your edits are kept) -------------------------
$props = Join-Path $server "server.properties"
function New-Secret([int]$Bytes = 18) {
    $b = New-Object byte[] $Bytes
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    ([Convert]::ToBase64String($b) -replace '[^A-Za-z0-9]', '')
}
function Get-Prop($file, $key) {
    $line = Get-Content $file -ErrorAction SilentlyContinue | Where-Object { $_ -like "$key=*" } | Select-Object -First 1
    if ($line) { $line.Substring($key.Length + 1) }
}
function Set-Prop($file, $key, $value) {
    $lines = @(Get-Content $file -ErrorAction SilentlyContinue)
    if ($lines | Where-Object { $_ -like "$key=*" }) { $lines = $lines | ForEach-Object { if ($_ -like "$key=*") { "$key=$value" } else { $_ } } }
    else { $lines += "$key=$value" }
    Set-Content $file $lines -Encoding ascii
}

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
# The controller owns public port 25565 and talks to the real server privately on 25566 (localhost only)
# and over RCON, so friends can't bypass the whitelist-aware wake gateway.
Set-Prop $props "server-port" "25566"
Set-Prop $props "server-ip" "127.0.0.1"
Set-Prop $props "enable-rcon" "true"
Set-Prop $props "rcon.port" "25575"
Set-Prop $props "broadcast-rcon-to-ops" "false"
$rconPassword = Get-Prop $props "rcon.password"
if (-not $rconPassword) { $rconPassword = New-Secret; Set-Prop $props "rcon.password" $rconPassword }

# --- Controller settings (created once; holds secrets, git-ignored) --------------------------
$controllerEnv = Join-Path $root "controller.env"
if (-not (Test-Path $controllerEnv)) {
    $panelPassword = New-Secret 12
    @"
# Settings for the Windows controller (run-controller.ps1). Restart the controller after editing.
RCON_PASSWORD=$rconPassword
PANEL_PASSWORD=$panelPassword
SERVER_NAME=$Motd
# What friends type into Minecraft, e.g. your playit.gg tunnel address
PUBLIC_ADDRESS=
# Your Minecraft name(s), comma separated: they get operator rights each time the server starts
OPS=
MC_MEMORY=6G
IDLE_MINUTES=10
BACKUPS_KEEP=14
# Discord bot (leave empty to disable). See README.
DISCORD_TOKEN=
DISCORD_GUILD_ID=
DISCORD_CHANNEL_ID=
DISCORD_ROLE_ID=
# Discord user IDs who may NOT use the commands (comma separated). Admins/the server owner are never blocked.
DISCORD_BLOCKED_IDS=
# playit.gg agent started alongside the controller (empty = don't manage it)
PLAYIT_EXE=playit
"@ | Set-Content $controllerEnv -Encoding ascii
    Write-Host "Created controller.env. Panel password (also saved in that file): $panelPassword"
}
if (Test-Path $controllerEnv) { Set-Prop $controllerEnv "MC_VERSION" $McVersion }   # lets the panel check uploaded modpacks

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

Write-Host "`nDone. Next: edit controller.env, then run .\install-controller.ps1 (see README)."
