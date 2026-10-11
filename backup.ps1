# Zips the world into backups\ and keeps the newest N. Safest while the server is stopped;
# while running, a copy may catch a chunk mid-save, so run "save-all" in the console first.
param([int]$Keep = 14)

$server = Join-Path $PSScriptRoot "server"
$world = Join-Path $server "world"
$backups = Join-Path $PSScriptRoot "backups"
if (-not (Test-Path $world)) { throw "No world folder yet - start the server once first." }
New-Item -ItemType Directory -Force $backups | Out-Null

$tmp = Join-Path $env:TEMP "smp-backup-$(Get-Date -Format yyyyMMddHHmmss)"
robocopy $world $tmp /E /R:1 /W:1 /NFL /NDL /NJH /NJS | Out-Null   # robocopy exit codes <8 are success
$zip = Join-Path $backups "world-$(Get-Date -Format 'yyyy-MM-dd_HH-mm').zip"
Compress-Archive -Path "$tmp\*" -DestinationPath $zip
Remove-Item $tmp -Recurse -Force

Get-ChildItem $backups -Filter "world-*.zip" | Sort-Object LastWriteTime -Descending | Select-Object -Skip $Keep | Remove-Item
Write-Host "Backup saved: $zip"
