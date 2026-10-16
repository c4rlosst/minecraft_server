"""Run from cloud/controller:  python -m unittest discover -s tests -v
Uses only the standard library: real sockets on localhost, fake Docker, fake clock."""
import asyncio
import json
import os
import struct
import sys
import tempfile
import unittest
import zipfile

from app import mcproto as proto
from app.backups import make_backup
from app.config import Config
from app.procctl import ProcessServer
from app.dockerctl import _demux
from app.gateway import Gateway
from app.manager import Manager, StartingUp
from app.offline import offline_uuid
from app.rcon import rcon

PROTOCOL = 800


def run(coro):
    return asyncio.run(coro)


async def read_frame(reader):
    raw = bytearray()
    return await proto.read_packet(reader, raw, max_len=1 << 20)


def handshake(next_state, protocol=PROTOCOL, host="example.com", port=25565):
    return proto.frame(0, proto.encode_varint(protocol) + proto.encode_string(host)
                       + struct.pack(">H", port) + proto.encode_varint(next_state))


class StubManager:
    def __init__(self, state="asleep", allowed=("steve",)):
        self.state, self.players, self.max_players = state, 0, 10
        self.allowed, self.starts = set(allowed), 0

    def allowed_names(self):
        return self.allowed

    def is_allowed(self, name):
        return name.lower() in self.allowed

    async def start(self):
        self.starts += 1
        self.state = "starting"
        return True


