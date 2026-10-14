"""Optional Discord bot. One command with subcommands:
/jarvis start, stop, status, whitelist-add, whitelist-remove, and for server admins: block, unblock, blocked."""
import logging

from .blocklist import Blocklist
from .manager import NAME_RE, PlayersOnline

log = logging.getLogger("bot")
COMMAND_NAME = "jarvis"


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


def _is_admin(member_or_perms) -> bool:
    perms = member_or_perms
    return bool(getattr(perms, "administrator", False) or getattr(perms, "manage_guild", False))


def register_commands(tree, cfg, manager, guild=None, blocklist=None):
    """Add the /jarvis command group to the tree and return it."""
    import discord
    from discord import app_commands

    blocklist = blocklist or Blocklist(cfg)
    group = app_commands.Group(name=COMMAND_NAME, description="Control the Minecraft server")

    def has_role(interaction) -> bool:
        if not cfg.discord_role_id:
            return True
        return any(role.id == cfg.discord_role_id for role in getattr(interaction.user, "roles", []))

    async def guard(interaction, need_role=True) -> bool:
        """True if the command may run; otherwise replies with the reason."""
        if _is_admin(getattr(interaction, "permissions", None)):
            return True   # the server owner and admins are never blocked and don't need the role
        if blocklist.is_blocked(interaction.user.id):
            await interaction.response.send_message("You're not allowed to use the server commands.", ephemeral=True)
            return False
        if need_role and not has_role(interaction):
            await interaction.response.send_message("You don't have the role needed for that.", ephemeral=True)
            return False
        return True

    async def admin_only(interaction) -> bool:
        if not _is_admin(interaction.permissions):
            await interaction.response.send_message("Only server admins (Manage Server) can do that.", ephemeral=True)
            return False
        return True

    @group.command(name="status", description="Is the server running?")
    async def status(interaction: discord.Interaction):
        if not await guard(interaction, need_role=False):
            return
        await interaction.response.send_message(describe(manager.snapshot()))

    @group.command(name="start", description="Wake the server up")
    async def start(interaction: discord.Interaction):
        if not await guard(interaction):
            return
        changed = await manager.start()
        if changed:
            log.info("%s started the server with /jarvis start", interaction.user)
        await interaction.response.send_message(
            "Starting the server, it'll be ready in about a minute." if changed
            else f"Nothing to do. {describe(manager.snapshot())}")

    @group.command(name="stop", description="Put the server to sleep (only when nobody is playing)")
    async def stop(interaction: discord.Interaction):
        if not await guard(interaction):
            return
        try:
            changed = await manager.stop()   # never forced from Discord
        except PlayersOnline as exc:
            log.info("%s tried /jarvis stop while %s player(s) were online: refused", interaction.user, exc.players)
            return await interaction.response.send_message(exc.message())
        if changed:
            log.info("%s stopped the server with /jarvis stop", interaction.user)
        await interaction.response.send_message(
            "Saving the world and shutting down." if changed else f"Nothing to do. {describe(manager.snapshot())}")

    async def whitelist(interaction, op: str, name: str):
        if not await guard(interaction):
            return
        if not NAME_RE.match(name):
            return await interaction.response.send_message("That isn't a valid Minecraft name.", ephemeral=True)
        try:
            result = await manager.whitelist_change(op, name)
        except Exception as exc:
            return await interaction.response.send_message(f"Failed: {exc}", ephemeral=True)
        note = "done" if result == "applied" else "queued, it applies the next time the server starts"
        await interaction.response.send_message(f"Whitelist {op} `{name}`: {note}.")

    @group.command(name="whitelist-add", description="Let a player join")
    async def whitelist_add(interaction: discord.Interaction, name: str):
        await whitelist(interaction, "add", name)

    @group.command(name="whitelist-remove", description="Stop a player joining")
    async def whitelist_remove(interaction: discord.Interaction, name: str):
        await whitelist(interaction, "remove", name)

    # ---- block list (server admins only) -----------------------------------------------------------
    @group.command(name="block", description="(Admins) Stop someone from using the server commands")
    async def block(interaction: discord.Interaction, user: discord.Member):
        if not await admin_only(interaction):
            return
        if user.bot or _is_admin(getattr(user, "guild_permissions", None)):
            return await interaction.response.send_message("Admins and bots can't be blocked.", ephemeral=True)
        added = blocklist.add(user.id)
        log.info("%s blocked %s from the commands", interaction.user, user)
        await interaction.response.send_message(
            f"{user.mention} can no longer use the server commands." if added else f"{user.mention} was already blocked.")

    @group.command(name="unblock", description="(Admins) Let someone use the server commands again")
    async def unblock(interaction: discord.Interaction, user: discord.Member):
        if not await admin_only(interaction):
            return
        result = blocklist.remove(user.id)
        message = {"removed": f"{user.mention} can use the server commands again.",
                   "not blocked": f"{user.mention} wasn't blocked.",
                   "in config": f"{user.mention} is blocked in controller.env (DISCORD_BLOCKED_IDS). Remove them there."}[result]
        await interaction.response.send_message(message)

    @group.command(name="blocked", description="(Admins) Who is blocked from the server commands?")
    async def blocked(interaction: discord.Interaction):
        if not await admin_only(interaction):
            return
        ids = blocklist.all()
        await interaction.response.send_message(
            "Blocked: " + ", ".join(f"<@{i}>" for i in ids) if ids else "Nobody is blocked.", ephemeral=True)

    tree.add_command(group, guild=guild)
    return group


async def run_bot(cfg, manager):
    import discord
    from discord import app_commands

    client = discord.Client(intents=discord.Intents.default())
    tree = app_commands.CommandTree(client)
    guild = discord.Object(id=cfg.discord_guild_id) if cfg.discord_guild_id else None
    register_commands(tree, cfg, manager, guild)

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
        await tree.sync(guild=guild)   # replaces the old /start-style commands with /jarvis
        log.info("Discord bot ready as %s", client.user)

    await client.start(cfg.discord_token)
