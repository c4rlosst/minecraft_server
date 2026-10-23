"""Glue between the shared world (shared.py) and the controller: heartbeat, upload after each sleep, handover."""
import asyncio
import logging
import time

from . import shared as shared_module
from .manager import PlayersOnline, StartingUp
from .progress import NullProgress

log = logging.getLogger("hosting")


async def run_after_stop(backup, hosting):
    """The server has just stopped. The local backup (a zip) and the upload to the shared storage both only READ the stopped
    world, so they run together: the hand over no longer waits for the zip before the upload starts. A failure in one never
    stops the other (the world is safe on this PC either way, and the next upload tries again)."""
    async def upload():
        try:
            await hosting.push_after_stop()
        except shared_module.HostingError as exc:
            log.error("The world was NOT uploaded to the shared storage: %s", exc)

    jobs = []
    if backup:
        jobs.append(backup())
    if hosting:
        jobs.append(upload())
    for result in await asyncio.gather(*jobs, return_exceptions=True):
        if isinstance(result, BaseException):
            log.error("after-stop task failed: %r", result, exc_info=result)


class Hosting:
    def __init__(self, shared, manager):
        self.shared, self.manager = shared, manager
        self.exit_event = asyncio.Event()      # set when the controller should quit (after a handover)
        self.dirty = False                     # server ran since the last upload
        self._handing_over = False             # a second click on Stop must not start a second hand over
        self._push_lock = asyncio.Lock()       # never two uploads at once
        self._last_checkpoint = None           # when the world was last saved to the shared storage while running
        self.snapshot_dir = None

    async def heartbeat_loop(self):
        while True:
            await asyncio.sleep(shared_module.BEAT_SECONDS)
            try:
                await asyncio.to_thread(self.shared.heartbeat)
            except Exception:
                log.exception("heartbeat failed")

    # -- checkpoints: a crash costs minutes, not the whole session ------------------------------------------
    async def checkpoint_loop(self, minutes, snapshot_dir):
        """While the server runs, save the world to the shared storage every few minutes. A host whose PC dies then loses
        only what happened since the last checkpoint: the next host starts from it."""
        self.snapshot_dir = snapshot_dir
        self._last_checkpoint = time.monotonic()
        while True:
            await asyncio.sleep(60)
            if time.monotonic() - self._last_checkpoint >= minutes * 60:
                try:
                    await self.checkpoint_now(minutes)
                except Exception:
                    log.exception("checkpoint failed")
                    self._last_checkpoint = time.monotonic()      # try again after a full interval, not every minute

    async def checkpoint_now(self, minutes=20) -> str:
        """One checkpoint. Returns 'saved', 'idle' (nobody played), 'busy', 'unsupported' or 'failed'."""
        m = self.manager
        if not getattr(self.shared, "supports_checkpoints", False) or not self.snapshot_dir:
            return "unsupported"
        if m.state != "online" or self._handing_over or self._push_lock.locked():
            return "busy"
        recently_played = m.players > 0 or (m.last_active is not None and m.clock() - m.last_active < minutes * 60 + 60)
        if not recently_played:
            self._last_checkpoint = time.monotonic()
            return "idle"
        async with self._push_lock:
            if m.state != "online":
                return "busy"
            saving_off = False
            try:
                await self._rcon("save-off", 20)
                saving_off = True
                await self._rcon("save-all flush", 180)           # everything on disk, as of this moment
                await asyncio.to_thread(self.shared.snapshot, self.snapshot_dir)
            except Exception as exc:
                log.warning("Checkpoint skipped: couldn't copy the world (%s)", exc)
                self._last_checkpoint = time.monotonic()
                return "failed"
            finally:
                if saving_off:
                    await self._save_on()                          # saving is only off for the seconds the copy takes
            try:
                count, nbytes = await asyncio.to_thread(self.shared.checkpoint, self.snapshot_dir)
            except Exception as exc:
                log.warning("Checkpoint couldn't be uploaded (%s); the next one tries again", exc)
                self._last_checkpoint = time.monotonic()
                return "failed"
        self._last_checkpoint = time.monotonic()
        log.info("Checkpoint saved to the shared storage (%s files, %.1f MB)", count, nbytes / 1048576)
        return "saved"

    async def _rcon(self, command, timeout):
        cfg = self.manager.cfg
        return await self.manager.rcon(cfg.mc_host, cfg.rcon_port, cfg.rcon_password, command, timeout)

    async def _save_on(self):
        """Turn the server's own saving back on. Never leave it off: a server that isn't saving loses its world."""
        for attempt in range(4):
            try:
                await self._rcon("save-on", 20)
                return
            except Exception as exc:
                log.warning("save-on failed (%s), trying again", exc)
                await asyncio.sleep(2)
        log.error("COULD NOT TURN WORLD SAVING BACK ON. Stop hosting and start again so the server saves.")

    def _progress(self):
        return getattr(self.shared, "progress", None) or NullProgress()

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
        self._progress().start_journey("stop")
        try:
            return await self._hand_over(m)
        except BaseException:
            self._progress().end_journey()                   # a failed hand over: the window drops the bar
            raise
        finally:
            self._handing_over = False

    async def _hand_over(self, m) -> str:
        self._progress().note("Saving the world and stopping the server...", step="stop")
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
        self._progress().note("Handing over to the next host...", step="handover")
        await asyncio.to_thread(self.shared.release)
        self._progress().end_journey()
        log.info("Handed over hosting; the controller will now quit.")
        asyncio.get_running_loop().call_later(1.5, self.exit_event.set)   # let the HTTP/Discord reply go out first
        return "handed over"
