"""Run from cloud/controller:  python -m unittest discover -s tests -v
Uses only the standard library: real sockets on localhost, fake Docker, fake clock."""
import asyncio
import json
import os
import struct
import tempfile
import unittest

from app import mcproto as proto
from app.config import Config
from app.dockerctl import _demux
from app.gateway import Gateway
from app.manager import Manager
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

    def test_whitelist_rejects_bad_names(self):
        with self.assertRaises(ValueError):
            run(self.manager.whitelist_change("add", "bad name; op me"))

    def test_whitelist_file_is_read(self):
        with open(self.cfg.whitelist_path, "w") as f:
            json.dump([{"uuid": "1", "name": "Steve"}], f)
        self.assertEqual(self.manager.allowed_names(), {"steve"})


if __name__ == "__main__":
    unittest.main()
