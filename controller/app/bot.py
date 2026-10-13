"""Optional Discord bot: /status, /start, /stop, /whitelist-add, /whitelist-remove."""
import logging

from .manager import NAME_RE

log = logging.getLogger("bot")


def describe(snap: dict) -> str:
    line = f"**{snap['server_name']}** is **{snap['state']}**"
    if snap["state"] == "online":
        line += f" - {snap['players']}/{snap['max_players']} players"
        if snap["names"]:
            line += f" ({', '.join(snap['names'])})"
        if snap["sleeps_in"] is not None:
            line += f"\nSleeps in about {snap['sleeps_in'] // 60 + 1} min if nobody joins."
    if snap["address"]:
        line += f"\nAddress: `{snap['address']}`"
    return line


async def run_bot(cfg, manager):
    import discord
    from discord import app_commands

    client = discord.Client(intents=discord.Intents.default())
    tree = app_commands.CommandTree(client)
    guild = discord.Object(id=cfg.discord_guild_id) if cfg.discord_guild_id else None

    def allowed(interaction) -> bool:
        if not cfg.discord_role_id:
            return True
        return any(role.id == cfg.discord_role_id for role in getattr(interaction.user, "roles", []))

    async def deny(interaction):
        await interaction.response.send_message("You don't have the role needed for that.", ephemeral=True)

    @tree.command(name="status", description="Is the server running?", guild=guild)
    async def status(interaction: discord.Interaction):
        await interaction.response.send_message(describe(manager.snapshot()))

    @tree.command(name="start", description="Wake the server up", guild=guild)
    async def start(interaction: discord.Interaction):
        if not allowed(interaction):
            return await deny(interaction)
        changed = await manager.start()
        await interaction.response.send_message(
            "Starting the server, it'll be ready in about a minute." if changed
            else f"Nothing to do. {describe(manager.snapshot())}")

    @tree.command(name="stop", description="Put the server to sleep", guild=guild)
    async def stop(interaction: discord.Interaction):
        if not allowed(interaction):
            return await deny(interaction)
        changed = await manager.stop()
        await interaction.response.send_message(
            "Saving the world and shutting down." if changed else f"Nothing to do. {describe(manager.snapshot())}")

    async def whitelist(interaction, op: str, name: str):
        if not allowed(interaction):
            return await deny(interaction)
        if not NAME_RE.match(name):
            return await interaction.response.send_message("That isn't a valid Minecraft name.", ephemeral=True)
        try:
            result = await manager.whitelist_change(op, name)
        except Exception as exc:
            return await interaction.response.send_message(f"Failed: {exc}", ephemeral=True)
        note = "done" if result == "applied" else "queued, it applies the next time the server starts"
        await interaction.response.send_message(f"Whitelist {op} `{name}`: {note}.")

    @tree.command(name="whitelist-add", description="Let a player join", guild=guild)
    async def whitelist_add(interaction: discord.Interaction, name: str):
        await whitelist(interaction, "add", name)

    @tree.command(name="whitelist-remove", description="Stop a player joining", guild=guild)
    async def whitelist_remove(interaction: discord.Interaction, name: str):
        await whitelist(interaction, "remove", name)

    async def announce(old: str, new: str):
        channel = client.get_channel(cfg.discord_channel_id) if cfg.discord_channel_id else None
        if channel is None:
            return
        snap = manager.snapshot()
        text = {
            "starting": "Waking the server up...",
            "online": "The server is **online**." + (f" Join with `{snap['address']}`" if snap["address"] else ""),
            "asleep": "The server went back to sleep." if old == "stopping" else None,
        }.get(new)
        if text:
            await channel.send(text)

    manager.on_change = announce

    @client.event
    async def on_ready():
        await tree.sync(guild=guild)
        log.info("Discord bot ready as %s", client.user)

    await client.start(cfg.discord_token)
