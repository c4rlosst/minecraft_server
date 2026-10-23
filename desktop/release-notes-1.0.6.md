## SMP Host 1.0.6 (Windows)

**Everyone should update, but not while hosting.** Press **Stop hosting** and wait for "Done" first. Then close SMP Host (also from the tray), run the new `SMP-Host-Setup.exe` over the old install, and open it again. Your settings and PC name stay.

**The shared world has moved to Cloudflare R2** (it has no small daily request limit). After you update, click **New invite** (top right) and paste the new invite the server owner sends you. The old Backblaze storage is marked "moved", so an old invite can't be used to host by accident.

**Download:** `SMP-Host-Setup.exe` below (76 MB).

### Progress can no longer be lost the way it was
Several ways a session's work could disappear are closed:
- **Changed files are never skipped.** Uploads and downloads now compare what is *inside* the files, not their size and modified time. Windows can leave a world file's modified time unchanged after Minecraft rewrote it, and the old comparison then skipped the change silently, so the shared world quietly missed parts of a session.
- **An old copy can't overwrite a newer world.** Every PC remembers which upload its world is based on. A PC whose copy is older downloads the newest world first (its own copy is kept as a backup), and an upload from an older base is refused.
- **Checkpoints.** While the server runs and people are playing, the world is saved to the shared storage every 20 minutes (it pauses the server's own saving for a few seconds, copies what changed, turns saving back on, then uploads slowly so the players' connection isn't used up). If a PC crashes, the next host loses at most the last 20 minutes. `CHECKPOINT_MINUTES` in `settings.env` changes it (0 = off).
- **Checked downloads.** After the world is downloaded it is compared with the storage, and anything missing or cut short is fetched again. A world that still doesn't match is not hosted.
- **A refused download is not "a new world".** When the storage refuses a download (a free plan's daily limit), the app now says so, instead of treating the lock file as missing.
- When a host goes silent, the next person is offered **Take over hosting**, with the age of the newest saved copy.

### A new look
- **A plain progress bar** (black outline, solid green fill, the percent above it) that fills smoothly from 0 to 100. One bar covers the whole start (taking the lock, downloading the world, starting the tunnel and the bot) and the whole stop (saving, uploading, checking, handing over). A step with no numbers yet creeps forward.
- **Nothing overlaps.** The panels and dialogs are laid out on a grid. Long names end in "...", the status of this PC is a coloured chip, and errors are short plain sentences in the *Shared World* box (the full text goes to the log).
- **Copy log** copies the log to send when asking for help, an empty log says what it is for, and the version number is shown in the corner.
- A green **Update** button appears when a newer SMP Host is out (it only opens the download page; `CHECK_UPDATES=0` in `settings.env` turns the check off).

### Faster and steadier
- Starting waits 8 seconds for the lock instead of 20 (the first heartbeat double-checks it).
- When the server stops, the backup zip and the upload run at the same time.
- The window does less work while nothing is going on, and a background check that finishes on the setup screen no longer causes an error.
- Hosting refuses to start with less than 2 GB of free disk space, and the crash message gives a plain sentence for known problems.

### For the server owner
- **History** shows who hosted when and for how long, with the backups matched to those sessions, and can restore one. A restore also accepts a zip of the `world` folder (a Google Drive download), and `python -m app.desktop.restore --zip <file> --upload` restores and uploads in one go.
- `seed-r2.ps1` starts a new Cloudflare R2 bucket from a world that is already on the PC, and `move-to-r2.ps1` copies the shared world over from the old storage. See `HOSTING.md`.

### Notes
- Windows 10/11, 64-bit. About 8 GB of free RAM for the Minecraft server while hosting.
- Windows may say "Windows protected your PC" because the installer is not code-signed: click **More info**, then **Run anyway**.
- You need an **invite code** (starts with `SMP1.`) from whoever runs the server. Never share it publicly: it holds the server's passwords and keys.

SHA-256 of `SMP-Host-Setup.exe`: `F47A7189F050FF7B70DC8E2AAE67FD702432D74D653A653CB41C1D2B600D7207`

Bundled third-party software: Eclipse Temurin JRE (GPLv2 + Classpath Exception), rclone (MIT), playit.gg agent (BSD-2-Clause), Python (PSF License), Monocraft font (SIL OFL 1.1).
