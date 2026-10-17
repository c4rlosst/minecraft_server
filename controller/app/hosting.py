"""Glue between the shared world (shared.py) and the controller: heartbeat, upload after each sleep, handover."""
import asyncio
import logging

from . import shared as shared_module
from .manager import PlayersOnline, StartingUp

log = logging.getLogger("hosting")


class Hosting:
    def __init__(self, shared, manager):
        self.shared, self.manager = shared, manager
        self.exit_event = asyncio.Event()      # set when the controller should quit (after a handover)
        self.dirty = False                     # server ran since the last upload
        self._handing_over = False             # a second click on Stop must not start a second hand over
        self._push_lock = asyncio.Lock()       # never two uploads at once

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
        """Runs once the Minecraft server has fully stopped. One upload at a time: if another caller (the automatic
        upload after a stop, a hand over) finished it while this one waited, there is nothing left to send."""
        async with self._push_lock:
            if not self.dirty:
                return
            count, nbytes = await asyncio.to_thread(self.shared.push)
            self.dirty = False
            log.info("World uploaded to the shared folder (%s files, %.1f MB)", count, nbytes / 1048576)

    async def handover(self) -> str:
        """Stop the server if nobody is on it, upload the world, release the lock and quit the controller."""
        if self._handing_over:
            raise shared_module.HostingError("Already saving and handing over; wait for it to finish.")
        m = self.manager
        if m.players > 0:
            raise PlayersOnline(m.players, list(m.names))
        self._handing_over = True
        try:
            return await self._hand_over(m)
        finally:
            self._handing_over = False

    async def _hand_over(self, m) -> str:
        if m.state in ("online", "starting"):
            try:
                await m.stop()
            except StartingUp as exc:
                raise shared_module.HostingError(exc.message().replace("protected from being stopped", "too new to hand over")
                                                 + " Try again shortly.")
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
