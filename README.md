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
  Discord command `/jarvis whitelist-add name`. (Not needed in offline mode, see below.)
- A whitelisted friend joins while the server sleeps -> sees "Waking the server up!" -> reconnects about a minute
  later. After `IDLE_MINUTES` (10) with nobody online it saves, backs up the world and sleeps.
- Strangers can't wake the server (with the whitelist on), and the real server is only reachable through the
  controller.

## Voice chat (Simple Voice Chat)

`simple-voice-chat` is in [mods.txt](mods.txt). Everyone who wants voice installs **Fabric + Fabric API + Simple Voice
Chat (same 26.3 build)** in their game (PolyMC/Prism); friends without it can still play. In game, **V** opens the
voice menu; a crossed-out plug icon means voice isn't connected.

Voice runs over **UDP port 24454**, which the free playit tunnel does not carry. To make it work for friends outside
your home:
1. **Router:** forward **UDP 24454** to this PC's LAN IP (reserve that IP so it doesn't change). It only works with a
   real public IP, not a shared/CGNAT connection (check the router's WAN IP equals your public IP).
2. **Windows Firewall** (admin PowerShell): `New-NetFirewallRule -DisplayName "Minecraft voice chat" -Direction Inbound -Protocol UDP -LocalPort 24454 -Action Allow`
3. **Tell clients where voice lives:** in `server\config\voicechat\voicechat-server.properties` set
   `voice_host=<your public IP or DDNS name>:24454`, then restart the Minecraft server (let it sleep, then wake it).
   Your home IP can change when the router restarts; update `voice_host` (or use a free DDNS name).

Voice only connects through your home address, so players see your public IP in their game. Without the forward, use a
Discord voice channel instead.

## Adding mods from the panel

The panel has a **Mods** card: upload a `.jar`, a `.zip` of jars, or a Modrinth `.mrpack` (from modrinth.com, via
"Download" on a modpack page), and remove installed mods. Only possible while the server is **asleep** (press
Stop server first). Changes apply the next time it starts.

- Only **Fabric** mods for the server's Minecraft version are accepted (a Forge/NeoForge jar is refused with a
  message). Client-only mods are skipped, an updated version of an installed mod replaces the old one.
- `.mrpack` files: mods are downloaded from Modrinth/GitHub, checked against their published hashes, and the pack's
  config files are applied (only into `mods`, `config` and `defaultconfigs`).
- Replaced or removed jars are moved to `server\mods-removed`, not deleted.
- **Mods run as code on this PC**: only upload mods you trust. The panel is protected by your panel password and is
  only reachable from this PC.
- Mods that add blocks or items need **every player to install them** (Fabric + the same mods) and break
  joining from older versions. World-generation mods like Terralith work with vanilla clients.
- You can also add mods by listing their Modrinth slug in [mods.txt](mods.txt) and running `.\setup.ps1`.

## Offline (non-Microsoft) accounts

By default only accounts verified by Mojang can join (`online-mode=true`, whitelist on). If friends use offline
launcher accounts, switch the server to offline mode (close the controller window first):

```powershell
.\set-account-mode.ps1 -Offline      # and  -Online  to switch back
.\run-controller.ps1
```

The script stops the Minecraft server itself, turns off the account check **and the whitelist**, and installs
**EasyAuth**: every player protects their name with a password, `/register <password> <password>` the first time and
`/login <password>` after that. Operators come from `OPS` in `controller.env`. (The whitelist stays off in this mode
because `/whitelist add` would store the online ID for real accounts, which never matches the offline ID players join
with. `-Online` brings the whitelist back with the same names.)

**Why the passwords matter:** without account verification, anyone who knows a name could join *as* that player,
including the operator, and since there's no whitelist anyone with the address can try to join and register. Keep
the address private. Whoever registers a name first owns it, so **join and register the operator account before you
tell anyone the address.** Admin commands (operators only): `/auth list`, `/auth update <name> <newpassword>` (reset a
password), `/auth remove <name>`. For a shared "door code" instead of per-player passwords, EasyAuth has an
`enableGlobalPassword` option in `config/EasyAuth/main.conf` (set it with `/auth setGlobalPassword <password>`).

## Discord bot (optional)

1. https://discord.com/developers/applications -> **New Application** -> **Bot** -> copy the token into
   `DISCORD_TOKEN` in `controller.env`.
2. **OAuth2 -> URL Generator**: tick `bot` and `applications.commands`, open the link and add the bot to your server.
3. In Discord enable Developer Mode (Settings -> Advanced), right-click your server -> **Copy Server ID** into
   `DISCORD_GUILD_ID`. Right-click a channel -> **Copy Channel ID** into `DISCORD_CHANNEL_ID` to get "server is
   online / went to sleep" announcements. Set `DISCORD_ROLE_ID` to limit who can start/stop.
4. Restart the controller. Commands: `/jarvis status`, `/jarvis start`, `/jarvis stop`, `/jarvis whitelist-add`,
   `/jarvis whitelist-remove`. `/jarvis stop` refuses while anyone is playing (only the panel can force it).
5. **Stop someone from using the commands:** a Discord server admin (anyone with Manage Server, including the
   owner) runs `/jarvis block @person`, `/jarvis unblock @person` or `/jarvis blocked` to see the list. Or put
   Discord user IDs in `DISCORD_BLOCKED_IDS` (comma separated) in `controller.env`. Blocked people can't use any
   `/jarvis` command. The owner and admins can never be blocked.

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
- **"Can't connect" or "Connection reset" on the tunnel address:** always join with the tunnel *name*, never its IP
  (playit routes by hostname, so an IP join is cut off). If the name doesn't resolve on a PC
  (`Resolve-DnsName your-name.tun.ply.gg` says "does not exist"), its DNS is stale or blocking it: run
  `ipconfig /flushdns`, restart the router, or put `8.8.8.8` first in that PC's DNS settings.
- **Whitelist add says "isn't a real Minecraft account":** the name must be an existing Microsoft/Mojang account.
- **Friends can't connect:** is the playit agent running and showing your tunnel as online? Is the PC awake?
- **Backups:** `backups\`, one per sleep, the newest 14 kept. To restore, stop the server and replace `server\world`.
- **Tests:** `cd controller; python -m unittest discover -s tests -t . -v`
