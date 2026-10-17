# Taking turns hosting (two trusted people, one world)

Two people can host the same world from their own PCs, one at a time, through a shared **Google Drive** folder.
Whoever starts their controller **takes a lock**, downloads the latest world, runs the server (and the tunnel and Discord
bot), uploads the world every time the server falls asleep, and gives the lock back when they hand over.
The address stays the same, because both PCs use the same playit agent.

```
shared Drive folder:   state.json  (who is hosting)      data/  (world, mods, config, EasyAuth passwords, ops/bans)
```

Only share this with someone you trust completely: a host has full access to the world, the players' passwords and the
bot token.

## Recommended: rclone with Backblaze B2 (no Google Drive at all)

A synced Drive folder is slow and unreliable for this: the desktop app syncs in the background, so one PC can see the
other's changes (the lock too) late, and it litters folders with `desktop.ini`. With **rclone** the controller talks to
cloud storage directly, so:
- the lock is seen by the other PC **at once**, and nobody has to wait for "Up to date";
- only the files that changed are uploaded or downloaded, in parallel;
- every upload keeps what it replaced for 7 days in `history/`, so a bad upload can be undone;
- an upload is refused if this PC's world looks wrong (fewer player files than the shared one, no `level.dat`, or half
  the files missing), so a half-finished download or a brand new default world can't replace the real one.

[Backblaze B2](https://www.backblaze.com/sign-up/b2-cloud-storage-backup-archive) gives 10 GB free and says no credit card
is needed (check the sign-up page). The world is about 0.4 GB.

**Once (the owner):**
1. Create a B2 account, then **Buckets -> Create a Bucket**: a unique name such as `japax-world-4821`, **Private**,
   encryption and object lock off, and (if offered) the lifecycle setting **"Keep only the last version of the file"**.
2. **Application Keys -> Add a New Application Key**: one key per person (e.g. `owner-pc`, later `friend-pc`), access to
   **that one bucket only**, **Read and Write**. Copy the keyID and applicationKey right away (shown once). A key can be
   revoked any time without affecting the others, and it can't touch anything but that bucket.

**On each PC** (the owner's and the co-host's), in PowerShell:
```powershell
winget install Rclone.Rclone
```
Open a **new** PowerShell, then create the remote with that person's own key (it stays on that PC, in
`%APPDATA%\rclone\rclone.conf`; never post it anywhere):
```powershell
rclone config create jx b2 account=YOUR_KEY_ID key=YOUR_APPLICATION_KEY
rclone lsd jx:
```
The second line should list the bucket. Then in `controller.env` (leave `SHARED_DIR` empty; `HOST_NAME` stays unique per PC):
```
SHARED_REMOTE=jx:japax-world-4821
```
**The very first time,** put the owner's world into the bucket (close the controller first):
```powershell
.\shared-world.ps1 upload
.\shared-world.ps1 status       # who is hosting, which upload is stored, and whether it matches the record
```
After that the controller works exactly like before: taking turns, hand over, the playit tunnel. No Google Drive app is
needed on any PC.

**Other storage:** Google Drive through rclone (`rclone config create jx drive scope=drive root_folder_id=<FOLDER_ID>`, sign
in once in the browser, `SHARED_REMOTE=jx:`), Cloudflare R2 (its docs describe a checkout step, so it may want a card), and
anything else rclone supports: run `rclone config`, then point `SHARED_REMOTE` at `remote-name:bucket-or-folder`. Settings (all
optional): `SHARED_HISTORY_DAYS=7` (0 turns the history off), `RCLONE_EXE`, and `SHARED_SKIP_CHECKS=1` to upload even when the
safety check refuses (only if you really did remove players or files).

Undo a bad upload: stop the controller, find the upload in `rclone lsf jx:history/` (the folder name is its time, UTC),
copy it back with `rclone copy jx:history/<time> jx:data`, and start once with `.\run-controller.ps1 -Takeover` (the
record of the last upload no longer matches, and the next upload fixes it).

The rest of this page describes the older way, a Drive-synced folder; the steps for taking turns are the same in both.

## One-time setup

**1. Shared folder (the owner).** Install [Google Drive for desktop](https://www.google.com/drive/download/). Create a
folder, e.g. `JAPAX-world`, and **share it with your co-host as Editor**.

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
SHARED_DIR=G:\My Drive\JAPAX-world
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

- **Start hosting:** double-click **Start JAPAX x RENE** (or `.\run-controller.ps1`). If the other person is hosting, it
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
