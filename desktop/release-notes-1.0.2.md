## SMP Host 1.0.2 (Windows)

**Everyone must update, and close the old app first.** Version 1.0.0 asks Backblaze for the lock far too often and uses up the free daily allowance for the whole group ("transaction cap exceeded"). An old copy left open keeps doing it. Close SMP Host (also from the tray), run the new `SMP-Host-Setup.exe` over the old install, and open it again. Your invite, settings and world stay as they are.

**Download:** `SMP-Host-Setup.exe` below (76 MB).

### If you see "transaction cap exceeded" / "free daily limit"
Backblaze's free plan has a daily allowance that resets at **00:00 UTC = 8:00 AM Philippine time**. Until then nothing can reach the shared storage, from any PC. Nothing is lost: the world and the lock are safe in the bucket. Wait for the reset (or raise the limit in Backblaze under Caps & Alerts), and make sure nobody still runs 1.0.0.

### New
- **Web panel, themed and without a password.** The **Web panel** button opens straight in the browser, in the same Minecraft style as the app, with the server picture, a Copy button for the address, the whitelist, mods (upload and remove) and the console log. It only opens on the PC that is hosting, and other websites cannot use it.

### Fewer requests (so the free plan lasts)
- The window checks the shared world every 10 minutes while idle and every 30 minutes while hosting (before: 2 and 5), never while hidden or minimized, and pauses for 30 minutes after a limit error. **Refresh** still checks right away.
- The hosting heartbeat is every 2 minutes (before: 1).

### From 1.0.1
- Windows warns before shutting down while you are hosting, and the app offers to stop hosting first so the world is uploaded. If someone shuts down anyway, press **Start hosting** again on the same PC: it resumes your own session and keeps your local world. Do not let someone else start before you.
- Clear message when the free daily limit is used up.

### Mods
Mods travel with the world: the host's `mods` and `config` folders are uploaded on **Stop hosting** and downloaded by the next host on **Start hosting**. To add a mod, use the web panel (or the mods folder via **Open folder**) on the PC that is hosting, then start and stop hosting once.

### Notes
- Windows 10/11, 64-bit. About 8 GB of free RAM for the Minecraft server while hosting.
- Windows may say "Windows protected your PC" because the installer is not code-signed: click **More info**, then **Run anyway**.
- You need an **invite code** (starts with `SMP1.`) from whoever runs the server. Never share it publicly.

SHA-256 of `SMP-Host-Setup.exe`: `CBF6CC75F04025BEB453808586EC593FEA7CB0E83CAAC475D01E66E573A23875`

Bundled third-party software: Eclipse Temurin JRE (GPLv2 + Classpath Exception), rclone (MIT), playit.gg agent (BSD-2-Clause), Python (PSF License), Monocraft font (SIL OFL 1.1).
