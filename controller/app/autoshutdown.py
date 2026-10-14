"""Optional: switch the PC off when nobody has been on the server for a while AND the person at the PC is away.

All of these must hold: the Minecraft server is asleep, it has been AUTO_SHUTDOWN_MINUTES since anyone was last on it
(the server itself sleeps after IDLE_MINUTES, so with the same number the PC goes right after the server sleeps), and there
has been no keyboard/mouse input for AUTO_SHUTDOWN_USER_IDLE_MINUTES. Then a warning (Shut down now / Cancel) is shown for
AUTO_SHUTDOWN_WARNING_SECONDS. Touching the keyboard or mouse, or a friend joining, cancels it. With shared hosting
the world is uploaded and the lock released first, and Windows waits a little so Google Drive can finish syncing."""
import asyncio
import ctypes
import logging
import subprocess
import sys
import threading
import time

from .manager import PlayersOnline
from .shared import HostingError

log = logging.getLogger("autoshutdown")
IS_WINDOWS = sys.platform == "win32"
TITLE = "Server controller"


def user_idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input on this PC (0 if unknown, so unknown never means 'away')."""
    if not IS_WINDOWS:
        return 0.0

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    ctypes.windll.kernel32.GetTickCount.restype = ctypes.c_uint
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


class WarningBox:
    """A Windows message box (Shut down now / Cancel) shown from a background thread. result: 1 = OK, 2 = Cancel."""
    def __init__(self, text: str):
        self.text, self.result, self._closing = text, None, False
        self._thread = threading.Thread(target=self._show, daemon=True)

    def _show(self):
        # MB_OKCANCEL | MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST
        code = ctypes.windll.user32.MessageBoxW(0, self.text, TITLE, 0x1 | 0x30 | 0x10000 | 0x40000)
        if not self._closing:
            self.result = code if code in (1, 2) else None     # 0 = couldn't show it (no desktop): ignore

    def show(self):
        if IS_WINDOWS:
            self._thread.start()

    def close(self):
        self._closing = True
        if IS_WINDOWS:
            hwnd = ctypes.windll.user32.FindWindowW(None, TITLE)
            if hwnd:
                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)    # WM_CLOSE


async def countdown(seconds, should_cancel, box, sleep=asyncio.sleep, clock=time.monotonic) -> str:
    """'proceed' when the time is up (or Shut down now was pressed), 'cancel' if cancelled or should_cancel() says so."""
    start = clock()
    while clock() - start < seconds:
        if box.result == 1:
            return "proceed"
        if box.result == 2:
            return "cancel"
        if should_cancel():
            return "cancel"
        await sleep(1)
    return "proceed"


async def windows_confirm(seconds, should_cancel, message) -> str:
    box = WarningBox(message)
    box.show()
    try:
        return await countdown(seconds, should_cancel, box)
    finally:
        box.close()


def windows_shutdown(delay: int, message: str, force: bool = False) -> None:
    cmd = ["shutdown", "/s", "/t", str(int(delay)), "/c", message] + (["/f"] if force else [])
    subprocess.run(cmd, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class AutoShutdown:
    def __init__(self, cfg, manager, hosting=None, request_exit=lambda: None, idle_fn=user_idle_seconds,
                 confirm_fn=windows_confirm, shutdown_fn=windows_shutdown, clock=time.monotonic, sleep=asyncio.sleep):
        self.cfg, self.manager, self.hosting, self.request_exit = cfg, manager, hosting, request_exit
        self.idle_fn, self.confirm_fn, self.shutdown_fn = idle_fn, confirm_fn, shutdown_fn
        self.clock, self.sleep = clock, sleep
        self.last_busy = clock()       # last moment someone was (or might be) on the server; starts when the controller does
        self.shut_down = False

    async def run(self):
        while not self.shut_down:
            try:
                await self.step()
            except Exception:
                log.exception("auto shutdown check failed")
            await self.sleep(15)

    async def step(self):
        cfg, m, now = self.cfg, self.manager, self.clock()
        if cfg.auto_shutdown_minutes <= 0:
            return
        if m.players > 0 or m.state == "starting" or m.maintenance:
            self.last_busy = now                     # someone is on (or just woke it): the quiet timer starts over
        if m.state != "asleep":
            return                                   # the server has to be asleep first (it sleeps after IDLE_MINUTES)
        quiet = now - self.last_busy >= cfg.auto_shutdown_minutes * 60
        idle = self.idle_fn()
        away = idle >= cfg.auto_shutdown_user_idle_minutes * 60
        if not (quiet and away):
            return

        log.info("Nobody online for %s min and the PC has been idle for %s min: warning before shutting down",
                 cfg.auto_shutdown_minutes, int(idle // 60))
        start_idle = idle

        def should_cancel() -> bool:
            return m.state != "asleep" or m.maintenance or self.idle_fn() < start_idle   # someone joined / you're back

        message = (f"Nobody has been on the server for {cfg.auto_shutdown_minutes} minutes and you've been away from the PC.\n\n"
                   f"This PC will shut down in {cfg.auto_shutdown_warning_seconds} seconds.\n\n"
                   "Click Cancel (or just move the mouse) to keep it on.")
        decision = await self.confirm_fn(cfg.auto_shutdown_warning_seconds, should_cancel, message)
        if decision != "proceed":
            log.info("Shutdown cancelled; waiting another %s minutes", cfg.auto_shutdown_minutes)
            self.last_busy = self.clock()
            return
        await self.power_off()

    async def power_off(self):
        delay = 15
        if self.hosting:                              # upload the world and give the lock back first
            try:
                await self.hosting.handover()
            except (PlayersOnline, HostingError) as exc:
                log.warning("Not shutting down: %s", exc)
                self.last_busy = self.clock()
                return
            delay = max(delay, self.cfg.auto_shutdown_drive_wait)   # let Google Drive finish syncing
        log.info("Shutting this PC down in %s seconds.", delay)
        self.shut_down = True
        await asyncio.to_thread(self.shutdown_fn, delay,
                                f"Shutting down: nobody has been on the server for {self.cfg.auto_shutdown_minutes} minutes. "
                                "Run 'shutdown /a' to cancel.", self.cfg.auto_shutdown_force)
        self.request_exit()
