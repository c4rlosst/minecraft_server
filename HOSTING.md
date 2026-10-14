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

**5. The same address (playit).** Install playit on the co-host's PC (`winget install DevelopedMethods.playit`), then copy
`C:\ProgramData\playit_gg\playit.toml` from the owner's PC to the **same path** on the co-host's PC. That file is the
agent's secret, so send it privately. This makes both PCs the same agent, so only **one PC may run it at a time**: the lock
makes sure the controller only starts it for the host. Make sure the playit *service* isn't set to start by itself on the
PC that isn't hosting (`Stop-Service playitd`).

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
- **A PC crashed or the window was closed with the X:** the lock stays held. After **5 minutes** without a heartbeat the other
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