class Served:
    """Starts a gateway on an ephemeral port."""
    def __init__(self, manager, cfg=None):
        self.gateway = Gateway(cfg or Config(), manager)

    async def __aenter__(self):
        self.server = await asyncio.start_server(self.gateway.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()


class ProtocolTests(unittest.TestCase):
    def test_varint_roundtrip(self):
        for value in (0, 1, 127, 128, 255, 25565, 2 ** 31 - 1, -1):
            encoded = proto.encode_varint(value)
            self.assertEqual(proto.decode_varint(encoded), (value, len(encoded)))

    def test_handshake_parse(self):
        packet = handshake(2)
        length, off = proto.decode_varint(packet)
        _, off2 = proto.decode_varint(packet, off)  # packet id
        info = proto.parse_handshake(packet[off2:])
        self.assertEqual((info["protocol"], info["host"], info["port"], info["next_state"]),
                         (PROTOCOL, "example.com", 25565, 2))

    def test_demux(self):
        data = b"\x01\x00\x00\x00\x00\x00\x00\x03abc" + b"\x02\x00\x00\x00\x00\x00\x00\x02hi"
        self.assertEqual(_demux(data), "abchi")
        self.assertEqual(_demux(b"plain tty text"), "plain tty text")


class GatewayTests(unittest.TestCase):
    def test_status_when_asleep(self):
        async def go():
            async with Served(StubManager("asleep")) as s:
                reader, writer = await asyncio.open_connection("127.0.0.1", s.port)
                writer.write(handshake(1) + proto.frame(0) + proto.frame(1, struct.pack(">q", 4242)))
                pid, data = await read_frame(reader)
                body = json.loads(proto.decode_string(data, 0, 1 << 20)[0])
                pid2, data2 = await read_frame(reader)
                writer.close()
                return pid, body, pid2, data2
        pid, body, pid2, data2 = run(go())
        self.assertEqual(pid, 0)
        self.assertIn("Asleep", body["description"]["text"])
        self.assertEqual(body["version"]["protocol"], PROTOCOL)  # echoes the client's protocol
        self.assertEqual((pid2, struct.unpack(">q", data2)[0]), (1, 4242))

    def login(self, port, name):
        async def go():
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(handshake(2) + proto.frame(0, proto.encode_string(name) + b"\x00" * 16))
            pid, data = await read_frame(reader)
            writer.close()
            return pid, json.loads(proto.decode_string(data, 0, 4096)[0])["text"]
        return go()

    def test_whitelisted_join_wakes_server_once(self):
        async def go():
            manager = StubManager("asleep")
            async with Served(manager) as s:
                first = await self.login(s.port, "Steve")
                second = await self.login(s.port, "Steve")
            return manager, first, second
        manager, first, second = run(go())
        self.assertEqual(first[0], 0)
        self.assertIn("Waking", first[1])
        self.assertIn("starting", second[1])
        self.assertEqual(manager.starts, 1)

    def test_old_and_new_client_protocols_get_status_and_wake_message(self):
        # 1.7.x=5, 1.8.x=47, 1.12.2=340, 1.20.4=765, 26.3 (this repo's tests use 800)
        async def one(port, protocol):
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(handshake(1, protocol) + proto.frame(0))
            _, data = await read_frame(r)
            status = json.loads(proto.decode_string(data, 0, 1 << 20)[0])
            w.close()
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(handshake(2, protocol) + proto.frame(0, proto.encode_string("Steve")))  # 1.8 login: name only
            _, data = await read_frame(r)
            text = json.loads(proto.decode_string(data, 0, 4096)[0])["text"]
            w.close()
            return status["version"]["protocol"], text

        async def go():
            manager = StubManager("asleep")
            async with Served(manager) as s:
                results = {p: await one(s.port, p) for p in (5, 47, 340, 765, 800)}
            return manager, results
        manager, results = run(go())
        for protocol, (echoed, text) in results.items():
            self.assertEqual(echoed, protocol)
            self.assertTrue("Waking" in text or "starting" in text)
        self.assertEqual(manager.starts, 1)

    def test_unlisted_join_does_not_wake(self):
        async def go():
            manager = StubManager("asleep")
            async with Served(manager) as s:
                result = await self.login(s.port, "Stranger")
            return manager, result
        manager, result = run(go())
        self.assertIn("not whitelisted", result[1])
        self.assertEqual(manager.starts, 0)

    def test_online_traffic_is_relayed_byte_for_byte(self):
        async def go():
            received = bytearray()

            async def upstream(reader, writer):
                received.extend(await reader.readexactly(len(sent)))
                writer.write(b"hello from mc")
                await writer.drain()
                writer.close()

            fake = await asyncio.start_server(upstream, "127.0.0.1", 0)
            fake_port = fake.sockets[0].getsockname()[1]
            login_start = proto.frame(0, proto.encode_string("Steve") + b"\x00" * 16)
            sent = handshake(2) + login_start
            cfg = Config(mc_host="127.0.0.1", mc_port=fake_port)
            async with Served(StubManager("online"), cfg) as s:
                reader, writer = await asyncio.open_connection("127.0.0.1", s.port)
                writer.write(sent)
                reply = await asyncio.wait_for(reader.read(100), 5)
                writer.close()
            fake.close()
            return bytes(received), sent, reply
        received, sent, reply = run(go())
        self.assertEqual(received, sent)
        self.assertEqual(reply, b"hello from mc")


async def fake_status_server(players=2, maximum=10):
    async def handler(reader, writer):
        try:
            await read_frame(reader)   # handshake
            await read_frame(reader)   # status request
            body = {"version": {"name": "26.3", "protocol": PROTOCOL},
                    "players": {"online": players, "max": maximum, "sample": [{"name": "Steve", "id": "x"}]}}
            writer.write(proto.frame(0, proto.encode_string(json.dumps(body))))
            await writer.drain()
        finally:
            writer.close()
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


class PingAndRconTests(unittest.TestCase):
    def test_ping_server(self):
        async def go():
            server, port = await fake_status_server()
            info = await proto.ping_server("127.0.0.1", port)
            server.close()
            return info
        info = run(go())
        self.assertEqual(info["players"]["online"], 2)

    def test_ping_refused_raises(self):
        async def go():
            server, port = await fake_status_server()
            server.close()
            await server.wait_closed()
            await proto.ping_server("127.0.0.1", port, 1)
        with self.assertRaises(Exception):
            run(go())

    def test_rcon(self):
        async def go():
            commands = []

            async def handler(reader, writer):
                async def recv():
                    (length,) = struct.unpack("<i", await reader.readexactly(4))
                    data = await reader.readexactly(length)
                    return struct.unpack("<ii", data[:8]) + (data[8:-2].decode(),)

                def send(rid, kind, body):
                    writer.write(struct.pack("<iii", 10 + len(body), rid, kind) + body.encode() + b"\0\0")

                rid, kind, body = await recv()
                send(rid if body == "secret" else -1, 2, "")
                if body == "secret":
                    rid, kind, body = await recv()
                    commands.append(body)
                    send(rid, 0, "Added Steve to the whitelist")
                await writer.drain()
                writer.close()

            server = await asyncio.start_server(handler, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            reply = await rcon("127.0.0.1", port, "secret", "whitelist add Steve")
            try:
                await rcon("127.0.0.1", port, "wrong", "list")
                bad = None
            except RuntimeError as exc:
                bad = str(exc)
            server.close()
            return reply, commands, bad
        reply, commands, bad = run(go())
        self.assertEqual(reply, "Added Steve to the whitelist")
        self.assertEqual(commands, ["whitelist add Steve"])
        self.assertIn("authentication failed", bad)


class FakeDocker:
    def __init__(self):
        self.status, self.calls = "exited", []

    def state(self, name):
        return self.status

    def start(self, name):
        self.calls.append("start")
        self.status = "running"

    def stop(self, name, grace=120):
        self.calls.append("stop")
        self.status = "exited"


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config(state_dir=self.tmp.name, idle_minutes=10, rcon_password="pw",
                          whitelist_path=os.path.join(self.tmp.name, "whitelist.json"))
        self.now = 1000.0
        self.docker = FakeDocker()
        self.info = None          # None = server not accepting connections yet
        self.rcon_calls = []

        async def ping(host, port, timeout):
            if self.info is None:
                raise ConnectionRefusedError
            return self.info

        async def fake_rcon(host, port, password, command, timeout=5.0):
            self.rcon_calls.append(command)
            return "ok"

        self.manager = Manager(self.cfg, self.docker, ping=ping, rcon=fake_rcon, clock=lambda: self.now)

    def test_wake_play_idle_sleep_cycle(self):
        async def go():
            m = self.manager
            await m.refresh()
            states = [m.state]                                   # asleep
            await m.start()
            states.append(m.state)                               # starting
            await m.refresh()
            states.append(m.state)                               # container up, port not open yet
            self.info = {"players": {"online": 1, "max": 10}, "version": {"name": "26.3"}}
            await m.refresh()
            states.append(m.state)                               # online with a player
            self.now += 3600
            await m.refresh()
            states.append(m.state)                               # still online: player present
            self.info["players"]["online"] = 0
            await m.refresh()                                    # idle clock starts from last seen player
            self.now += 9 * 60
            await m.refresh()
            states.append(m.state)                               # 9 min idle: still awake
            self.now += 2 * 60
            await m.refresh()
            states.append(m.state)                               # 11 min idle: stopping
            await m._stop_task
            states.append(m.state)                               # asleep again
            return states
        states = run(go())
        self.assertEqual(states, ["asleep", "starting", "starting", "online", "online", "online", "stopping", "asleep"])
        self.assertEqual(self.docker.calls, ["start", "stop"])

    def test_nobody_joins_after_wake_goes_back_to_sleep(self):
        async def go():
            m = self.manager
            await m.start()
            self.info = {"players": {"online": 0, "max": 10}}
            await m.refresh()
            self.now += 11 * 60
            await m.refresh()
            await m._stop_task
            return m.state
        self.assertEqual(run(go()), "asleep")

    # ---- startup protection (START_GRACE_MINUTES)
    def test_a_fresh_start_cannot_be_stopped_until_the_grace_time_is_up(self):
        async def go():
            m = self.manager
            await m.start()
            self.info = {"players": {"online": 0, "max": 10}}
            await m.refresh()                                  # online, 0 players, started 0 s ago
            with self.assertRaises(StartingUp) as early:
                await m.stop()
            self.now += 100
            with self.assertRaises(StartingUp) as later:
                await m.stop()
            state_after_refusals = m.state
            self.now += 81                                     # 181 s: past 3 minutes
            stopped = await m.stop()
            await m._stop_task
            return early.exception, later.exception, state_after_refusals, stopped
        early, later, state, stopped = run(go())
        self.assertEqual((early.seconds_left, later.seconds_left), (180, 80))
        self.assertIn("1 min 20 s", later.message())
        self.assertEqual(state, "online")                      # the refusals left it running
        self.assertTrue(stopped)

    def test_force_overrides_the_startup_protection(self):
        async def go():
            await self.manager.start()
            return await self.manager.stop(force=True)
        self.assertTrue(run(go()))

    def test_idle_sleep_also_waits_for_the_grace_time(self):
        self.cfg.idle_minutes = 1                              # would sleep after 1 minute...
        self.cfg.start_grace_minutes = 3                       # ...but not before 3 minutes after the start

        async def go():
            m = self.manager
            await m.start()
            self.info = {"players": {"online": 0, "max": 10}}
            await m.refresh()
            self.now += 120
            await m.refresh()
            still_awake = m.state
            self.now += 61                                     # 181 s since the start
            await m.refresh()
            await m._stop_task
            return still_awake, m.state
        self.assertEqual(run(go()), ("online", "asleep"))

    def test_grace_can_be_switched_off_and_does_not_apply_to_an_adopted_server(self):
        async def go():
            self.cfg.start_grace_minutes = 0
            await self.manager.start()
            self.assertTrue(await self.manager.stop())         # off: stop works immediately
            await self.manager._stop_task
        run(go())

        self.cfg.start_grace_minutes = 3
        self.docker.status = "running"                         # a server that was already running when the controller started
        self.info = {"players": {"online": 0, "max": 10}}
        manager = Manager(self.cfg, self.docker, ping=self.manager.ping, rcon=self.manager.rcon, clock=lambda: self.now)

        async def adopted():
            await manager.refresh()
            self.assertEqual(manager.grace_left(), 0)
            return await manager.stop()
        self.assertTrue(run(adopted()))

    def test_whitelist_queue_persists_and_applies_on_boot(self):
        async def go():
            m = self.manager
            result = await m.whitelist_change("add", "Alex")
            allowed_while_asleep = m.allowed_names()
            reloaded = Manager(self.cfg, self.docker).pending
            await m.start()
            self.info = {"players": {"online": 0, "max": 10}}
            await m.refresh()
            return result, allowed_while_asleep, reloaded, m.pending
        result, allowed, reloaded, pending = run(go())
        self.assertEqual(result, "queued")
        self.assertIn("alex", allowed)
        self.assertEqual(reloaded["add"], ["Alex"])
        self.assertEqual(self.rcon_calls, ["whitelist add Alex"])
        self.assertEqual(pending, {"add": [], "remove": []})

    def test_whitelisting_a_nonexistent_account_reports_an_error_when_online(self):
        async def fake_rcon(host, port, password, command, timeout=5.0):
            return "That player does not exist"
        self.manager.rcon = fake_rcon

        async def go():
            await self.manager.start()
            self.info = {"players": {"online": 0, "max": 10}}
            await self.manager.refresh()
            await self.manager.whitelist_change("add", "NotARealPerson")
        with self.assertRaises(ValueError) as ctx:
            run(go())
        self.assertIn("real Minecraft account", str(ctx.exception))

    def test_whitelist_rejects_bad_names(self):
        with self.assertRaises(ValueError):
            run(self.manager.whitelist_change("add", "bad name; op me"))

    def test_whitelist_file_is_read(self):
        with open(self.cfg.whitelist_path, "w") as f:
            json.dump([{"uuid": "1", "name": "Steve"}], f)
        self.assertEqual(self.manager.allowed_names(), {"steve"})

    def test_ops_applied_once_when_server_comes_up_and_bad_names_skipped(self):
        self.cfg.ops = "Alex, bad name;x"

        async def go():
            await self.manager.start()
            self.info = {"players": {"online": 0, "max": 10}}
            await self.manager.refresh()
            await self.manager.refresh()
        run(go())
        self.assertEqual(self.rcon_calls, ["op Alex"])

    def test_state_changes_are_announced_and_backup_runs_after_stop(self):
        events, backups = [], []

        async def on_change(old, new):
            events.append((old, new))

        async def after_stop():
            backups.append(self.docker.status)   # server must already be stopped when the backup runs

        async def go():
            m = self.manager
            m.on_change, m.after_stop = on_change, after_stop
            await m.start()
            self.info = {"players": {"online": 0, "max": 10}}
            await m.refresh()
            self.now += 4 * 60       # past the 3-minute startup protection
            await m.stop()
            await m._stop_task
            await asyncio.sleep(0)   # let notification tasks run
        run(go())
        self.assertEqual(events, [("asleep", "starting"), ("starting", "online"),
                                  ("online", "stopping"), ("stopping", "asleep")])
        self.assertEqual(backups, ["exited"])


class OfflineModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server_dir = self.tmp.name
        self.cfg = Config(state_dir=os.path.join(self.tmp.name, "state"), account_mode="offline", ops="MCboy23, bad name",
                          rcon_password="pw", whitelist_path=os.path.join(self.tmp.name, "whitelist.json"))
        self.docker, self.rcon_calls, self.info = FakeDocker(), [], None

        async def ping(host, port, timeout):
            if self.info is None:
                raise ConnectionRefusedError
            return self.info

        async def fake_rcon(host, port, password, command, timeout=5.0):
            self.rcon_calls.append(command)
            return "ok"
        self.manager = Manager(self.cfg, self.docker, ping=ping, rcon=fake_rcon)

    def test_offline_uuid_matches_the_real_server(self):
        # value taken from a real server's whitelist.json for this name
        self.assertEqual(offline_uuid("hypernachoishere"), "99de81ea-322b-32b8-9436-b12e1557922a")

    def test_everyone_may_join_and_whitelist_is_off(self):
        self.assertFalse(self.manager.whitelist_on)
        self.assertTrue(self.manager.is_allowed("AnyoneAtAll"))
        self.assertFalse(self.manager.snapshot()["whitelist"])
        with self.assertRaises(ValueError) as ctx:
            run(self.manager.whitelist_change("add", "Alex"))
        self.assertIn("offline mode", str(ctx.exception))

    def test_ops_written_with_offline_uuid_before_start_and_stale_uuid_repaired(self):
        ops_path = os.path.join(self.server_dir, "ops.json")
        with open(ops_path, "w") as f:   # what the old online-mode /op command left behind
            json.dump([{"uuid": "86b86a44-fec9-40e9-82fa-5c3003918588", "name": "MCboy23", "level": 4,
                        "bypassesPlayerLimit": False}, {"uuid": "x", "name": "Other", "level": 2}], f)

        async def go():
            await self.manager.start()
            self.info = {"players": {"online": 0, "max": 10}}
            await self.manager.refresh()
        run(go())
        with open(ops_path) as f:
            ops = {e["name"]: e for e in json.load(f)}
        self.assertEqual(ops["MCboy23"]["uuid"], offline_uuid("MCboy23"))
        self.assertEqual(ops["MCboy23"]["level"], 4)
        self.assertEqual(ops["Other"]["level"], 2)               # other operators are kept
        self.assertNotIn("bad name", ops)
        self.assertEqual(self.rcon_calls, [])                    # no /op command: it would store the online UUID

    def test_online_mode_still_uses_whitelist(self):
        cfg = Config(state_dir=self.cfg.state_dir, whitelist_path=self.cfg.whitelist_path)
        manager = Manager(cfg, self.docker)
        self.assertTrue(manager.whitelist_on)
        self.assertFalse(manager.is_allowed("Stranger"))


class FakeJava(ProcessServer):
    def _command(self):
        return [sys.executable, "-c", "import time; time.sleep(120)"]


class ProcessServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config(state_dir=os.path.join(self.tmp.name, "state"), server_dir=os.path.join(self.tmp.name, "server"),
                          mc_host="127.0.0.1", rcon_port=1, rcon_password="x")  # port 1: nothing listens
        os.makedirs(self.cfg.server_dir)
        self.server = FakeJava(self.cfg)
        self.addCleanup(lambda: self.server.stop(grace=1))

    def test_start_stop_lifecycle_and_pid_file(self):
        self.assertEqual(self.server.state(), "exited")
        self.server.start()
        self.assertEqual(self.server.state(), "running")
        self.server.start()                       # starting twice must not spawn a second process
        self.server.stop(grace=30)                # RCON is unreachable, so it falls back to killing
        self.assertEqual(self.server.state(), "exited")
        self.assertFalse(os.path.exists(self.server.pid_file))

    def test_stale_pid_file_means_exited(self):
        os.makedirs(self.cfg.state_dir)
        with open(self.server.pid_file, "w") as f:
            f.write("999999")
        self.assertEqual(self.server.state(), "exited")

    def test_logs_tail(self):
        os.makedirs(os.path.join(self.cfg.server_dir, "logs"))
        with open(os.path.join(self.cfg.server_dir, "logs", "latest.log"), "w") as f:
            f.write("\n".join(f"line {i}" for i in range(500)))
        self.assertEqual(self.server.logs(tail=3).splitlines(), ["line 497", "line 498", "line 499"])
        self.assertIn("no log yet", ProcessServer(Config(server_dir=self.tmp.name + "x")).logs())

    def test_missing_jar_gives_clear_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            ProcessServer(self.cfg).start()
        self.assertIn("setup.ps1", str(ctx.exception))


class BackupTests(unittest.TestCase):
    def test_zip_and_prune(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, backups = os.path.join(tmp, "server"), os.path.join(tmp, "backups")
            os.makedirs(os.path.join(server, "world", "region"))
            with open(os.path.join(server, "world", "level.dat"), "w") as f:
                f.write("data")
            os.makedirs(backups)
            for day in ("2020-01-01", "2020-01-02", "2020-01-03"):
                open(os.path.join(backups, f"world-{day}_00-00-00.zip"), "w").close()
            archive = make_backup(server, backups, keep=2)
            self.assertTrue(zipfile.is_zipfile(archive))
            with zipfile.ZipFile(archive) as z:
                self.assertIn("level.dat", z.namelist())
            self.assertEqual(len(os.listdir(backups)), 2)
            self.assertIn(os.path.basename(archive), os.listdir(backups))

    def test_no_world_means_no_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(make_backup(tmp, os.path.join(tmp, "b")))


if __name__ == "__main__":
    unittest.main()
