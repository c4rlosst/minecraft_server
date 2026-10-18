## SMP Host 1.0.3 (Windows)

Close SMP Host first (also from the tray), then run the new `SMP-Host-Setup.exe` over the old install. Your invite, settings and world stay as they are. **Nothing else changes: this version keeps using Backblaze unless the server owner moves the world to Cloudflare R2 (below).**

**Download:** `SMP-Host-Setup.exe` below (76 MB).

### New
- **Cloudflare R2 storage (optional).** The app can now use Cloudflare R2 instead of Backblaze B2. R2's free plan has no small daily request limit, so the "transaction cap exceeded" problem goes away. The server owner moves the world once with `move-to-r2.ps1` (see `HOSTING.md`); friends then get a new invite. R2 support is new and has been tested with local folders only so far, so Backblaze stays the default until the owner decides to move.
- **New invite button** (top right of the main screen). Set up again from a new invite without reinstalling: use it when the server moves to other storage or a key is replaced. Your PC name carries over. It asks you to stop hosting first.
- **Invite friend** asks for the right key type: a Backblaze keyID and applicationKey, or a Cloudflare Access Key ID and Secret Access Key, depending on what the server uses.

### Still true from 1.0.2
- Web panel button (while hosting): themed like the app, no password, only opens on the hosting PC.
- The app asks the shared storage far less often, so the free Backblaze daily allowance lasts (it resets at 00:00 UTC = 8:00 AM Philippine time).
- Windows warns before shutting down while you are hosting, and the app offers to stop hosting first so the world is uploaded.

### Notes
- Windows 10/11, 64-bit. About 8 GB of free RAM for the Minecraft server while hosting.
- Windows may say "Windows protected your PC" because the installer is not code-signed: click **More info**, then **Run anyway**.
- You need an **invite code** (starts with `SMP1.`) from whoever runs the server. Never share it publicly.

SHA-256 of `SMP-Host-Setup.exe`: `7DEB77E81F77284D4C3F1532622894FB878E584B1436038EC888459FF10507EA`

Bundled third-party software: Eclipse Temurin JRE (GPLv2 + Classpath Exception), rclone (MIT), playit.gg agent (BSD-2-Clause), Python (PSF License), Monocraft font (SIL OFL 1.1).
