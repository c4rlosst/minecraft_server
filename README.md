# Friends SMP (Fabric, Java Edition)

Self-hosted modded Minecraft server for this Windows PC.

> **Want an Aternos-style server** (sleeps when empty, wakes when a friend joins, web panel + Discord bot)?
> See [cloud/README.md](cloud/README.md). The rest of this file is the Windows, run-it-on-a-PC setup.

## Quick start

```powershell
.\setup.ps1     # downloads Fabric + mods from mods.txt, asks you to accept the EULA
.\start.ps1     # runs the server (auto-restarts on crash)
```

In the server console:

```
whitelist add FriendName
op YourName
```

Everyone joins with the **same Minecraft version** (default `26.3`) and the Fabric loader. Easiest way for friends:
install [Prism Launcher](https://prismlauncher.org) -> new instance -> Fabric, then drop any gameplay mods into it.

## Letting friends connect (pick one)

| Option | Cost | Notes |
|---|---|---|
| **Port forward 25565 (TCP)** on your router to this PC, share your public IP | Free | Exposes your home IP; public IP can change. |
| **[playit.gg](https://playit.gg) tunnel** | Free | No router access needed, hides your IP. Install their agent, create a Minecraft Java tunnel. |
| **[Tailscale](https://tailscale.com)** | Free | Private; every friend installs it and joins your tailnet, then connects to your Tailscale IP. |

Allow the port through Windows Firewall (run PowerShell as Administrator, once):

```powershell
New-NetFirewallRule -DisplayName "Minecraft SMP" -Direction Inbound -Protocol TCP -LocalPort 25565 -Action Allow
```

## Mods

Add Modrinth slugs to `mods.txt` and re-run `.\setup.ps1`. Slugs with no Fabric build for the current version are
skipped with a warning. New Minecraft versions often lag on mods, so check that your must-have mods exist before
bumping `-McVersion`. Mods that add content must be installed by every player; performance mods (the defaults) are
server-only.

## Any friend can host (shared world)

The world lives in a cloud folder (Google Drive / Dropbox / OneDrive) shared with everyone who may host. Only one
person can host at a time; `host.ps1` handles the handoff.

**One-time, on each host PC**
1. Install the cloud app and sync the shared folder to disk ("Available offline" / "Mirror files").
2. Clone this repo, install Java, run `.\setup.ps1`.
3. Copy `host.config.example.ps1` to `host.config.ps1` and set `$SharedDir` and `$HostName`.
4. Same address for everyone (playit.gg). Do this **once, by one person**: make a free playit.gg account, install the
   agent (`winget install DevelopedMethods.playit`), create an agent + a **Minecraft Java** tunnel pointing at local
   port 25565, and note the tunnel address friends will use. Then give every host the agent's **secret key**
   privately (never in git or the shared cloud folder) and put it in `$PlayitSecret` in `host.config.ps1`.
   `host.ps1` then starts the agent with that key (`SECRET_KEY`), so the address is identical whoever hosts.
   The agent must only run on one PC at a time, which the host lock already guarantees.
   Note: playit's docs show `SECRET_KEY` only for their Docker image. If a host's agent asks you to sign in or
   claim a new agent instead, tell me and I'll adjust to what your version needs.

**Each session**
```powershell
.\host.ps1 -Status   # who's hosting right now?
.\host.ps1           # take the lock, pull the world, start server + tunnel
```
Type `stop` in the console when done. The script then uploads the world and frees the lock. Wait until your cloud
app shows "synced" before shutting down the PC, and don't close the window early.

**Safety rules**
- The shared folder holds the world, `mods/`, `config/`, whitelist/ops/bans and `server.properties`, so every host
  runs identical content. Change mods or settings on whichever PC is hosting; they propagate on the next handoff.
- The lock is cooperative (cloud sync isn't instant): say "hosting now" in your group chat anyway.
- The next host's script waits until its synced copy matches the last upload, so it never starts on a half-synced world.
- Crashed PC? After 5 min without a heartbeat, `.\host.ps1 -Force` lets someone else take over using the last
  uploaded world. Anything that PC hadn't uploaded is lost, so start the crashed PC again if you can.
- A local backup is taken before every pull. Also run `.\backup.ps1` once in a while.

## Keeping it healthy

- `.\backup.ps1` zips the world into `backups\` (keeps the last 14). Schedule it with Task Scheduler.
- `.\start.ps1 -Ram 8G` changes memory (default 6G; leave a few GB for Windows).
- Server settings live in `server\server.properties`; your edits survive re-running setup.
- The PC must stay on (and not sleep) while people play. A cheap VPS later is just: copy this folder, run setup, start.
