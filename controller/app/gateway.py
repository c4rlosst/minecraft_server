"""Public Minecraft port. Relays traffic to the real server when it is up; otherwise answers the server list
itself and wakes the server when a whitelisted player tries to join."""
import asyncio
import logging

from . import mcproto as proto

log = logging.getLogger("gateway")

MOTD = {
    "asleep": "§eAsleep §7- join to wake it up",
    "starting": "§6Starting up... §7try again in a minute",
    "stopping": "§6Shutting down...",
    "error": "§cServer unavailable",
}


class Gateway:
    def __init__(self, cfg, manager, max_pending: int = 256):
        self.cfg, self.manager = cfg, manager
        self._pending = 0
        self._max_pending = max_pending

    async def handle(self, reader, writer):
        try:
            await self._handle(reader, writer)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, proto.ProtocolError, ConnectionError, OSError):
            pass
        except Exception:
            log.exception("connection handler failed")
        finally:
            writer.close()

    async def _handle(self, reader, writer):
        if self._pending >= self._max_pending:
            return
        raw = bytearray()
        self._pending += 1
        try:
            packet_id, data = await asyncio.wait_for(proto.read_packet(reader, raw), 10)
            if packet_id != 0x00:
                return
            hs = proto.parse_handshake(data)
        finally:
            self._pending -= 1

        if self.manager.state == "online" and await self._relay(reader, writer, raw):
            return
        if hs["next_state"] == 1:
            await self._status(reader, writer, hs)
        elif hs["next_state"] in (2, 3):
            await self._login(reader, writer)

    async def _relay(self, reader, writer, raw) -> bool:
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(self.cfg.mc_host, self.cfg.mc_port), 3)
        except (OSError, asyncio.TimeoutError):
            return False
        up_writer.write(bytes(raw))
        await up_writer.drain()

        async def pipe(src, dst):
            try:
                while chunk := await src.read(65536):
                    dst.write(chunk)
                    await dst.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                dst.close()

        tasks = [asyncio.create_task(pipe(reader, up_writer)), asyncio.create_task(pipe(up_reader, writer))]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in tasks:
            task.cancel()
        up_writer.close()
        return True

    async def _status(self, reader, writer, hs):
        raw = bytearray()
        for _ in range(2):  # status request, then optional ping
            packet_id, data = await asyncio.wait_for(proto.read_packet(reader, raw, 64), 5)
            if packet_id == 0x00:
                state = self.manager.state
                writer.write(proto.status_response(
                    hs["protocol"], state.capitalize(), MOTD.get(state, MOTD["error"]),
                    self.manager.players, self.manager.max_players))
            elif packet_id == 0x01:
                writer.write(proto.frame(0x01, data))
                await writer.drain()
                return
            await writer.drain()

    async def _login(self, reader, writer):
        packet_id, data = await asyncio.wait_for(proto.read_packet(reader, bytearray(), 512), 10)
        if packet_id != 0x00:
            return
        name = proto.parse_login_name(data)
        state = self.manager.state
        if name.lower() not in self.manager.allowed_names():
            message, color = "You are not whitelisted on this server.", "red"
        elif state == "asleep":
            try:
                await self.manager.start()
                log.info("%s woke the server", name)
                message, color = "Waking the server up! Reconnect in about a minute.", "yellow"
            except Exception:
                log.exception("could not start the server")
                message, color = "The server failed to start. Tell the admin.", "red"
        elif state == "starting":
            message, color = "The server is starting up. Reconnect in a minute.", "yellow"
        elif state == "stopping":
            message, color = "The server is shutting down. Try again in a minute.", "yellow"
        else:
            message, color = "The server isn't reachable right now. Try again shortly.", "red"
        writer.write(proto.login_disconnect(message, color))
        await writer.drain()
