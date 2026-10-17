# Taking turns hosting (one world, several PCs)

Friends can host the same world from their own PCs, one at a time. Whoever starts their controller takes a lock,
downloads the latest world, runs the server, tunnel and Discord bot, uploads the world each time the server falls asleep,
and gives the lock back on a hand over. The world lives in free cloud storage (Backblaze B2), and the address stays the
same because every PC uses the same playit agent. Only share this with people you trust completely: a host has the
world, the players' passwords and the bot token.

## The easy way: the SMP Host app

A normal Windows app (`SMP-Host-Setup.exe`, built with `.\desktop\build.ps1` into `desktop\dist\`) with Java, rclone
and the playit agent inside. Like GitHub Desktop for the world: it shows the shared world (version, who uploaded it, who
hosts now) and this PC, **Start hosting** pulls the latest world and hosts, **Stop hosting** uploads and hands over.
It has a Minecraft look, and the server's own **name, description and picture** are shown at the top.

**Once, the owner:**
1. Backblaze website: an account, a **private bucket**, and an application key **per person** (you too): that bucket
   only, **Read and Write**. See the steps under "Without the app" below.
2. Put your world in the bucket (close the old Start SMP window first): `.\setup-sharing.ps1`, your key, answer `y`.
3. Make your own invite: `.\make-invite.ps1` with your key (it's copied to the clipboard).
4. Install `SMP-Host-Setup.exe`, paste the invite, tick the EULA box, **Set up**. From now on use the app instead of
   Start SMP (never both: they'd share the same ports).

**Each friend:** in the app click **Invite friend**, paste the key you made for them, and send them the code and
`SMP-Host-Setup.exe` **privately**. They install it, paste the code, and click **Start hosting** when it's their turn.

Good to know: Windows may say "Windows protected your PC" for the **installer** the first time, because it isn't code
signed: **More info -> Run anyway**. The app itself has no custom `.exe`: its shortcut starts a bundled, digitally signed
`pythonw.exe` on plain Python files. (A program frozen into one `.exe` with PyInstaller got flagged by Windows Defender
as `Trojan:Win32/Bearfoos.A!ml`, a machine-learning false alarm that hits many such programs, so this build avoids it.)
The separate playit app must not run at the same time (the app runs its own agent and warns you). The world, settings
and backups live in `%LOCALAPPDATA%\SMP Host` and stay there if the app is uninstalled.

## Server name, description and picture

Click **Server info** in the app. Change the **name**, an optional one-line **description**, and the **picture** (any PNG,
JPG, GIF, BMP or WEBP: it is cropped to a square and made 64x64, the size Minecraft shows next to the name). A preview
shows how it looks in the Minecraft server list. This is what players see:
- **While the server sleeps:** the controller answers the server list itself, with your picture, the name in bold and
  "Asleep - (your description)".
- **While it is awake:** the real server shows the same name, description and picture (they are written into
  `server.properties` and `server-icon.png` each time it wakes).

It is **shared with everyone who hosts**: saving uploads the three to the shared storage, and every host picks up the
newest at its next start (the app also checks while it is open). The name is plain text up to 32 characters, the
description up to 60. Nothing about the server's name is built into the app.

## Without the app (scripts)

**The owner, on the [Backblaze B2](https://www.backblaze.com/sign-up/b2-cloud-storage-backup-archive) website** (10 GB
free, and the sign-up page says no credit card; the world is about 0.4 GB):
1. Sign up.
2. **Buckets -> Create a Bucket**: a unique name like `my-world-4821`, **Private**.
3. **Application Keys -> Add a New Application Key**, once for each person: access to **that bucket only**, **Read and
   Write**. Copy its keyID and applicationKey right away (shown only once). Send each friend their own key **privately**.

**Then on each PC, the owner first,** in PowerShell in the `minecraft_server` folder (close the controller first):
```powershell
git pull
.\setup-sharing.ps1
```
Paste the bucket name, that person's keyID and applicationKey, and a name for the PC (different on every PC). The script
installs rclone, saves the settings, and on the owner's PC uploads the world (answer `y`; only the owner, only once).
Then start the controller again (your Start shortcut, or `.\run-controller.ps1`). That's all; no Google Drive anywhere.

A brand new co-host PC first needs the normal install (`setup.ps1`, `install-controller.ps1`), a copy of the owner's
`controller.env` and the playit identity file: see "The co-host's PC" and "The same address (playit)" further down.

## Every day

- **Host:** double-click your Start shortcut (or `.\run-controller.ps1`). If someone else is hosting, it says so and closes.
- **Switch hosts:** **Hand over hosting** in the panel, or `/jarvis handover` in Discord (refused while anyone plays).
  Then the other person starts theirs.
- **Who is hosting?** `/jarvis status`, or `.\shared-world.ps1 status`.
- **Don't close the window with the X while hosting.** If it happens anyway, start it again on the same PC and it carries on.

## What keeps the world safe (automatic)

- The lock is seen by the other PC at once, so two people can never host at the same moment.
- Only changed files are uploaded or downloaded, in parallel.
- An upload is refused if this PC's world looks wrong (fewer player files than the shared one, no `level.dat`, or half
  the files missing), so a half-finished download or a fresh default world can never replace the real one.
- A download or upload that was cut off is detected and redone.
- Every upload keeps what it replaced for 7 days. To undo a bad upload: stop the controller, list them with
  `rclone lsf smp:<bucket>/history/` (named by time, UTC), copy one back with
  `rclone copy smp:<bucket>/history/<time> smp:<bucket>/data`, and start once with `.\run-controller.ps1 -Takeover`.
- Each PC also keeps its own zip backups in `backups\`.

Other storage works too (anything rclone supports): set it up with `rclone config`, then run
`.\setup-sharing.ps1 -Remote name:folder`. Optional settings in `controller.env`: `SHARED_HISTORY_DAYS` (7; 0 = off) and
`SHARED_SKIP_CHECKS=1` (upload even when the safety check refuses; only if you really removed players or files).

---

## The older way: a Google Drive folder (not recommended)

It works with a folder synced by Google Drive for desktop (`SHARED_DIR`) instead, but Drive syncs late (the lock too)
and adds `desktop.ini` files everywhere. Everything below about the co-host's PC, playit and voice chat applies to both ways.

## One-time setup

**1. Shared folder (the owner).** Install [Google Drive for desktop](https://www.google.com/drive/download/). Create a
folder, e.g. `my-world`, and **share it with your co-host as Editor**.

**2. Shared folder (the co-host).** Install Drive for desktop, open the invitation, and choose **Add shortcut to My
Drive**. In Drive's settings, set that folder to **Available offline** (or use "Mirror files"), otherwise the files may
stay online-only and the controller can't read them.

**3. The co-host's PC.** Windows, Java 25, Python 3.10+, about 8 GB of spare RAM:
```powershell
git clone https://github.com/c4rlosst/minecraft_server.git
cd minecraft_server
.\setup.ps1                 # downloads Fabric and the mods, asks you to accept the Minecraft EULA
.\install-controller.ps1
```

**4. Settings (both PCs).** In `controller.env` set the path to the shared folder **as it appears on that PC** and your
own name:
```
SHARED_DIR=G:\My Drive\my-world
HOST_NAME=YourName
```
The co-host also needs the owner's `controller.env` first (it holds the RCON and panel passwords and the Discord token),
sent **privately**, never through GitHub or Discord channels. Copy it over, then change `SHARED_DIR` and `HOST_NAME` to the
co-host's own values.

**5. The same address (playit).** The co-host needs only the playit agent plus **your agent's identity file**. No new
account, no claim code, no new tunnel:
1. Install playit on the co-host's PC: `winget install DevelopedMethods.playit`.
2. Copy `C:\ProgramData\playit_gg\playit.toml` from the owner's PC and put it at the **same path** on the co-host's PC
   (replace theirs). It's the agent's secret key, so send it privately. Copy **only** that file, not `installed_user.sid`
   (that one belongs to whichever Windows user installed playit).
3. Keep `PLAYIT_EXE=playit` in `controller.env` (the default).

Both PCs now are the *same* agent, so **only one may run it at a time**. The controller takes care of that: it starts the
agent only **after** it has taken the hosting lock, and stops it when it quits (`playit stop`), including after a handover.
If the stop ever fails (for example a permissions error shown in the controller log), run `playit stop` yourself, or
`Stop-Service playitd` from an administrator PowerShell. Don't set the playit service to start with Windows on either PC
(it's "Manual" by default). Check who's running it any time with `playit status`.

**6. Voice chat (optional).** Voice goes straight to the host's home internet, so it only works when that host has
**UDP 24454 forwarded** on their router and allowed in Windows Firewall (see the README, "Voice chat"). Then set
`VOICE_HOST=auto` in their `controller.env`: the controller writes that PC's public IP into the voice chat settings each time
the server starts. Without it, use a Discord voice channel while that person hosts.

**7. The owner hosts first.** The shared folder starts empty, and the first host's world becomes the shared world once
its server falls asleep. If the co-host started first, they'd create a brand-new empty world and upload that instead. (Before
any download replaces a PC's local world, the controller saves a zip of it in `backups\`.)

## Every day

- **Start hosting:** double-click your Start shortcut (or `.\run-controller.ps1`). If the other person is hosting, it
  says so and quits. When it starts, it waits until Drive has fully synced, takes the lock and downloads the world. Discord
  announces "<name>'s PC is now hosting".
- **While hosting:** it works like before: join to wake the server, it sleeps after 10 empty minutes and then uploads the
  world. `/jarvis status` shows whose PC is hosting.
- **Stop hosting:**
  - Easiest: **Hand over hosting** in the panel, or `/jarvis handover` in Discord (server admins). It refuses while anyone
    is playing. It stops the server, uploads the world, releases the lock and closes the controller.
  - Or press **Ctrl+C** in the controller window: it stops the server (kicking anyone online), uploads and releases.
  - **Don't just close the window with the X.** The lock stays held. See "If a PC crashes" below.
- **Before switching off the PC:** let Google Drive finish syncing (its tray icon shows "synced"). The next host's
  controller waits for the files to arrive and won't start on a half-synced world.

## If something goes wrong

- **"X is already hosting"** → that person's controller is running. Ask them to hand over.
- **"The shared folder doesn't match the last upload yet"** → Drive is still syncing. Wait and start again.
- **Tunnel doesn't work after a handover:** the previous host's playit may still be running. On that PC run `playit stop`.
- **Your own PC crashed or its window was closed with the X:** just start it again. A PC **resumes its own lock** (it's
  recognised by its `HOST_NAME`) and keeps its local world, which is newer than the last upload. This is why every host
  needs a **different `HOST_NAME`**.
- **Another PC crashed or the window was closed with the X:** the lock stays held (and playit may keep running there). After **5 minutes** without a heartbeat the other
  host can start with `.\run-controller.ps1 -Takeover`. They get the **last uploaded world**; anything played since the
  last time the server fell asleep on the crashed PC is lost. A host who is still alive can never be taken over.
- **Both PCs' clocks must be right** (Windows: Settings → Time → Sync now), because the heartbeat compares times.
- **Two people start at the very same moment:** one of them loses and sees an error. Just try again.

## Limits to keep in mind

- Only the host's PC needs to be on. If nobody is hosting, the address doesn't work and the bot is offline.
- Each hand-off uploads only changed files, so it's quick after the first one.
- Google Drive free storage is 15 GB: the world (about 60 MB now), mods and backups on each PC are separate, the shared copy is
  one copy.
- If Drive creates a "conflict" copy of `state.json`, delete the extra `state (...)` files and start again.
