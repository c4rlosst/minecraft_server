# Runs the controller (wake-on-join gateway, sleep timer, web panel, Discord bot) and the playit.gg agent.
# Leave it running. It starts the Minecraft server by itself when a whitelisted friend joins.
$ErrorActionPreference = "Stop"
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
foreach ($line in Get-Content $envFile) {
    $line = $line.Trim()
    if (-not $line -or $line.StartsWith("#")) { continue }
    $key, $value = $line -split "=", 2
    Set-Item -Path "env:$($key.Trim())" -Value $value.Trim()
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
