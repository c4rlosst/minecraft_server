"""SMP Host (the desktop app): invites, per-PC setup, the bundled playit agent, the panel client, and one real run of
the controller the way the app starts it."""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from app.desktop import invite as invites
from app.desktop.home import Home, Tools, fit_memory
from app.desktop.runner import Controller, Panel, last_error, log_size
from app.playit import PlayitDaemon

ENV = {"RCON_PASSWORD": "rcon-pw", "PANEL_PASSWORD": "panel-pw", "SERVER_NAME": "Test SMP", "SERVER_DESCRIPTION": "Survival with friends",
       "PUBLIC_ADDRESS": "cairo-unviable.tun.ply.gg", "OPS": "MCboy23", "MC_VERSION": "26.3", "MC_MEMORY": "6G",
       "DISCORD_TOKEN": "bot-token", "HOST_NAME": "MCboy23", "SHARED_DIR": r"G:\My Drive\world",
       "VOICE_HOST": "auto", "AUTO_SHUTDOWN_MINUTES": "10"}
PLAYIT = 'secret_key = "abc123"\n'
B2 = {"type": "b2", "bucket": "my-world-4821", "account": "key-id", "key": "app-key"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def png(size=(64, 64), color=(60, 120, 200)) -> bytes:
    import struct
    import zlib
    w, h = size
    raw = b"".join(b"\x00" + bytes(color) * w for _ in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class InviteTests(unittest.TestCase):
    def test_round_trip_carries_shared_settings_but_never_this_pcs_own(self):
        data = invites.read(invites.make(ENV, PLAYIT, B2))
        self.assertEqual(data["storage"], B2)
        self.assertEqual(data["playit"], PLAYIT)
        self.assertEqual(data["env"]["DISCORD_TOKEN"], "bot-token")
        for own in ("HOST_NAME", "SHARED_DIR", "VOICE_HOST", "AUTO_SHUTDOWN_MINUTES"):
            self.assertNotIn(own, data["env"])

    def test_pasting_from_a_chat_with_line_breaks_still_works(self):
        code = invites.make(ENV, PLAYIT, B2)
        broken = "  " + "\n".join(code[i:i + 40] for i in range(0, len(code), 40)) + "\n"
        self.assertEqual(invites.read(broken)["storage"]["bucket"], "my-world-4821")

    def test_clear_messages_for_bad_invites(self):
        code = invites.make(ENV, PLAYIT, B2)
        for bad, words in (("hello", "isn't an invite"), (code[:40], "incomplete"),
                           ("SMP1.!!!", "incomplete")):
            with self.assertRaises(invites.InviteError) as ctx:
                invites.read(bad)
            self.assertIn(words, str(ctx.exception))
        with self.assertRaises(invites.InviteError):
            invites.make({"SERVER_NAME": "x"}, PLAYIT, B2)                        # no passwords
        with self.assertRaises(invites.InviteError):
            invites.make(ENV, PLAYIT, dict(B2, key=""))                          # no storage key

    def test_owner_can_make_one_from_the_command_line(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            env_path, playit_path = os.path.join(tmp, "controller.env"), os.path.join(tmp, "playit.toml")
            with open(env_path, "w") as f:
                f.write("# comment\n" + "\n".join(f"{k}={v}" for k, v in ENV.items()) + "\n")
            with open(playit_path, "w") as f:
                f.write(PLAYIT)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = invites.main(["make", "--env", env_path, "--playit", playit_path, "--bucket", "b",
                                     "--key-id", "k", "--key", "s"])
        self.assertEqual(code, 0)
        self.assertEqual(invites.read(out.getvalue())["storage"]["account"], "k")


class HomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Home(self.tmp.name)
        self.invite = invites.read(invites.make(ENV, PLAYIT, B2))

    def test_setting_up_from_an_invite(self):
        settings = self.home.apply_invite(self.invite, "keeeeo", total_gb=16)
        self.assertTrue(self.home.configured())
        self.assertEqual(self.home.settings()["SHARED_REMOTE"], "smp:my-world-4821")
        self.assertEqual((settings["HOST_NAME"], settings["MC_MEMORY"]), ("keeeeo", "6G"))
        with open(self.home.rclone_conf) as f:
            conf = f.read()
        self.assertIn("[smp]\ntype = b2\naccount = key-id\nkey = app-key", conf)
        with open(self.home.playit_secret) as f:
            self.assertEqual(f.read(), PLAYIT)
        for d in (self.home.server_dir, self.home.state_dir, self.home.backup_dir):
            self.assertTrue(os.path.isdir(d))

    def test_memory_is_lowered_on_a_small_pc(self):
        self.assertEqual(self.home.apply_invite(self.invite, "x", total_gb=8)["MC_MEMORY"], "5G")
        self.assertEqual(fit_memory("6G", 4), "2G")
        self.assertEqual(fit_memory("6G", 0), "6G")                   # unknown: keep what the owner set

    def test_setting_up_again_keeps_this_pcs_own_extras(self):
        self.home.apply_invite(self.invite, "keeeeo", total_gb=16)
        with open(self.home.settings_path, "a") as f:
            f.write("PANEL_PORT=18080\n")
        self.home.apply_invite(self.invite, "keeeeo", total_gb=16)
        self.assertEqual(self.home.settings()["PANEL_PORT"], "18080")
        self.assertEqual(self.home.panel_port(), 18080)

    def test_a_host_can_invite_the_next_one_with_their_own_key(self):
        self.home.apply_invite(self.invite, "keeeeo", total_gb=16)
        data = invites.read(self.home.make_invite_for("my-world-4821", "friend-id", "friend-key"))
        self.assertEqual((data["storage"]["account"], data["storage"]["key"]), ("friend-id", "friend-key"))
        self.assertEqual(data["env"]["PANEL_PASSWORD"], "panel-pw")
        self.assertEqual(data["playit"], PLAYIT)
        self.assertNotIn("HOST_NAME", data["env"])

    # -- the server's name, description and picture
    def test_the_face_starts_as_the_invites_name_and_description(self):
        self.home.apply_invite(self.invite, "keeeeo", total_gb=16)
        face = self.home.identity()
        self.assertEqual((face["name"], face["description"], face["icon_path"]), ("Test SMP", "Survival with friends", None))

    def test_saving_the_name_description_and_picture(self):
        from app import identity
        self.home.apply_invite(self.invite, "keeeeo", total_gb=16)
        before = self.home.identity_stamp()
        ident = self.home.save_identity("  My   Cool§c SMP ", "Hello §a world", png())
        self.assertEqual((ident["name"], ident["description"], ident["icon"]), ("My Cool SMP", "Hello world", True))
        face = self.home.identity()
        self.assertEqual((face["name"], face["description"]), ("My Cool SMP", "Hello world"))
        self.assertTrue(face["icon_path"].endswith("server-icon.png"))
        self.assertTrue(identity.icon_ok(identity.read_icon(self.home.server_dir)))
        self.assertEqual((self.home.settings()["SERVER_NAME"], self.home.settings()["SERVER_DESCRIPTION"]),
                         ("My Cool SMP", "Hello world"))
        self.assertNotEqual(self.home.identity_stamp(), before)                # the window notices and redraws
        self.assertEqual(self.home.save_identity("Renamed", "", "keep")["icon"], True)    # picture kept
        self.assertEqual(self.home.save_identity("Renamed", "", "remove")["icon"], False)
        self.assertIsNone(self.home.identity()["icon_path"])
        with self.assertRaises(ValueError):
            self.home.save_identity("   ", "x")
        with self.assertRaises(ValueError):
            self.home.save_identity("Name", "x", b"not a picture")

    def test_a_friend_invited_later_gets_the_current_name_not_the_old_one(self):
        self.home.apply_invite(self.invite, "keeeeo", total_gb=16)
        self.home.save_identity("Brand New Name", "Fresh description")
        data = invites.read(self.home.make_invite_for("my-world-4821", "friend-id", "friend-key"))
        self.assertEqual((data["env"]["SERVER_NAME"], data["env"]["SERVER_DESCRIPTION"]),
                         ("Brand New Name", "Fresh description"))

    def test_saving_shares_it_through_the_storage(self):
        from app import identity
        from app.shared import SharedWorld
        self.home.apply_invite(self.invite, "keeeeo", total_gb=16)
        cloud = os.path.join(self.tmp.name, "cloud")
        os.makedirs(cloud)
        world = SharedWorld(cloud, self.home.server_dir, "keeeeo")
        self.home.save_identity("Shared Name", "Shared text", png())
        self.home.sync_identity(world)
        self.assertEqual(world.read_identity()["name"], "Shared Name")
        self.assertTrue(identity.icon_ok(world.read_shared_icon()))
        other = Home(os.path.join(self.tmp.name, "friends-pc"))                # another PC picks it up
        other.apply_invite(self.invite, "friend", total_gb=16)
        other.sync_identity(SharedWorld(cloud, other.server_dir, "friend"))
        face = other.identity()
        self.assertEqual((face["name"], face["description"]), ("Shared Name", "Shared text"))
        self.assertTrue(face["icon_path"])

    def test_the_tunnel_is_only_used_when_the_invite_brought_one(self):
        agent = Tools(java="j", rclone="r", playitd=os.path.join(self.tmp.name, "playitd.exe"))
        self.home.apply_invite(invites.read(invites.make(ENV, "", B2)), "practice", total_gb=16)    # no tunnel identity
        self.assertFalse(self.home.uses_tunnel(agent))                    # (so another playit on this PC doesn't matter)
        self.home.apply_invite(self.invite, "keeeeo", total_gb=16)                                  # brings one
        self.assertTrue(self.home.uses_tunnel(agent))
        self.assertFalse(self.home.uses_tunnel(Tools(java="j", rclone="r", playitd="")))            # no agent bundled

    def test_controller_environment(self):
        self.home.apply_invite(self.invite, "keeeeo", total_gb=16)
        playitd = os.path.join(self.tmp.name, "playitd.exe")
        open(playitd, "w").close()
        env = self.home.controller_env(Tools(java="C:/jre/bin/java.exe", rclone="C:/tools/rclone.exe", playitd=playitd))
        self.assertEqual((env["BACKEND"], env["SERVER_DIR"], env["RCLONE_CONFIG"]),
                         ("process", self.home.server_dir, self.home.rclone_conf))
        self.assertEqual((env["JAVA_PATH"], env["RCLONE_EXE"]), ("C:/jre/bin/java.exe", "C:/tools/rclone.exe"))
        self.assertEqual((env["PLAYIT_EXE"], env["PLAYIT_SECRET_PATH"]), (playitd, self.home.playit_secret))
        self.assertEqual((env["LISTEN_HOST"], env["PANEL_HOST"]), ("127.0.0.1", "127.0.0.1"))
        self.assertEqual((env["SHARED_REMOTE"], env["HOST_NAME"]), ("smp:my-world-4821", "keeeeo"))
        env = self.home.controller_env(Tools(java="", rclone="", playitd=""))
        self.assertEqual(env["PLAYIT_EXE"], "")                        # no agent: the tunnel just isn't started

    def test_installing_the_server_downloads_fabric_and_accepts_the_eula(self):
        urls = []

        def opener(url):
            urls.append(url)
            if url.endswith("/loader"):
                return json.dumps([{"version": "0.20.0-beta", "stable": False}, {"version": "0.19.5", "stable": True}]).encode()
            if url.endswith("/installer"):
                return json.dumps([{"version": "1.1.0", "stable": True}]).encode()
            return b"JAR"
        self.home.install_server("26.3", True, opener=opener)
        self.assertTrue(urls[-1].endswith("/loader/26.3/0.19.5/1.1.0/server/jar"))
        with open(os.path.join(self.home.server_dir, "fabric-server-launch.jar"), "rb") as f:
            self.assertEqual(f.read(), b"JAR")
        with open(os.path.join(self.home.server_dir, "eula.txt")) as f:
            self.assertIn("eula=true", f.read())
        self.home.install_server("26.3", True, opener=lambda url: self.fail("downloaded twice"))


try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


@unittest.skipIf(Image is None, "Pillow is not installed")
class PictureMakerTests(unittest.TestCase):
    """Any picture becomes the 64x64 PNG that Minecraft shows next to the server name."""

    def make(self, img, **save):
        import io
        from app.desktop.icon import make_icon
        buf = io.BytesIO()
        img.save(buf, **save)
        buf.seek(0)
        return make_icon(buf)

    def test_a_wide_photo_is_cropped_to_a_square_and_made_64x64(self):
        from app import identity
        photo = Image.new("RGB", (640, 360), (200, 30, 30))
        data = self.make(photo, format="JPEG")
        self.assertTrue(identity.icon_ok(data))
        out = Image.open(__import__("io").BytesIO(data))
        self.assertEqual(out.size, (64, 64))

    def test_small_pixel_art_stays_sharp(self):
        art = Image.new("RGB", (16, 16), (255, 0, 0))
        for x in range(8, 16):
            for y in range(16):
                art.putpixel((x, y), (0, 0, 255))
        out = Image.open(__import__("io").BytesIO(self.make(art, format="PNG"))).convert("RGB")
        self.assertEqual((out.getpixel((0, 0)), out.getpixel((31, 10)), out.getpixel((32, 10)), out.getpixel((63, 63))),
                         ((255, 0, 0), (255, 0, 0), (0, 0, 255), (0, 0, 255)))      # no blur at the edge

    def test_things_that_are_not_pictures_get_a_plain_message(self):
        import io
        from app.desktop.icon import make_icon
        with self.assertRaises(ValueError) as ctx:
            make_icon(io.BytesIO(b"this is not a picture at all"))
        self.assertIn("doesn't look like a picture", str(ctx.exception))

    def test_a_detailed_photo_that_would_be_too_big_gets_fewer_colours(self):
        import random
        from app import identity
        rnd = random.Random(1)
        noise = Image.new("RGB", (64, 64))
        noise.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(64 * 64)])
        original, identity.MAX_ICON_BYTES = identity.MAX_ICON_BYTES, 9000
        try:
            data = self.make(noise, format="PNG")
        finally:
            identity.MAX_ICON_BYTES = original
        self.assertLessEqual(len(data), 9000)


class PlayitDaemonTests(unittest.TestCase):
    def test_runs_its_own_agent_with_the_secret_and_stops_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe, secret = os.path.join(tmp, "playitd.exe"), os.path.join(tmp, "playit.toml")
            for p in (exe, secret):
                open(p, "w").close()
            started = []

            class Proc:
                def __init__(self):
                    self.alive, self.terminated = True, False

                def poll(self):
                    return None if self.alive else 0

                def terminate(self):
                    self.alive, self.terminated = False, True

                def wait(self, timeout=None):
                    return 0

            def popen(cmd, **kwargs):
                started.append(cmd)
                return Proc()
            agent = PlayitDaemon(exe, secret, os.path.join(tmp, "playit.log"), popen=popen)
            self.assertEqual(agent.start(), "started")
            self.assertEqual(agent.start(), "already running")
            self.assertEqual(started[0][:5], [exe, "--secret-path", secret, "--socket-path", PlayitDaemon.PIPE])
            self.assertIn("--log-path", started[0])
            proc = agent.proc
            agent.stop()
            self.assertTrue(proc.terminated)
            self.assertFalse(agent.is_running())
            self.assertEqual(PlayitDaemon(exe, os.path.join(tmp, "missing.toml")).start(), "not installed")


class PanelClientTests(unittest.TestCase):
    """The app's client against the real panel API."""

    def setUp(self):
        from aiohttp import web
        from app.config import Config
        from app.manager import Manager
        from app.web import build_app

        class Runtime:
            def state(self, name):
                return "exited"
        self.quits = []
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Config(panel_password="panel-pw", state_dir=tmp.name, whitelist_path=os.path.join(tmp.name, "w.json"))
        app = build_app(cfg, Manager(cfg, Runtime()), on_quit=lambda: self.quits.append(True))
        self.port = free_port()
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        async def serve():
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", self.port).start()
            ready.set()
        threading.Thread(target=lambda: (self.loop.run_until_complete(serve()), self.loop.run_forever()),
                         daemon=True).start()
        ready.wait(10)
        self.addCleanup(lambda: self.loop.call_soon_threadsafe(self.loop.stop))

    def test_status_handover_and_quit(self):
        panel = Panel(self.port, "panel-pw")
        self.assertTrue(panel.listening())
        self.assertEqual(panel.status()["state"], "asleep")              # logged in by itself
        ok, why = panel.handover()
        self.assertFalse(ok)
        self.assertIn("isn't turned on", why)
        self.assertTrue(panel.quit())
        time.sleep(0.8)
        self.assertEqual(self.quits, [True])
        self.assertIsNone(Panel(self.port, "wrong").status())
        self.assertIsNone(Panel(free_port(), "panel-pw").status())        # nothing listening there

    def test_last_error_explains_why_the_controller_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "controller.log")
            with open(log, "w") as f:
                f.write("2026 ERROR main: old problem\n")
            start = log_size(log)
            with open(log, "a") as f:
                f.write("2026 INFO main: Taking the hosting lock\n2026 ERROR main: keeeeo is already hosting.\n")
            self.assertEqual(last_error(log, start), "keeeeo is already hosting.")


