# Friends SMP (Fabric, Java Edition)

Self-hosted modded Minecraft server for this Windows PC.

A modded Fabric Minecraft server that sleeps when nobody is online and wakes when a whitelisted friend joins. A small background program (the controller) owns the public port, starts and stops the real server, keeps the whitelist, takes a backup every time the server sleeps, and talks to your friends through a Discord bot.

> **Want an Aternos-style server** (sleeps when empty, wakes when a friend joins, web panel + Discord bot)?
> See [cloud/README.md](cloud/README.md). The rest of this file is the Windows, run-it-on-a-PC setup.

## Quick start

```powershell
.\setup.ps1     # downloads Fabric + mods from mods.txt, asks you to accept the EULA
.\start.ps1     # runs the server (auto-restarts on crash)
```

In the server console:

```
friends -> playit.gg tunnel -> controller (port 25565) -> Minecraft (hidden, port 25566)
                                  ^        ^
                  Discord bot ----+        +---- web panel (this PC only)
```

The PC must be **on, with Windows sleep turned off**, for the server and bot to work. While the server is asleep it
uses almost nothing.

## Set up

1. **Install the server** (downloads Fabric + the mods in [mods.txt](mods.txt), asks you to accept the
   [Minecraft EULA](https://aka.ms/MinecraftEULA)):
   ```powershell
   .\setup.ps1
   ```
2. **Edit `controller.env`** (created by step 1): set `PUBLIC_ADDRESS` to your playit.gg tunnel address and `OPS` to your
   Minecraft name. Your panel password is in this file too.
3. **Install the controller** (creates a Python environment; downloads aiohttp and discord.py):
   ```powershell
   .\install-controller.ps1
   ```
4. **Try it in this window:**
   ```powershell
   .\run-controller.ps1
   ```
5. **Make it start automatically** whenever you log in to Windows, and turn off sleep (Settings -> System ->
   Power -> Screen and sleep -> "Never" for sleep when plugged in):
   ```powershell
   .\install-controller.ps1 -Startup
   Start-ScheduledTask -TaskName "SMP Controller"
   ```
   Undo it any time with `.\install-controller.ps1 -Remove`.

## Playing

- Friends use **plain vanilla Minecraft Java, any version from 1.8 to 26.3**, and your tunnel address. No Fabric or
  mods needed: the server-side mods ViaFabric + ViaBackwards + ViaRewind translate between versions.
  (If you remove those three from [mods.txt](mods.txt), everyone must be on 26.3.)
- Old-version caveats: newer blocks and mobs show up as look-alikes for old clients, 1.8 players get the server's
  1.9+ combat rules, and a mod that adds new blocks/items breaks cross-version join (those mods also need every
  player to install them). Stay with server-only mods if you want everyone to join with vanilla.
- Add friends to the whitelist with the panel (`http://127.0.0.1:8080`, password from `controller.env`) or the
  Discord command `/whitelist-add name`.
- A whitelisted friend joins while the server sleeps -> sees "Waking the server up!" -> reconnects about a minute
  later. After `IDLE_MINUTES` (10) with nobody online it saves, backs up the world and sleeps.
- Strangers can't wake the server, and the real server is only reachable through the controller.

## Discord bot (optional)

1. https://discord.com/developers/applications -> **New Application** -> **Bot** -> copy the token into
   `DISCORD_TOKEN` in `controller.env`.
2. **OAuth2 -> URL Generator**: tick `bot` and `applications.commands`, open the link and add the bot to your server.
3. In Discord enable Developer Mode (Settings -> Advanced), right-click your server -> **Copy Server ID** into
   `DISCORD_GUILD_ID`. Right-click a channel -> **Copy Channel ID** into `DISCORD_CHANNEL_ID` to get "server is
   online / went to sleep" announcements. Set `DISCORD_ROLE_ID` to limit who can start/stop.
4. Restart the controller. Commands: `/status`, `/start`, `/stop`, `/whitelist-add`, `/whitelist-remove`.

Keep the token private. Anyone with it controls your bot.

## Files

| File | What it does |
|---|---|
| `setup.ps1` | Downloads Fabric + mods, writes `server.properties` (private port, RCON), creates `controller.env` |
| `install-controller.ps1` | Python environment; `-Startup` starts it at login |
| `run-controller.ps1` | Runs the controller and the playit.gg agent |
| `mods.txt` | Modrinth mod slugs. Re-run `setup.ps1` after editing. Content mods must be installed by every player |
| `controller/` | The controller (Python) and its tests |
| `start.ps1`, `backup.ps1` | Manual start / manual backup, only for troubleshooting |
| `cloud/` | The same idea in Docker for a rented/always-on Linux server |

## Troubleshooting

- **Logs:** `server\logs\latest.log` (Minecraft) and the controller window / `.\run-controller.ps1` output.
- **Won't wake:** the name must be on the whitelist (`server\whitelist.json`); check the controller is running.
- **Friends can't connect:** is the playit agent running and showing your tunnel as online? Is the PC awake?
- **Backups:** `backups\`, one per sleep, the newest 14 kept. To restore, stop the server and replace `server\world`.
- **Tests:** `cd controller; python -m unittest discover -s tests -t . -v`
