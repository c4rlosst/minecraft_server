"""Discord command wiring tests. Needs discord.py (installed in controller/.venv); skipped without it."""
import asyncio
import tempfile
import unittest

try:
    import discord
    from discord import app_commands
    from app.bot import register_commands
    from app.blocklist import Blocklist
except ImportError:  # pragma: no cover
    discord = None

from app.config import Config
from app.manager import PlayersOnline


class FakeManager:
    def __init__(self):
        self.calls = []

    def snapshot(self):
        return {"server_name": "JAPAX x RENE", "state": "asleep", "players": 0, "max_players": 10, "names": [],
                "sleeps_in": None, "address": "example.tun.ply.gg"}

    async def start(self):
        self.calls.append("start")
        return True

    async def stop(self, force=False):
        self.calls.append("stop")
        return False

    async def whitelist_change(self, op, name):
        self.calls.append((op, name))
        return "queued"


class FakeResponse:
    def __init__(self):
        self.sent = []

    async def send_message(self, text, ephemeral=False):
        self.sent.append((text, ephemeral))


class FakeInteraction:
    def __init__(self, role_ids=(), user_id=1, admin=False):
        self.response = FakeResponse()
        self.user = type("U", (), {"id": user_id, "roles": [type("R", (), {"id": i})() for i in role_ids]})()
        self.permissions = type("P", (), {"administrator": admin, "manage_guild": admin})()


class FakeMember:
    def __init__(self, user_id, admin=False, bot=False):
        self.id, self.bot = user_id, bot
        self.guild_permissions = type("P", (), {"administrator": admin, "manage_guild": admin})()
        self.mention = f"<@{user_id}>"


