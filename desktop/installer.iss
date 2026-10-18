; Inno Setup script for SMP Host (built by build.ps1). Installs for the current user only: no admin rights needed.
; The world, settings and backups live in %LOCALAPPDATA%\SMP Host and are kept when the app is uninstalled.
; The shortcut starts the bundled python.org pythonw.exe (signed) on smp_host.pyw: there is no custom .exe in the app.
#define AppName "SMP Host"
#define AppVersion "1.0.1"

[Setup]
AppId={{6F3C2A9E-4B7D-4E8A-9C21-5D0B7E1A3F44}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=SMP-Host-Setup
SetupIconFile=build\icon.ico
UninstallDisplayIcon={app}\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Files]
Source: "dist\{#AppName}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\smp_host.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\smp_host.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\smp_host.pyw"""; WorkingDir: "{app}"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
