## SMP Host 1.0.5 (Windows)

Update when nobody on your PC is hosting: press **Stop hosting** and wait for "Done" first, then close SMP Host (also from the tray), run the new `SMP-Host-Setup.exe` over the old install, and open it again. Your invite, settings and world stay as they are.

**Download:** `SMP-Host-Setup.exe` below (76 MB).

### New: an experience bar for loading and saving
While the app takes the lock, downloads the world, starts the server, saves, uploads and hands over, a Minecraft XP bar fills in the **This PC** panel. The big green number is the percent, with how much has been transferred and how many files underneath. It uses the real numbers of the transfer, so it moves as the world moves. When the size isn't known yet, a few segments sweep across instead. It also shows for the automatic upload after the server falls asleep.

### New: every click answers
- **Refresh** shows "Checking...", then says what it found: up to date, a new version and who uploaded it, or who is hosting now. The shared-world box shows when it last checked.
- **Greyed-out buttons** explain why when you click them (for example "You're not hosting right now, so there is nothing to stop", or "The web panel opens while this PC is hosting").
- **Copy** says "Copied!" and tells you where to paste the address; **Open folder** says what it opened.
- **Start hosting** and **Stop hosting** respond at once with a message and the bar.
- Buttons dip when pressed and act when you let go; letting go somewhere else cancels the click.

### Also
- **Restore a backup from a zip of the world folder** (for example one downloaded from Google Drive), not only the app's own backups. `session.lock` is skipped.
- Everything from 1.0.4: History (who hosted and when, backups matched to those sessions, restore), a failed upload at shutdown keeps the lock, and the offer to take over a silent host's lock.

### Notes
- Windows 10/11, 64-bit. About 8 GB of free RAM for the Minecraft server while hosting.
- Windows may say "Windows protected your PC" because the installer is not code-signed: click **More info**, then **Run anyway**.
- You need an **invite code** (starts with `SMP1.`) from whoever runs the server. Never share it publicly.

SHA-256 of `SMP-Host-Setup.exe`: `27078294E3E448EA76195AA24A7CD19BE3A25BB6A1A4D701BD230967CFF10EBA`

Bundled third-party software: Eclipse Temurin JRE (GPLv2 + Classpath Exception), rclone (MIT), playit.gg agent (BSD-2-Clause), Python (PSF License), Monocraft font (SIL OFL 1.1).