@unittest.skipIf(discord is None, "discord.py not installed")
class BotTests(unittest.TestCase):
    def build(self, **cfg_kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Config(state_dir=tmp.name, **cfg_kwargs)

        async def go():
            client = discord.Client(intents=discord.Intents.none())
            tree = app_commands.CommandTree(client)
            manager = FakeManager()
            blocklist = Blocklist(cfg)
            group = register_commands(tree, cfg, manager, None, blocklist)
            return tree, group, manager, blocklist
        tree, group, manager, blocklist = asyncio.run(go())
        return tree, {c.name: c for c in group.commands}, manager, blocklist

    def test_everything_lives_under_jarvis(self):
        tree, commands, _, _ = self.build()
        self.assertEqual([c.name for c in tree.get_commands()], ["jarvis"])      # no more top-level /start
        self.assertEqual(sorted(commands), ["block", "blocked", "handover", "start", "status", "stop", "unblock",
                                            "whitelist-add", "whitelist-remove"])

    def test_start_stop_status_run_the_manager(self):
        _, commands, manager, _ = self.build()
        results = {}

        async def go():
            for name in ("start", "stop", "status"):
                interaction = FakeInteraction()
                await commands[name].callback(interaction)
                results[name] = interaction.response.sent[0][0]
        asyncio.run(go())
        self.assertEqual(manager.calls, ["start", "stop"])
        self.assertIn("Starting the server", results["start"])
        self.assertIn("Nothing to do", results["stop"])
        self.assertIn("JAPAX x RENE", results["status"])

    def test_stop_from_discord_is_refused_while_players_are_online(self):
        _, commands, manager, _ = self.build()

        async def refusing_stop(force=False):
            manager.calls.append(("stop", force))
            raise PlayersOnline(2, ["Alex", "Sam"])
        manager.stop = refusing_stop

        async def go():
            interaction = FakeInteraction()
            await commands["stop"].callback(interaction)
            return interaction
        interaction = asyncio.run(go())
        self.assertEqual(manager.calls, [("stop", False)])           # Discord never forces
        self.assertIn("playing right now", interaction.response.sent[0][0])
        self.assertIn("Alex", interaction.response.sent[0][0])

    def test_role_restriction_blocks_start_for_others_but_not_status(self):
        _, commands, manager, _ = self.build(discord_role_id=42)

        async def go():
            outsider, member, status = FakeInteraction([1]), FakeInteraction([42]), FakeInteraction([1])
            await commands["start"].callback(outsider)
            await commands["start"].callback(member)
            await commands["status"].callback(status)
            return outsider, status
        outsider, status = asyncio.run(go())
        self.assertIn("role", outsider.response.sent[0][0])
        self.assertEqual(manager.calls, ["start"])                                # only the member's call went through
        self.assertIn("JAPAX x RENE", status.response.sent[0][0])

    def test_whitelist_command_validates_names(self):
        _, commands, manager, _ = self.build()

        async def go():
            bad, good = FakeInteraction(), FakeInteraction()
            await commands["whitelist-add"].callback(bad, "no spaces allowed!")
            await commands["whitelist-add"].callback(good, "Alex")
            return bad, good
        bad, good = asyncio.run(go())
        self.assertIn("valid Minecraft name", bad.response.sent[0][0])
        self.assertEqual(manager.calls, [("add", "Alex")])
        self.assertIn("queued", good.response.sent[0][0])

    # ---- block list
    def test_blocked_people_cannot_use_any_command_but_everyone_else_can(self):
        _, commands, manager, blocklist = self.build(discord_blocked_ids="111")
        blocklist.add(222)

        async def go():
            outs = {}
            for who in (111, 222, 333):
                for name in ("status", "start", "stop"):
                    interaction = FakeInteraction(user_id=who)
                    await commands[name].callback(interaction)
                    outs[(who, name)] = interaction.response.sent[0]
            return outs
        outs = asyncio.run(go())
        for who in (111, 222):                       # 111 blocked in config, 222 blocked with /jarvis block
            for name in ("status", "start", "stop"):
                text, ephemeral = outs[(who, name)]
                self.assertIn("not allowed", text)
                self.assertTrue(ephemeral)
        self.assertNotIn("not allowed", outs[(333, "status")][0])
        self.assertEqual(manager.calls, ["start", "stop"])            # only the unblocked person's calls ran

    def test_owner_and_admins_are_never_blocked_and_skip_the_role_check(self):
        _, commands, manager, _ = self.build(discord_blocked_ids="999", discord_role_id=42)

        async def go():
            interaction = FakeInteraction(user_id=999, admin=True)     # blocked in config AND no role, but an admin
            await commands["start"].callback(interaction)
            return interaction
        interaction = asyncio.run(go())
        self.assertEqual(manager.calls, ["start"])
        self.assertIn("Starting", interaction.response.sent[0][0])

    def test_only_admins_can_block_and_never_other_admins_or_bots(self):
        _, commands, _, blocklist = self.build()

        async def go():
            outsider = FakeInteraction()
            await commands["block"].callback(outsider, FakeMember(5))
            friend, admin_target, bot_target = (FakeInteraction(admin=True) for _ in range(3))
            await commands["block"].callback(friend, FakeMember(5))
            await commands["block"].callback(admin_target, FakeMember(6, admin=True))
            await commands["block"].callback(bot_target, FakeMember(7, bot=True))
            return outsider, friend, admin_target, bot_target
        outsider, friend, admin_target, bot_target = asyncio.run(go())
        self.assertIn("Only server admins", outsider.response.sent[0][0])
        self.assertIn("can no longer", friend.response.sent[0][0])
        self.assertIn("can't be blocked", admin_target.response.sent[0][0])
        self.assertIn("can't be blocked", bot_target.response.sent[0][0])
        self.assertTrue(blocklist.is_blocked(5))
        self.assertFalse(blocklist.is_blocked(6))
        self.assertFalse(blocklist.is_blocked(7))

    def test_unblock_and_list(self):
        _, commands, _, blocklist = self.build(discord_blocked_ids="10")
        blocklist.add(5)

        async def go():
            listing, unblock, config_one, not_blocked = (FakeInteraction(admin=True) for _ in range(4))
            await commands["blocked"].callback(listing)
            await commands["unblock"].callback(unblock, FakeMember(5))
            await commands["unblock"].callback(config_one, FakeMember(10))
            await commands["unblock"].callback(not_blocked, FakeMember(77))
            return listing, unblock, config_one, not_blocked
        listing, unblock, config_one, not_blocked = asyncio.run(go())
        self.assertIn("<@5>", listing.response.sent[0][0])
        self.assertIn("<@10>", listing.response.sent[0][0])
        self.assertIn("can use the server commands again", unblock.response.sent[0][0])
        self.assertIn("controller.env", config_one.response.sent[0][0])
        self.assertIn("wasn't blocked", not_blocked.response.sent[0][0])
        self.assertFalse(blocklist.is_blocked(5))
        self.assertTrue(blocklist.is_blocked(10))

    def test_blocklist_is_saved_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(state_dir=tmp, discord_blocked_ids="10, 11")
            first = Blocklist(cfg)
            self.assertTrue(first.add(20))
            self.assertFalse(first.add(20))                              # already blocked
            self.assertEqual(first.remove(10), "in config")              # fixed entries are removed in controller.env
            second = Blocklist(cfg)                                      # a restart keeps the list
            self.assertEqual(second.all(), [10, 11, 20])
            self.assertEqual(second.remove(20), "removed")
            self.assertEqual(second.remove(20), "not blocked")
            self.assertEqual(Blocklist(cfg).all(), [10, 11])


if __name__ == "__main__":
    unittest.main()
