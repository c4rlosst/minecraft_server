"""Glue between the shared world (shared.py) and the controller: heartbeat, upload after each sleep, handover."""
import asyncio
import logging

from . import shared as shared_module
from .manager import PlayersOnline

log = logging.getLogger("hosting")


class Hosting:
    def __init__(self, shared, manager):
        self.shared, self.manager = shared, manager
        self.exit_event = asyncio.Event()      # set when the controller should quit (after a handover)
        self.dirty = False                     # server ran since the last upload

    async def heartbeat_loop(self):
        while True:
            await asyncio.sleep(60)
            try:
                await asyncio.to_thread(self.shared.heartbeat)
            except Exception:
                log.exception("heartbeat failed")

    def mark_started(self):
        self.dirty = True

    async def push_after_stop(self):
        """Runs once the Minecraft server has fully stopped."""
        count, nbytes = await asyncio.to_thread(self.shared.push)
        self.dirty = False
        log.info("World uploaded to the shared folder (%s files, %.1f MB)", count, nbytes / 1048576)

    async def handover(self) -> str:
        """Stop the server if nobody is on it, upload the world, release the lock and quit the controller."""
        m = self.manager
        if m.players > 0:
            raise PlayersOnline(m.players, list(m.names))
        if m.state in ("online", "starting"):
            await m.stop()
        for _ in range(180):                    # wait up to 3 minutes for the stop + upload
            if m.state == "asleep":
                break
            await asyncio.sleep(1)
        else:
            raise shared_module.HostingError("The server didn't stop in time; try again in a minute.")
        if self.dirty:                          # nothing uploaded yet this session (e.g. after a manual stop)
            await self.push_after_stop()
        await asyncio.to_thread(self.shared.release)
        log.info("Handed over hosting; the controller will now quit.")
        asyncio.get_running_loop().call_later(1.5, self.exit_event.set)   # let the HTTP/Discord reply go out first
        return "handed over"
