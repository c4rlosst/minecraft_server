# Aternos-style SMP (always-on Linux box + Docker)

A small **controller** owns the public Minecraft port. The real Fabric server runs in its own container and is
**asleep until a whitelisted player joins** (or someone presses Start / uses `/start` in Discord). It goes back to
sleep after `IDLE_MINUTES` with nobody online. Includes a password-protected **web panel**, an optional **Discord
bot**, and automatic **backups**.

```
players ──25565──> controller ──(online)──> mc (Fabric)   <── start/stop via Docker socket
friends ──8080───> web panel  ──┘                         <── whitelist via RCON
Discord ─────────> bot ───────┘
```

## What you need

An always-on Linux machine with Docker. It doesn't care whether it's x86 or ARM.

| Option | Notes |
|---|---|
| **Oracle Cloud "Always Free" VM** | Free, but check Oracle's current Always Free page first. Reports say the free Ampere allowance was cut in 2026, idle VMs can be reclaimed, and ARM capacity is often "out of capacity". Choose your home region carefully, it can't be changed. |
| **Cheap VPS** (Hetzner, OVH, DigitalOcean...) | Roughly $5-15/month. Fine for 6 GB of Minecraft RAM. |
| **A PC / mini PC / Raspberry Pi 5 at home** | Free if it stays on. Use the playit profile so you don't need port forwarding. |

Plan for **8 GB RAM or more** on the machine if the server uses 6 GB (`MC_MEMORY`). Modded servers need headroom.

## Set up

On the Linux machine (Ubuntu shown):

```bash
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER   # then log out and back in
git clone https://github.com/c4rlosst/minecraft_server.git
cd minecraft_server/cloud
bash init.sh         # creates .env with random RCON + panel passwords
nano .env            # set EULA=TRUE, OPS=YourName, PUBLIC_ADDRESS=...
```

Setting `EULA=TRUE` means you accept the [Minecraft EULA](https://aka.ms/MinecraftEULA).

### Make it reachable (pick one)

**A. playit.gg, no ports to open (works at home or on a VM).** Put your agent's secret key in `PLAYIT_SECRET_KEY`
in `.env` (the same tunnel and address you already made, point it at port `25565`), then:

```bash
docker compose --profile playit up -d --build
```

**B. Direct connection.** Open **TCP 25565** (Minecraft) and **8080** (panel) in the provider's firewall and the
machine's firewall (on Oracle: the VCN security list **and** the instance's own iptables/ufw). Friends connect to
the machine's public IP or a domain you point at it (a free DuckDNS name works).

```bash
docker compose up -d --build
```

The first start downloads the server and mods, then goes to sleep after the idle timeout. That's normal.

### Optional: HTTPS for the panel

Point a domain at the machine, set `PANEL_DOMAIN` in `.env` (and `TRUST_PROXY=1`, `PANEL_BIND=127.0.0.1`), open
ports 80/443, and add `--profile https`:

```bash
docker compose --profile https up -d --build
```

Without HTTPS the panel password travels in plain text, so use HTTPS if the panel is on the open internet.

### Optional: Discord bot

1. https://discord.com/developers/applications -> **New Application** -> **Bot** -> copy the token into `DISCORD_TOKEN`.
2. **OAuth2 -> URL Generator**: scopes `bot` and `applications.commands`, open the link to invite it to your server.
3. Put your Discord server's ID in `DISCORD_GUILD_ID` (commands appear instantly) and, to limit who can
   start/stop, a role ID in `DISCORD_ROLE_ID`. Then `docker compose up -d`.

Commands: `/status`, `/start`, `/stop`, `/whitelist-add name`, `/whitelist-remove name`.

## Day to day

- **Panel:** `http://<machine>:8080` (or your HTTPS domain). Log in with `PANEL_PASSWORD`. Start/stop, players,
  whitelist and the console log.
- **Joining wakes it:** a **whitelisted** player who connects to a sleeping server sees "Waking the server up!" and
  reconnects about a minute later. Strangers can't wake it.
- **Console:** `docker exec -i smp-mc rcon-cli` (type commands, e.g. `op YourName`).
- **Backups:** every 6 h to `backups/` (14 days kept), only after someone has played.
- **Update mods:** edit `MODS` in `.env`, then `docker compose up -d` and start the server.
- **Logs:** `docker compose logs -f controller`.

## Limits worth knowing

- Joining a sleeping server **kicks you with a message** the first time; you reconnect once it's up. (Holding the
  connection open like Aternos does needs deeper protocol work.)
- The controller needs the **Docker socket** to start/stop the server, which is effectively root on that machine.
  Keep the panel password strong and the machine dedicated to this.
- The server sees every player as coming from the controller, so IP bans are useless. Use name bans/whitelist.
- Tested here with simulated clients and servers only. Test a real join, sleep and wake on 26.3 before inviting
  everyone, and tell me what you see if a step misbehaves.
- The old Windows scripts in the repo root still work for a one-PC setup; this folder is the always-on alternative.

## Developing

```bash
cd controller && python -m unittest discover -s tests -t . -v
```
