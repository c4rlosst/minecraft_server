## SMP Host 1.0.1 (Windows)

**Everyone should update.** Run the new `SMP-Host-Setup.exe` over the old install. Your settings, invite and world stay as they are.

**Download:** `SMP-Host-Setup.exe` below (76 MB).

### When can we download and upload?
Any time. There is no schedule: the world downloads when you press **Start hosting** and uploads when you press **Stop hosting**.

The only limit is the free Backblaze plan's **daily allowance of requests**. It resets every day at **00:00 UTC = 8:00 AM Philippine time**. If it is ever used up, the app now says so in plain words and shows the reset time in your own time zone, instead of a cryptic error. Your world is never lost when this happens.

### Fixed
- **"Class B transactions cap reached" on Backblaze.** The app read the shared lock far too often. Hosting now re-reads it only every 10th beat (and after the PC wakes from sleep), and the window checks every 2 minutes idle / 5 minutes hosting, and not at all while hidden or minimized. Refresh still does a full check.
- **Turning the PC off while hosting.** Windows now warns "SMP Host is preventing shutdown" and the app offers to stop hosting first, so the world is uploaded. If someone shuts down anyway, press **Start hosting** again on the same PC: it resumes your own session and keeps your local world (nothing is lost). Do not let someone else start before you.

### Mods
Mods travel with the world. The host's `mods` and `config` folders (and the EasyAuth logins) are uploaded on **Stop hosting** and downloaded by the next host on **Start hosting**, so everyone always runs the same mods. To add a mod: put it in the mods folder on the PC that is hosting (**Open folder**), start and stop hosting once, and everyone gets it on their next turn. Friends should never copy mods by hand.

### Notes
- Windows 10/11, 64-bit. About 8 GB of free RAM for the Minecraft server while hosting.
- Windows may say "Windows protected your PC" because the installer is not code-signed: click **More info**, then **Run anyway**.
- You need an **invite code** (starts with `SMP1.`) from whoever runs the server. Never share it publicly: it holds the server's passwords.
- No custom `.exe` inside: the shortcut runs the bundled, signed python.org `pythonw.exe` on plain Python files.

SHA-256 of `SMP-Host-Setup.exe`: `3B23BE3C769EACBEAF35E3D25A1A96BE8B04C63A30FBBF5F12CBD5F9740239B3`

Bundled third-party software: Eclipse Temurin JRE (GPLv2 + Classpath Exception), rclone (MIT), playit.gg agent (BSD-2-Clause), Python (PSF License), Monocraft font (SIL OFL 1.1).
