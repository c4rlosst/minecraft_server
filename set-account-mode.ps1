# Switch the server between account modes. It stops the Minecraft server itself (saving the world) if needed.
#   .\set-account-mode.ps1 -Offline   let players without a verified Microsoft account join. The whitelist is turned
#                                     off and EasyAuth passwords (/register, /login) protect every name instead.
#   .\set-account-mode.ps1 -Online    back to Mojang-verified accounts and the whitelist (removes EasyAuth).
# After running it, (re)start the controller: .\run-controller.ps1
# Old copies of the files are saved in server\account-mode-backup-<time>\.
param([switch]$Offline, [switch]$Online, [switch]$NoSetup)

$ErrorActionPreference = "Stop"
if ($Offline -eq $Online) { throw "Choose exactly one: -Offline or -Online" }
$root = $PSScriptRoot
$server = Join-Path $root "server"
$props = Join-Path $server "server.properties"
$modsTxt = Join-Path $root "mods.txt"
$controllerEnv = Join-Path $root "controller.env"
if (-not (Test-Path $props)) { throw "Run .\setup.ps1 first." }

function Get-Prop($key) {
    $line = Get-Content $props | Where-Object { $_ -like "$key=*" } | Select-Object -First 1
    if ($line) { $line.Substring($key.Length + 1) }
}
function Set-KeyValue($file, $key, $value) {
    $lines = @(Get-Content $file -ErrorAction SilentlyContinue)
    if ($lines | Where-Object { $_ -like "$key=*" }) { $lines = $lines | ForEach-Object { if ($_ -like "$key=*") { "$key=$value" } else { $_ } } }
    else { $lines += "$key=$value" }
    Set-Content $file $lines -Encoding ascii
}

# --- stop the Minecraft server if it is running ------------------------------------------------
$pidFile = Join-Path $root "state\mc.pid"
$serverProc = $null
if (Test-Path $pidFile) {
    $serverProc = Get-Process -Id ([int](Get-Content $pidFile)) -ErrorAction SilentlyContinue
    if ($serverProc -and $serverProc.ProcessName -notlike "java*") { $serverProc = $null }
}
if ($serverProc) {
    Write-Host "Stopping the Minecraft server (saving the world)..."
    $venvPython = Join-Path $root "controller\.venv\Scripts\python.exe"
    $python = if (Test-Path $venvPython) { $venvPython } else { "python" }
    $env:RCON_PW = Get-Prop "rcon.password"
    $env:RCON_PORT_NUM = Get-Prop "rcon.port"
    & $python -c "import asyncio,os,sys; sys.path.insert(0, sys.argv[1]); from app.rcon import rcon; print(asyncio.run(rcon('127.0.0.1', int(os.environ['RCON_PORT_NUM']), os.environ['RCON_PW'], 'stop', 15)))" (Join-Path $root "controller")
    $env:RCON_PW = $null
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Process -Id $serverProc.Id -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
    if (Get-Process -Id $serverProc.Id -ErrorAction SilentlyContinue) { throw "The server didn't stop within 90 seconds. Stop it from the panel, then run this again." }
}

# --- back up what we're about to change --------------------------------------------------------
$backup = Join-Path $server ("account-mode-backup-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Force $backup | Out-Null
foreach ($f in "server.properties", "whitelist.json", "ops.json", "usercache.json") {
    if (Test-Path (Join-Path $server $f)) { Copy-Item (Join-Path $server $f) $backup }
}

# --- server.properties -------------------------------------------------------------------------
$onlineValue = if ($Offline) { "false" } else { "true" }
Set-KeyValue $props "online-mode" $onlineValue
Set-KeyValue $props "white-list" $onlineValue            # offline: off (passwords protect names), online: on
Set-KeyValue $props "enforce-whitelist" $onlineValue
Set-KeyValue $props "enforce-secure-profile" $onlineValue # offline players have no signed chat keys
if (Test-Path $controllerEnv) { Set-KeyValue $controllerEnv "ACCOUNT_MODE" $(if ($Offline) { "offline" } else { "online" }) }

# --- UUIDs differ between modes: clear files keyed by UUID, keep the names for going back online
$whitelist = Join-Path $server "whitelist.json"
$names = @()
if (Test-Path $whitelist) {
    $names = @((Get-Content $whitelist -Raw | ConvertFrom-Json) | ForEach-Object { $_.name } | Where-Object { $_ })
    Remove-Item $whitelist
}
if (Test-Path (Join-Path $server "ops.json")) { Remove-Item (Join-Path $server "ops.json") }   # rewritten from OPS at start
$usercache = Join-Path $server "usercache.json"   # name -> UUID memory from the other mode would be reused wrongly
if (Test-Path $usercache) { Remove-Item $usercache }   # a copy is already in the backup folder
$stateDir = Join-Path $root "state"
New-Item -ItemType Directory -Force $stateDir | Out-Null
$pendingPath = Join-Path $stateDir "pending_whitelist.json"
$add = @(); $remove = @()
if (Test-Path $pendingPath) {
    $old = Get-Content $pendingPath -Raw | ConvertFrom-Json
    $add = @($old.add); $remove = @($old.remove)
}
$add = @(($add + $names) | Where-Object { $_ } | Sort-Object -Unique)
# build the JSON by hand so one-element lists stay lists (Windows PowerShell 5.1 would flatten them)
function ToJsonList($items) { "[" + ((@($items) | ForEach-Object { '"' + $_ + '"' }) -join ",") + "]" }
Set-Content $pendingPath ('{"add": ' + (ToJsonList $add) + ', "remove": ' + (ToJsonList $remove) + '}') -Encoding ascii

# --- EasyAuth ----------------------------------------------------------------------------------
$modLines = @(Get-Content $modsTxt)
$hasEasyAuth = [bool]($modLines | Where-Object { $_.Trim() -eq "easyauth" })
if ($Offline -and -not $hasEasyAuth) {
    Add-Content $modsTxt "`n# Passwords for players on an offline-mode server (added by set-account-mode.ps1)`neasyauth"
}
if ($Online) {
    if ($hasEasyAuth) { Set-Content $modsTxt ($modLines | Where-Object { $_.Trim() -ne "easyauth" -and $_ -notlike "*added by set-account-mode.ps1*" }) }
    Get-ChildItem (Join-Path $server "mods") -Filter "easyauth*.jar" -ErrorAction SilentlyContinue | ForEach-Object { Remove-Item -LiteralPath $_.FullName }
}

Write-Host "online-mode=$onlineValue, whitelist $(if ($Offline) { 'OFF' } else { 'ON' })."
if ($Offline -and -not $NoSetup) { Write-Host "Installing EasyAuth..."; & (Join-Path $root "setup.ps1") }

if ($Offline) {
    Write-Host "`nOffline mode is on. Next:"
    Write-Host "  1. Restart the controller:  .\run-controller.ps1   (close the old controller window first)"
    Write-Host "  2. Join the server yourself FIRST and run:  /register <password> <password>"
    Write-Host "     (whoever registers a name first owns it, so register the operator account before sharing the address)"
    Write-Host "  3. Tell each friend: join, then type /register <password> <password>; next time /login <password>."
    Write-Host "  4. Admin tools (as operator): /auth list, /auth update <name> <newpassword>, /auth remove <name>"
} else {
    Write-Host "`nOnline mode is on again: only Mojang-verified accounts can join, whitelist on."
    Write-Host "Restart the controller:  .\run-controller.ps1   (close the old controller window first)"
}
