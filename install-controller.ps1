# Installs the controller's Python dependencies into controller\.venv.
#   .\install-controller.ps1            install / update dependencies
#   .\install-controller.ps1 -Startup   also start the controller automatically when you log in to Windows
#   .\install-controller.ps1 -Remove    remove the automatic start again
param([switch]$Startup, [switch]$Remove)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$taskName = "SMP Controller"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Automatic start removed."
    return
}

$venv = Join-Path $root "controller\.venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python 3.10+ not found. Install it from python.org first." }
    Write-Host "Creating Python environment..."
    & python -m venv $venv
}
Write-Host "Installing dependencies (aiohttp, discord.py)..."
& (Join-Path $venv "Scripts\python.exe") -m pip install --quiet --upgrade pip
& (Join-Path $venv "Scripts\python.exe") -m pip install --quiet -r (Join-Path $root "controller\requirements.txt")

if ($Startup) {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $root `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$(Join-Path $root 'run-controller.ps1')`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
        -Description "Aternos-style Minecraft controller" -Force | Out-Null
    Write-Host "The controller will now start automatically when you log in. Start it right now with:"
    Write-Host "  Start-ScheduledTask -TaskName '$taskName'"
}
Write-Host "`nInstalled. To run it in this window for a first test: .\run-controller.ps1"