class SlowHandoverTests(unittest.TestCase):
    """Stopping saves and uploads the world, which can take minutes: the app must wait for the real answer."""

    def test_the_stop_request_waits_much_longer_than_a_status_request(self):
        from app.desktop import runner
        seen = []
        panel = Panel(1, "pw")
        panel._request = lambda method, path, body=None, timeout=None: (seen.append((path, timeout)) or (200, {}))
        panel.status()
        panel.handover()
        panel.quit()
        self.assertEqual(seen, [("/api/status", None), ("/api/handover", runner.LONG_WAIT), ("/api/quit", runner.LONG_WAIT)])
        self.assertGreaterEqual(runner.LONG_WAIT, 600)


class HandoverRaceTests(unittest.TestCase):
    def test_a_second_hand_over_and_a_second_upload_are_not_started(self):
        from app.hosting import Hosting
        from app.shared import HostingError

        class World:
            def __init__(self):
                self.pushes = 0

            def push(self):
                self.pushes += 1
                time.sleep(0.05)
                return 1, 10

            def release(self):
                pass

        class Manager:
            state, players, names = "asleep", 0, []

        async def go():
            world = World()
            hosting = Hosting(world, Manager())
            hosting.mark_started()
            await asyncio.gather(hosting.push_after_stop(), hosting.push_after_stop(), hosting.push_after_stop())
            first = world.pushes
            hosting.mark_started()
            results = await asyncio.gather(hosting.handover(), hosting.handover(), return_exceptions=True)
            return first, world.pushes, results
        first, total, results = asyncio.run(go())
        self.assertEqual(first, 1)                                    # three callers, one upload
        self.assertEqual(total, 2)                                    # the hand over uploaded once more, only once
        self.assertEqual(sum(1 for r in results if r == "handed over"), 1)
        self.assertEqual(sum(1 for r in results if isinstance(r, HostingError)), 1)


