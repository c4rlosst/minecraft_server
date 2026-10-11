# Starts the server and restarts it automatically after a crash. Type "stop" in the console to shut down.
param([string]$Ram = "6G")

$server = Join-Path $PSScriptRoot "server"
if (-not (Test-Path (Join-Path $server "fabric-server-launch.jar"))) { throw "Run .\setup.ps1 first." }
Set-Location $server

while ($true) {
    & java "-Xms$Ram" "-Xmx$Ram" -XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 -XX:+DisableExplicitGC `
        -jar fabric-server-launch.jar nogui
    if ($LASTEXITCODE -eq 0) { break }
    Write-Warning "Server exited with code $LASTEXITCODE. Restarting in 10s (Ctrl+C to cancel)..."
    Start-Sleep 10
}
