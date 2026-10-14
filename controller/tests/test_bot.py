"""Discord command wiring tests. Needs discord.py (installed in controller/.venv); skipped without it."""
import asyncio
import unittest

try:
    import discord
    from discord import app_commands
    from app.bot import register_commands, describe
except ImportError:  # pragma: no cover
    discord = None

from app.config import Config


class FakeManager:
    def __init__(self):
        self.calls = []

    def snapshot(self):
        return {"server_name": "JAPAX x RENE", "state": "asleep", "players": 0, "max_players": 10, "names": [],
                "sleeps_in": None, "address": "example.tun.ply.gg"}

    async def start(self):
        self.calls.append("start")
        return True

    async def stop(self):
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
    def __init__(self, role_ids=()):
        self.response = FakeResponse()
        self.user = type("U", (), {"roles": [type("R", (), {"id": i})() for i in role_ids]})()


@unittest.skipIf(discord is None, "discord.py not installed")
class BotTests(unittest.TestCase):
    def build(self, **cfg_kwargs):
        async def go():
            client = discord.Client(intents=discord.Intents.none())
            tree = app_commands.CommandTree(client)
            manager = FakeManager()
            group = register_commands(tree, Config(**cfg_kwargs), manager, None)
            return tree, group, manager
        return asyncio.run(go())

    def test_everything_lives_under_jarvis(self):
        tree, group, _ = self.build()
        self.assertEqual([c.name for c in tree.get_commands()], ["jarvis"])      # no more top-level /start
        self.assertEqual(sorted(c.name for c in group.commands),
                         ["start", "status", "stop", "whitelist-add", "whitelist-remove"])

    def test_start_stop_status_run_the_manager(self):
        _, group, manager = self.build()
        commands = {c.name: c for c in group.commands}

        async def go():
            for name in ("start", "stop", "status"):
                interaction = FakeInteraction()
                await commands[name].callback(interaction)
                yield_name = (name, interaction.response.sent[0][0])
                results.append(yield_name)
        results = []
        asyncio.run(go())
        self.assertEqual(manager.calls, ["start", "stop"])
        self.assertIn("Starting the server", dict(results)["start"])
        self.assertIn("Nothing to do", dict(results)["stop"])
        self.assertIn("JAPAX x RENE", dict(results)["status"])

    def test_role_restriction_blocks_start_for_others_but_not_status(self):
        _, group, manager = self.build(discord_role_id=42)
        commands = {c.name: c for c in group.commands}

        async def go():
            outsider, member = FakeInteraction([1]), FakeInteraction([42])
            await commands["start"].callback(outsider)
            await commands["start"].callback(member)
            status = FakeInteraction([1])
            await commands["status"].callback(status)
            return outsider, member, status
        outsider, member, status = asyncio.run(go())
        self.assertIn("role", outsider.response.sent[0][0])
        self.assertEqual(manager.calls, ["start"])                                # only the member's call went through
        self.assertIn("JAPAX x RENE", status.response.sent[0][0])

    def test_whitelist_command_validates_names(self):
        _, group, manager = self.build()
        commands = {c.name: c for c in group.commands}

        async def go():
            bad, good = FakeInteraction(), FakeInteraction()
            await commands["whitelist-add"].callback(bad, "no spaces allowed!")
            await commands["whitelist-add"].callback(good, "Alex")
            return bad, good
        bad, good = asyncio.run(go())
        self.assertIn("valid Minecraft name", bad.response.sent[0][0])
        self.assertEqual(manager.calls, [("add", "Alex")])
        self.assertIn("queued", good.response.sent[0][0])


if __name__ == "__main__":
    unittest.main()
