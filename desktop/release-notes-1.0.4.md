## SMP Host 1.0.4 (Windows)

**Everyone should update, but not while hosting.** If you are hosting right now, press **Stop hosting** and wait for "Done" first. Then close SMP Host (also from the tray), run the new `SMP-Host-Setup.exe` over the old install, and open it again. Your invite, settings and world stay as they are.

**Download:** `SMP-Host-Setup.exe` below (76 MB).

### Fixed: a session could be lost
A host whose final upload failed (for example when the storage was out of requests) used to give the hosting lock back anyway. The next start then downloaded the older shared world over that PC's own world, and the session's progress was gone from both places. Now:
- A failed upload at shutdown **keeps the lock**. Start hosting again on the same PC: it resumes with its own, newer world, and **Stop hosting** uploads it once the storage works.
- If a PC's lock has gone silent (crash, power loss) and another PC wants to host, the app now offers **Take over hosting**, with a warning that whatever the first PC never uploaded is lost. Ask them first.

### New: History
The **History** button (top right) shows:
- **Who hosted, from what time to what time, and for how long**, newest first, with how each session ended ("no stop recorded" means the PC crashed or was switched off without stopping; the end time is the last time it was seen alive). It starts recording with this version, so older sessions don't appear.
- **The backups on this PC**, matched to those sessions ("as X's session started: the world BEFORE it", "as X's session ended: includes it", "during X's session"), and **Restore selected backup** (or **Pick another file...**) to put a backup back as the world. Close hosting first. The current world is kept in a folder next to it, nothing is deleted. Then press **Start hosting** (it keeps the restored world) and **Stop hosting** to upload it for everyone; what the shared world had is kept in the upload history for a few days.

Which backup to pick: a backup holds the world as it was when it was made. To get a session back, pick one made at the end of it or after it, not the one made when it started.

### Also
- Cloudflare R2 (optional, from 1.0.3): see `HOSTING.md`. Backblaze stays the default.
- The web panel (while hosting), the New invite button and the request reductions from 1.0.2 and 1.0.3 are all still there.

### Notes
- Windows 10/11, 64-bit. About 8 GB of free RAM for the Minecraft server while hosting.
- Windows may say "Windows protected your PC" because the installer is not code-signed: click **More info**, then **Run anyway**.
- You need an **invite code** (starts with `SMP1.`) from whoever runs the server. Never share it publicly.

SHA-256 of `SMP-Host-Setup.exe`: `88A53230ED915D3D7FE2F7A18C764D2ABEC11E8861AECDB69BBBD4EF969417CF`

Bundled third-party software: Eclipse Temurin JRE (GPLv2 + Classpath Exception), rclone (MIT), playit.gg agent (BSD-2-Clause), Python (PSF License), Monocraft font (SIL OFL 1.1).
