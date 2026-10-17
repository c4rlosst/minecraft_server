# Builds desktop\dist\SMP-Host-Setup.exe: the SMP Host app with Java, rclone, the playit agent and its own Python inside,
# so a friend installs one thing and needs nothing else. Run from anywhere:  .\desktop\build.ps1
#   -SkipInstaller   only build the app folder (desktop\dist\SMP Host), no Setup.exe
#
# There is deliberately NO custom .exe in the app: the shortcut starts the bundled python.org pythonw.exe (digitally signed
# by the Python Software Foundation) on plain .py files. Programs frozen into one .exe with PyInstaller are often flagged
# by antivirus heuristics (Windows Defender called one of ours "Trojan:Win32/Bearfoos.A!ml"), so this avoids that.
param([switch]$SkipInstaller)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Windows PowerShell's progress bar makes big downloads crawl
$desk = $PSScriptRoot
$root = Split-Path $desk -Parent
$vendor = Join-Path $desk "vendor"
$tools = Join-Path $vendor "tools"
$runtime = Join-Path $vendor "runtime"
$buildDir = Join-Path $desk "build"
$dist = Join-Path $desk "dist"
$appName = "SMP Host"
$stage = Join-Path $dist $appName
$python = Join-Path $root "controller\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Run .\install-controller.ps1 first." }
New-Item -ItemType Directory -Force $tools, $buildDir, $dist | Out-Null
$headers = @{ "User-Agent" = "smp-host-build/1.0" }

function Invoke-Tool([string]$Exe, [string[]]$Arguments) {
    # Tools log progress to stderr; Windows PowerShell would treat that as an error. Judge by the exit code.
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Exe @Arguments 2>&1 | ForEach-Object { "$_" } | ForEach-Object { Write-Host $_ }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    if ($code -ne 0) { throw "$([IO.Path]::GetFileName($Exe)) failed (exit code $code)" }
}

function Copy-Tree([string]$src, [string]$dst, [string[]]$skipDirs = @(), [string[]]$skipFiles = @()) {
    $args = @($src, $dst, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    if ($skipDirs) { $args += "/XD"; $args += $skipDirs }
    if ($skipFiles) { $args += "/XF"; $args += $skipFiles }
    & robocopy @args | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying $src failed (robocopy exit code $LASTEXITCODE)" }
    $global:LASTEXITCODE = 0
}

function Get-Zip([string]$url, [string]$name) {
    $zip = Join-Path $buildDir $name
    Write-Host "Downloading $url"
    Invoke-WebRequest $url -OutFile $zip -Headers $headers -UseBasicParsing
    $out = Join-Path $buildDir ($name -replace '\.zip$', '')
    if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
    Expand-Archive -LiteralPath $zip -DestinationPath $out
    return $out
}

Write-Host "1/6 Java runtime (Eclipse Temurin 25)"
if (-not (Test-Path (Join-Path $runtime "bin\java.exe"))) {
    $out = Get-Zip "https://api.adoptium.net/v3/binary/latest/25/ga/windows/x64/jre/hotspot/normal/eclipse" "temurin-jre.zip"
    $inner = Get-ChildItem -LiteralPath $out -Directory | Select-Object -First 1
    if (Test-Path -LiteralPath $runtime) { Remove-Item -LiteralPath $runtime -Recurse -Force }
    Move-Item -LiteralPath $inner.FullName -Destination $runtime
}

Write-Host "2/6 rclone and the playit agent"
if (-not (Test-Path (Join-Path $tools "rclone.exe"))) {
    $out = Get-Zip "https://downloads.rclone.org/rclone-current-windows-amd64.zip" "rclone.zip"
    Copy-Item (Get-ChildItem -LiteralPath $out -Recurse -Filter rclone.exe | Select-Object -First 1).FullName (Join-Path $tools "rclone.exe")
}
if (-not (Test-Path (Join-Path $tools "playitd.exe"))) {
    $installed = Join-Path $env:ProgramFiles "playit_gg\bin\playitd.exe"
    if (-not (Test-Path $installed)) { throw "Install playit on this PC once (winget install DevelopedMethods.playit); the build copies its agent." }
    Copy-Item $installed (Join-Path $tools "playitd.exe")
}

Write-Host "3/6 Fresh app folder: Python runtime (the signed python.org one)"
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage | Out-Null
$base = (& $python -c "import sys; print(sys.base_prefix)").Trim()
if (-not (Test-Path (Join-Path $base "pythonw.exe"))) { throw "Couldn't find pythonw.exe in $base" }
$pyStage = Join-Path $stage "python"
Copy-Tree $base $pyStage -skipDirs @("$base\Doc", "$base\include", "$base\libs", "$base\Scripts", "$base\share",
    "$base\Lib\site-packages", "$base\Lib\test", "$base\Lib\idlelib", "$base\Lib\turtledemo", "$base\Lib\ensurepip",
    "__pycache__") -skipFiles @("NEWS.txt")
$site = Join-Path $pyStage "Lib\site-packages"
New-Item -ItemType Directory -Force $site | Out-Null
Write-Host "    installing the app's packages into it"
Invoke-Tool $python @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-warn-script-location",
    "--only-binary", ":all:", "--target", $site, "aiohttp>=3.10,<4", "discord.py>=2.4,<3", "pillow", "pystray")

Write-Host "4/6 The app's own files"
Copy-Tree (Join-Path $root "controller\app") (Join-Path $stage "controller\app") -skipDirs @("__pycache__")
Copy-Item (Join-Path $desk "smp_host.pyw") (Join-Path $stage "smp_host.pyw")
Copy-Tree $tools (Join-Path $stage "tools")
Copy-Tree $runtime (Join-Path $stage "runtime")
$env:PYTHONPATH = Join-Path $root "controller"
Invoke-Tool $python @("-c", "from app.desktop import pixel; pixel.save_icon(r'$buildDir\icon.ico')")      # a grass block
Copy-Item (Join-Path $buildDir "icon.ico") (Join-Path $stage "icon.ico")

Write-Host "5/6 Pre-compiling (so the first start is quick)"
$old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
& (Join-Path $pyStage "python.exe") -m compileall -q -j 0 (Join-Path $pyStage "Lib") (Join-Path $stage "controller\app") 2>&1 |
    Where-Object { "$_" -match "Error|error" } | Select-Object -First 5 | ForEach-Object { Write-Host "    $_" }
$ErrorActionPreference = $old
$sizeMb = (Get-ChildItem -LiteralPath $stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("App folder: {0} ({1:N0} MB)" -f $stage, $sizeMb)

if ($SkipInstaller) { return }
Write-Host "6/6 Installer (Inno Setup)"
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    winget install --id JRSoftware.InnoSetup --exact --silent --scope user --accept-package-agreements --accept-source-agreements | Out-Null
    $iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) { throw "Inno Setup didn't install. Install it (winget install JRSoftware.InnoSetup) and run this again." }
}
Invoke-Tool $iscc @("/Q", (Join-Path $desk "installer.iss"))
Write-Host "Done: $(Join-Path $dist 'SMP-Host-Setup.exe')" -ForegroundColor Green