class FriendlyLogTests(unittest.TestCase):
    def test_log_lines_are_shortened_and_noise_is_dropped(self):
        from app.desktop.gui import friendly_log
        text = ("2026-10-17 16:46:10,027 INFO main: Taking the hosting lock\n"
                "2026-10-17 16:46:16,500 INFO aiohttp.access: 127.0.0.1 GET /api/status 200\n"
                "2026-10-17 16:46:16,600 INFO discord.client: logging in using static token\n"
                "2026-10-17 16:46:17,000 WARNING shared: Resuming the hosting lock\n"
                "2026-10-17 16:46:18,000 ERROR main: Not uploading: this PC's world has no level.dat\n"
                "Traceback (most recent call last):\n")
        self.assertEqual(friendly_log(text).splitlines(), [
            "16:46:10  Taking the hosting lock",
            "16:46:17  !  Resuming the hosting lock",
            "16:46:18  !! Not uploading: this PC's world has no level.dat",
            "          Traceback (most recent call last):"])
        self.assertEqual(friendly_log("2026-10-17 16:46:16,500 INFO aiohttp.access: GET\n"), "")


@unittest.skipUnless(shutil.which("rclone"), "rclone is not installed")
class RealControllerRunTests(unittest.TestCase):
    """Starts the real controller the way the app does, against a local folder standing in for the B2 bucket:
    it takes the lock and downloads the world; Stop hosting uploads, releases and the process ends."""

    def test_start_hosting_then_stop_hosting(self):
        from app.rclone import Rclone
        from app.shared import RcloneWorld
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bucket = os.path.join(tmp.name, "bucket")
        owner_server = os.path.join(tmp.name, "owner", "server")
        for rel, text in (("world/level.dat", "the real world"), ("world/players/data/a.dat", "items"),
                          ("EasyAuth/easyauth.db", "passwords"), ("server.properties", "motd=Test")):
            path = os.path.join(owner_server, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
        owner = RcloneWorld(bucket, owner_server, "MCboy23", Rclone("rclone"))
        owner.acquire(claim_wait=0)
        owner.push()
        owner.release()
        from app import identity, mcproto
        owner.write_identity(identity.make("Real Name", "Hello there", True, 1000), png())    # the owner's server info

        home = Home(os.path.join(tmp.name, "friend"))
        code = invites.make(ENV, "", {"type": "remote", "remote": bucket})
        home.apply_invite(invites.read(code), "keeeeo", total_gb=16)
        panel_port, game_port = free_port(), free_port()
        with open(home.settings_path, "a") as f:
            f.write(f"PANEL_PORT={panel_port}\nLISTEN_PORT={game_port}\nCLAIM_WAIT=0\nDISCORD_TOKEN=\n")
        controller = Controller(home, Tools(java="java", rclone=shutil.which("rclone"), playitd=""))
        proc = controller.start()
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        panel = Panel(panel_port, "panel-pw")
        deadline = time.time() + 60
        while not panel.listening() and time.time() < deadline and proc.poll() is None:
            time.sleep(0.5)
        self.assertTrue(panel.listening(), f"controller didn't come up; log:\n{self.log(home)}")
        self.assertEqual(panel.status()["state"], "asleep")
        state = owner.read_state()
        self.assertEqual((state["status"], state["host"]), ("hosting", "keeeeo"))
        with open(os.path.join(home.server_dir, "world", "players", "data", "a.dat")) as f:
            self.assertEqual(f.read(), "items")                              # the world was pulled in
        # the friend's controller already shows the owner's name, description and picture in the server list
        status = asyncio.run(mcproto.ping_server("127.0.0.1", game_port))
        self.assertEqual(status["description"]["text"], "§lReal Name§r\n§eAsleep §7- Hello there")
        self.assertTrue(status["favicon"].startswith("data:image/png;base64,"))
        self.assertEqual(identity.read_local(home.server_dir)["name"], "Real Name")
        ok, why = panel.handover()
        self.assertTrue(ok, why)
        proc.wait(60)
        state = owner.read_state()
        self.assertEqual((state["status"], state["lastHost"]), ("idle", "keeeeo"))

    @staticmethod
    def log(home):
        try:
            with open(home.log_path, encoding="utf-8", errors="replace") as f:
                return f.read()[-3000:]
        except OSError:
            return "(no log)"


if __name__ == "__main__":
    unittest.main()
