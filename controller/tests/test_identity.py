"""The server's name, description and picture (identity.py): what players see in their server lists, and how every host
ends up showing the same thing."""
import asyncio
import json
import os
import struct
import tempfile
import unittest
import zlib

from app import identity
from app import mcproto as proto
from app.config import Config
from app.shared import SharedWorld
from tests.test_all import Served, StubManager, handshake, read_frame, run


def png(size=(64, 64), color=(200, 40, 40)) -> bytes:
    w, h = size
    raw = b"".join(b"\x00" + bytes(color) * w for _ in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = os.path.join(self.tmp.name, "server")
        os.makedirs(self.server)

    def path(self, *parts):
        return os.path.join(self.server, *parts)

    def write(self, name, data, mode="w"):
        with open(self.path(name), mode) as f:
            f.write(data)

    def read(self, name, mode="r"):
        with open(self.path(name), mode) as f:
            return f.read()


class TextTests(unittest.TestCase):
    def test_clean_keeps_one_plain_line(self):
        self.assertEqual(identity.clean("  My§c  Server\n\tName  ", 32), "My Server Name")
        self.assertEqual(identity.clean("x" * 100, 10), "x" * 10)
        self.assertEqual(identity.clean(None, 10), "")
        self.assertEqual(identity.clean("Café ☃", 32), "Café ☃")           # real letters survive

    def test_motd_has_the_name_in_bold_and_the_description_under_it(self):
        self.assertEqual(identity.motd_text("Our SMP"), "§lOur SMP§r")
        self.assertEqual(identity.motd_text("Our SMP", "Survival"), "§lOur SMP§r\n§7Survival")

    def test_properties_escaping_round_trips(self):
        for text in ("plain", "Café ☃", "emoji \U0001F600 here", "back\\slash", "two\nlines",
                     "§lBold§r\n§7desc", "tab\there"):
            escaped = identity.properties_escape(text)
            self.assertTrue(escaped.isascii() and "\n" not in escaped, escaped)
            self.assertEqual(identity.properties_unescape(escaped), text)
        self.assertEqual(identity.properties_escape("§l"), "\\u00A7l")
        self.assertEqual(identity.properties_escape("a\nb"), "a\\nb")


class MotdFileTests(Tmp):
    def test_replaces_or_adds_motd_and_keeps_every_other_line(self):
        self.write("server.properties", "server-port=25566\nmotd=Old name\nenable-rcon=true\n")
        self.assertTrue(identity.set_motd(self.server, "New", "Hello é"))
        text = self.read("server.properties", "r")
        self.assertEqual(text.splitlines()[0::2], ["server-port=25566", "enable-rcon=true"])
        line = [ln for ln in text.splitlines() if ln.startswith("motd=")][0]
        self.assertEqual(line, "motd=\\u00A7lNew\\u00A7r\\n\\u00A77Hello \\u00E9")
        self.assertEqual(identity.properties_unescape(line[5:]), identity.motd_text("New", "Hello é"))
        self.assertFalse(identity.set_motd(self.server, "New", "Hello é"))             # nothing to change

    def test_adds_the_line_when_there_is_none_and_never_creates_the_file(self):
        self.write("server.properties", "server-port=25566\n")
        self.assertTrue(identity.set_motd(self.server, "Name"))
        self.assertIn("motd=\\u00A7lName\\u00A7r", self.read("server.properties"))
        os.remove(self.path("server.properties"))
        self.assertFalse(identity.set_motd(self.server, "Name"))
        self.assertFalse(os.path.exists(self.path("server.properties")))


class PictureTests(Tmp):
    def test_only_a_64x64_png_counts(self):
        self.assertTrue(identity.icon_ok(png()))
        self.assertFalse(identity.icon_ok(png((32, 32))))
        self.assertFalse(identity.icon_ok(png((64, 65))))
        self.assertFalse(identity.icon_ok(b"not a picture"))
        self.assertFalse(identity.icon_ok(png() + b"x" * identity.MAX_ICON_BYTES))

    def test_data_uri_follows_the_file(self):
        path = self.path("server-icon.png")
        self.assertIsNone(identity.icon_data_uri(path))
        identity.write_icon(self.server, png(color=(1, 2, 3)))
        first = identity.icon_data_uri(path)
        self.assertTrue(first.startswith("data:image/png;base64,"))
        identity.write_icon(self.server, png(color=(9, 9, 9)))
        self.assertNotEqual(identity.icon_data_uri(path), first)                        # re-read after a change
        identity.write_icon(self.server, None)
        self.assertIsNone(identity.icon_data_uri(path))
        with self.assertRaises(ValueError):
            identity.write_icon(self.server, png((10, 10)))


class SyncTests(Tmp):
    def setUp(self):
        super().setUp()
        self.cloud = os.path.join(self.tmp.name, "cloud")
        os.makedirs(self.cloud)
        self.world = SharedWorld(self.cloud, self.server, "A")

    def test_nothing_anywhere_means_nothing_to_do(self):
        self.assertIsNone(identity.sync(self.world, self.server))

    def test_a_newer_local_identity_is_published(self):
        identity.write_local(self.server, identity.make("Our SMP", "Survival", True, 100))
        identity.write_icon(self.server, png())
        self.assertEqual(identity.sync(self.world, self.server)["name"], "Our SMP")
        shared = self.world.read_identity()
        self.assertEqual((shared["name"], shared["description"], shared["icon"]), ("Our SMP", "Survival", True))
        self.assertTrue(identity.icon_ok(self.world.read_shared_icon()))

    def test_a_newer_shared_identity_is_taken_over_picture_included(self):
        identity.write_local(self.server, identity.make("Old", "", False, 100))
        self.world.write_identity(identity.make("Fresh name", "New text", True, 200), png(color=(5, 6, 7)))
        got = identity.sync(self.world, self.server)
        self.assertEqual((got["name"], got["description"]), ("Fresh name", "New text"))
        self.assertEqual(identity.read_local(self.server)["name"], "Fresh name")
        self.assertEqual(identity.read_icon(self.server), png(color=(5, 6, 7)))

    def test_removing_the_picture_reaches_the_other_host(self):
        identity.write_local(self.server, identity.make("Name", "", True, 100))
        identity.write_icon(self.server, png())
        self.world.write_identity(identity.make("Name", "", False, 200), None)
        identity.sync(self.world, self.server)
        self.assertIsNone(identity.read_icon(self.server))
        self.assertFalse(os.path.exists(self.path("server-icon.png")))

    def test_the_same_version_changes_nothing(self):
        ident = identity.make("Same", "", False, 100)
        identity.write_local(self.server, ident)
        self.world.write_identity(ident, None)
        before = os.stat(self.path("identity.json")).st_mtime_ns
        self.assertEqual(identity.sync(self.world, self.server)["name"], "Same")
        self.assertEqual(os.stat(self.path("identity.json")).st_mtime_ns, before)

    def test_a_damaged_shared_file_is_ignored(self):
        with open(os.path.join(self.cloud, "identity.json"), "w") as f:
            f.write("{not json")
        identity.write_local(self.server, identity.make("Mine", "", False, 100))
        self.assertEqual(identity.sync(self.world, self.server)["name"], "Mine")
        self.assertEqual(self.world.read_identity()["name"], "Mine")                    # and it was repaired


class ApplyTests(Tmp):
    def test_the_controller_uses_the_identity_and_writes_the_real_servers_motd(self):
        cfg = Config(server_dir=self.server, server_name="From env")
        self.write("server.properties", "motd=Old\nserver-port=25566\n")
        self.assertIsNone(identity.apply(cfg))
        identity.apply_to_server(cfg)
        self.assertIn("motd=Old", self.read("server.properties"))                     # no identity: nothing touched
        self.assertEqual(cfg.server_name, "From env")
        identity.write_local(self.server, identity.make("Chosen", "Friends only", False, 5))
        self.assertEqual(identity.apply(cfg)["name"], "Chosen")
        self.assertEqual((cfg.server_name, cfg.server_description), ("Chosen", "Friends only"))
        identity.apply_to_server(cfg)
        self.assertIn("motd=\\u00A7lChosen\\u00A7r\\n\\u00A77Friends only", self.read("server.properties"))

    def test_parse_rejects_junk(self):
        for raw in (b"", b"[]", b'{"name": ""}', b"\xff\xfe", b'{"description": "x"}'):
            self.assertIsNone(identity.parse(raw))
        parsed = identity.parse('{"name": "  A  B ", "icon": 1}')
        self.assertEqual((parsed["name"], parsed["icon"]), ("A B", True))


class ServerListTests(Tmp):
    """What a player's server list shows while the server sleeps."""

    def ping(self, cfg, state="asleep"):
        async def go():
            async with Served(StubManager(state), cfg) as s:
                reader, writer = await asyncio.open_connection("127.0.0.1", s.port)
                writer.write(handshake(1) + proto.frame(0))
                _pid, data = await read_frame(reader)
                writer.close()
                return json.loads(proto.decode_string(data, 0, 1 << 20)[0])
        return run(go())

    def test_name_description_and_picture_are_shown_while_asleep(self):
        identity.write_icon(self.server, png())
        body = self.ping(Config(server_dir=self.server, server_name="Our SMP", server_description="Survival"))
        self.assertEqual(body["description"]["text"], "§lOur SMP§r\n§eAsleep §7- Survival")
        self.assertTrue(body["favicon"].startswith("data:image/png;base64,"))

    def test_without_a_description_it_still_says_how_to_wake_it(self):
        body = self.ping(Config(server_dir=self.server, server_name="Our SMP"))
        self.assertIn("join to wake it up", body["description"]["text"])
        self.assertNotIn("favicon", body)

    def test_a_wrong_sized_picture_is_not_sent(self):
        with open(self.path("server-icon.png"), "wb") as f:
            f.write(png((128, 128)))
        self.assertNotIn("favicon", self.ping(Config(server_dir=self.server)))

    def test_other_states_keep_their_own_line(self):
        cfg = Config(server_dir=self.server, server_description="Survival")
        self.assertIn("Starting up", self.ping(cfg, "starting")["description"]["text"])


try:
    import aiohttp  # noqa: F401
    from aiohttp.test_utils import TestClient, TestServer
    from app.manager import Manager
    from app.web import build_app
except ImportError:  # pragma: no cover
    aiohttp = None


@unittest.skipIf(aiohttp is None, "aiohttp not installed")
class PanelReloadTests(Tmp):
    def test_the_app_can_make_a_running_controller_pick_up_a_change(self):
        class Runtime:
            def state(self, name):
                return "exited"
        cfg = Config(panel_password="pw", server_dir=self.server, server_name="Before",
                     state_dir=os.path.join(self.tmp.name, "state"), whitelist_path=self.path("w.json"))

        async def go():
            async with TestClient(TestServer(build_app(cfg, Manager(cfg, Runtime())))) as client:
                self.assertEqual((await client.post("/api/reload-identity")).status, 401)
                await client.post("/api/login", json={"password": "pw"})
                identity.write_local(self.server, identity.make("After", "New text", False, 9))
                resp = await client.post("/api/reload-identity")
                return resp.status, await resp.json()
        status, body = asyncio.run(go())
        self.assertEqual(status, 200)
        self.assertEqual((body["name"], body["description"], body["custom"]), ("After", "New text", True))
        self.assertEqual((cfg.server_name, cfg.server_description), ("After", "New text"))


if __name__ == "__main__":
    unittest.main()
