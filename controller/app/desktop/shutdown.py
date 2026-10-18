"""Stop Windows from shutting the PC down in silence while this one is hosting.

If the PC is switched off mid-hosting, the world is never uploaded and the next host starts from an old copy. While hosting,
the app registers a *shutdown block reason*: Windows then lists "SMP Host" on its "this app is preventing shutdown" screen
with our sentence, and the person can cancel and press Stop hosting (or shut down anyway on purpose). The app also hears the
shutdown attempt and brings its window up. A no-op away from Windows."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

WM_QUERYENDSESSION = 0x0011
GWLP_WNDPROC = -4
REASON = "You're still hosting the world. Cancel, then press Stop hosting so it's uploaded for the next host."

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t)


class ShutdownGuard:
    def __init__(self, root, hosting, on_attempt):
        self.root, self.hosting, self.on_attempt = root, hosting, on_attempt
        self.hwnd, self.blocked, self._proc, self._old = None, False, None, None
        self.active = sys.platform == "win32"
        if self.active:
            self._install()

    def _install(self):
        user32 = ctypes.windll.user32
        self._set = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        self._call = user32.CallWindowProcW
        self._set.restype = ctypes.c_ssize_t
        self._set.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        self._call.restype = ctypes.c_ssize_t
        self._call.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
        user32.GetParent.restype = wintypes.HWND
        self.root.update_idletasks()
        inner = self.root.winfo_id()
        self.hwnd = user32.GetParent(inner) or inner      # Tk's real top-level window wraps the one winfo_id names
        self._proc = WNDPROC(self._wndproc)               # kept on self: a collected callback would crash the app
        self._old = self._set(self.hwnd, GWLP_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_QUERYENDSESSION and self.hosting():
            self.sync()                                   # make sure the reason is registered before Windows asks
            self.root.after(0, self.on_attempt)           # never block inside the message: Windows is waiting on it
        return self._call(self._old, hwnd, msg, wparam, lparam)

    def sync(self):
        """Register the block reason while hosting, drop it when not. Cheap; call it on every tick."""
        if not self.active or not self.hwnd:
            return
        want = bool(self.hosting())
        if want == self.blocked:
            return
        user32 = ctypes.windll.user32
        if want:
            self.blocked = bool(user32.ShutdownBlockReasonCreate(wintypes.HWND(self.hwnd), ctypes.c_wchar_p(REASON)))
        else:
            user32.ShutdownBlockReasonDestroy(wintypes.HWND(self.hwnd))
            self.blocked = False

    def close(self):
        if self.active and self.hwnd:
            if self.blocked:
                ctypes.windll.user32.ShutdownBlockReasonDestroy(wintypes.HWND(self.hwnd))
                self.blocked = False
            if self._old:                                 # hand the window back before Tk destroys it
                self._set(self.hwnd, GWLP_WNDPROC, ctypes.c_void_p(self._old))
                self._old = None
