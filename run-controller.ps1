# Runs the controller (wake-on-join gateway, sleep timer, web panel, Discord bot) and the playit.gg agent.
# Leave it running. It starts the Minecraft server by itself when a whitelisted friend joins.
#   -Takeover   shared hosting only: take over from a host whose PC crashed (no heartbeat for 5+ minutes).
#               Whatever that host had not uploaded is lost. A host who is still alive is never overridden.
param([switch]$Takeover)
$ErrorActionPreference = "Stop"
if ($Takeover) { $env:TAKEOVER = "1" }
$root = $PSScriptRoot
$envFile = Join-Path $root "controller.env"
$python = Join-Path $root "controller\.venv\Scripts\python.exe"
if (-not (Test-Path $envFile)) { throw "Run .\setup.ps1 first." }
if (-not (Test-Path $python)) { throw "Run .\install-controller.ps1 first." }

# Defaults for the Windows layout (controller.env can override any of them)
$env:BACKEND = "process"
$env:MC_HOST = "127.0.0.1"
$env:MC_PORT = "25566"
$env:LISTEN_HOST = "127.0.0.1"   # only the playit agent (same PC) needs to reach it
$env:PANEL_HOST = "127.0.0.1"    # panel is private to this PC
$env:SERVER_DIR = Join-Path $root "server"
$env:WHITELIST_PATH = Join-Path $root "server\whitelist.json"
$env:STATE_DIR = Join-Path $root "state"
$env:BACKUP_DIR = Join-Path $root "backups"
$env:LOG_FILE = Join-Path $root "state\controller.log"   # same messages as the window, kept on disk too
foreach ($line in Get-Content $envFile) {
    $line = $line.Trim()
    if (-not $line -or $line.StartsWith("#")) { continue }
    $key, $value = $line -split "=", 2
    Set-Item -Path "env:$($key.Trim())" -Value $value.Trim()
}

# Don't start a second copy (e.g. if the automatic-start version is already running)
$panelPort = if ($env:PANEL_PORT) { [int]$env:PANEL_PORT } else { 8080 }
if (Get-NetTCPConnection -LocalPort $panelPort -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "The controller is already running, nothing to do."
    return
}

$playit = $null
$playitCmd = if ($env:PLAYIT_EXE) { Get-Command $env:PLAYIT_EXE -ErrorAction SilentlyContinue }
# Only the real agent counts: playit.exe (terminal agent) or playitd (Windows service/daemon). The tray helper
# (playitd-tray.exe) is not an agent, so it must not stop us from starting one.
$agentRunning = (Get-Process -Name "playit", "playitd" -ErrorAction SilentlyContinue) -or
    ((Get-Service -Name "playitd" -ErrorAction SilentlyContinue).Status -eq "Running")
if ($playitCmd -and -not $agentRunning) {
    $playit = Start-Process $playitCmd.Source -PassThru -WindowStyle Hidden
    Write-Host "playit.gg agent started."
} elseif ($playitCmd) {
    Write-Host "playit.gg agent is already running, leaving it alone."
}

try {
    Set-Location (Join-Path $root "controller")
    & $python -m app.main
}
finally {
    if ($playit -and -not $playit.HasExited) { Stop-Process $playit -Force -ErrorAction SilentlyContinue }
}
