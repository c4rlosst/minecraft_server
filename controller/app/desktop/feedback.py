"""What the window shows back after a click or during a long job: the Minecraft XP-style progress bar, the toast message,
and the plain-words result of a Refresh. The drawing parts need a Scene (gui.py); the wording parts are plain functions."""
import math
import time

from .. import progress as progress_file
from ..sharedtool import _ago as ago



# ---- wording ---------------------------------------------------------------------------------------
def bar_text(job: dict):
    """(label, fraction or None, detail, span or None, step start) for the controller's current job (see progress.py)."""
    label = job.get("label") or "Working..."
    detail = ""
    if job.get("bytes"):
        detail = progress_file.size_text(job["bytes"]) + " transferred"
        if job.get("files"):
            detail += f"  -  {job['files']} file{'s' if job['files'] != 1 else ''}"
    span = tuple(job["span"]) if job.get("span") else None
    return label, job.get("fraction"), detail, span, job.get("stepStarted") or job.get("started")


def describe_refresh(before, after, error, me, now=None):
    """The toast after pressing Refresh: ('ok' | 'warn' | 'error', text)."""
    now = now or time.time()
    if error:
        return "error", "Can't reach the shared world. See the red box for why."
    if after is None:
        return "warn", "The shared world is empty. The owner uploads it first."
    version = after.get("version", 0)
    when = ago(now - float(after["lastPush"])) if after.get("lastPush") else "never"
    host = after.get("host")
    hosting_now = after.get("status") == "hosting"
    who = "you" if host == me else host
    if before is None:
        base = f"Checked: version {version}, uploaded {when}."
    elif after.get("version", 0) != before.get("version", 0):
        return "ok", f"New: version {version} was uploaded {when} by {after.get('lastHost') or 'someone'}. " \
                     "Start hosting downloads it."
    elif (before.get("status"), before.get("host")) != (after.get("status"), host):
        base = "Changed: " + (f"{who} is hosting now." if hosting_now else "nobody is hosting now.")
        return ("warn" if hosting_now and who != "you" else "ok"), base
    else:
        base = f"Up to date: version {version}, uploaded {when}."
    if hosting_now and who != "you":
        return "warn", base + f" {who} is hosting."
    return "ok", base


def blocked_reason(button: str, hosting: bool, listening: bool, busy: str, controller_up: bool) -> str:
    """Why a greyed-out button can't be used right now (shown when it is clicked)."""
    if button == "start":
        if busy:
            return "Wait for this to finish first: " + busy.split("...")[0].strip() + "."
        return "You're already hosting. Press Stop hosting when you're done."
    if button == "stop":
        if busy:
            return "Already working on it. Keep the PC on until it says Done."
        return "You're not hosting right now, so there is nothing to stop."
    if button == "panel":
        if controller_up and not listening:
            return "The web panel opens in a moment: the server is still getting ready."
        return "The web panel opens while this PC is hosting. Press Start hosting first."
    return "Not available right now."


# ---- drawing ---------------------------------------------------------------------------------------
BAR_GREEN = "#26b24f"       # a flat green, like a classic progress bar
BAR_SHINE = "#46c96b"       # a thin lighter line along the top of the fill
TRACK = "#ffffff"


def creep(lo: float, hi: float, since: float, now: float, tau: float = 6.0) -> float:
    """Where a step with no numbers has got to: it moves forward inside its slice (lo..hi) and slows down as it nears the
    end, so the bar always moves and never reaches the next step early."""
    return lo + (hi - lo) * (1.0 - math.exp(-max(0.0, now - since) / tau))


def ease(shown: float, target: float, dt: float, rate: float = 6.0) -> float:
    """One animation frame: the drawn value glides toward the real one (about 0.15 s to close most of a gap), never backwards
    by a little, and snaps back only when a new job starts."""
    if target < shown - 0.25:
        return target
    if target <= shown:
        return shown
    shown += (target - shown) * (1.0 - math.exp(-rate * dt))
    return target if target - shown < 0.0006 else shown


class ProgressBar:
    """A plain progress bar: black outline, white track, a solid green fill that grows smoothly from 0 to 100, and the
    percent above it. Steps without numbers creep forward (see creep()); when the job succeeds the bar glides to 100 and
    then goes away."""

    HEIGHT = 18
    FPS_MS = 33

    def __init__(self, scene, x, y, w):
        self.scene, self.canvas = scene, scene.canvas
        self.x, self.y, self.w, self.h = x, y, w, self.HEIGHT
        c, h = self.canvas, self.HEIGHT
        self.items = []

        def add(item):
            self.items.append(item)
            return item
        self.frame = add(c.create_rectangle(x - 3, y - 3, x + w + 3, y + h + 3, fill="#000000", outline="#000000"))
        self.track = add(c.create_rectangle(x, y, x + w, y + h, fill=TRACK, outline=""))
        self.fill = add(c.create_rectangle(x, y, x, y + h, fill=BAR_GREEN, outline=""))
        self.shine = add(c.create_rectangle(x, y, x, y + 3, fill=BAR_SHINE, outline=""))
        self.label = scene.text(x, y - 26, "", 10, "#202020")
        self.percent = scene.text(x + w, y - 26, "", 10, "#202020", anchor="ne", bold=True)
        self.detail = scene.text(x, y + h + 10, "", 9, "#404040")
        for t in (self.label, self.percent, self.detail):
            self.items += t.ids
        self.visible, self.target, self.shown = False, 0.0, 0.0
        self._fraction = self._span = self._since = None
        self._finishing, self._done_at, self._last, self._percent_shown = False, None, None, -1
        self.hide()

    # -- what the window tells it -----------------------------------------------------------------------
    def show(self, label, fraction=None, detail="", span=None, since=None):
        """fraction: 0..1 when known. Otherwise span (lo, hi) and since (when the step began) let it creep."""
        self._fraction, self._span, self._since = fraction, span, since
        self._finishing = False
        self.label.set(label)
        self.detail.set(detail)
        if not self.visible:
            self.visible, self.shown, self.target, self._last = True, 0.0, 0.0, time.time()
            self._percent_shown = -1
            for item in self.items:
                self.canvas.itemconfig(item, state="normal")
            self._animate()

    def complete(self):
        """The job succeeded: glide to 100 and go away."""
        if self.visible and not self._finishing:
            self._finishing, self._done_at = True, None

    def hide(self):
        self.visible = False
        for item in self.items:
            self.canvas.itemconfig(item, state="hidden")

    # -- the animation ---------------------------------------------------------------------------------
    def _target(self, now):
        if self._finishing:
            return 1.0
        if self._fraction is not None:
            return max(0.0, min(1.0, float(self._fraction)))
        lo, hi = self._span or (0.0, 0.95)
        return creep(lo, hi, self._since or now, now, 6.0 if self._span else 20.0)

    def _draw(self):
        c, x, y, h = self.canvas, self.x, self.y, self.h
        right = x + round(self.w * self.shown)
        c.coords(self.fill, x, y, right, y + h)
        c.coords(self.shine, x, y, right, y + (3 if right - x > 2 else 0))
        pct = int(self.shown * 100 + 0.0001)
        if pct != self._percent_shown:
            self._percent_shown = pct
            self.percent.set(f"{pct}%")

    def _animate(self):
        if not self.visible:
            return
        try:
            now = time.time()
            dt, self._last = now - (self._last or now), now
            self.target = self._target(now)
            self.shown = ease(self.shown, self.target, dt)
            self._draw()
            if self._finishing and self.shown >= 0.9999:
                self._done_at = self._done_at or now
                if now - self._done_at > 0.7:                       # a moment at 100%, then away
                    self.hide()
                    return
            self.canvas.after(self.FPS_MS, self._animate)
        except Exception:                                           # the window was rebuilt or closed
            self.visible = False


ExpBar = ProgressBar                                                # (the old name)


class Toast:
    """A short message that appears over the title area of the window and fades after a few seconds."""
    COLORS = {"info": "#a0a0a0", "ok": "#4cc04c", "warn": "#e6b422", "error": "#d23b3b"}

    def __init__(self, scene, cx, y, w=640, h=34):
        self.scene, self.canvas, self.job = scene, scene.canvas, None
        c = scene.canvas
        self.box = c.create_rectangle(cx - w // 2, y, cx + w // 2, y + h, fill="#1a1a1a", outline="#a0a0a0", width=3, state="hidden")
        self.text = scene.text(cx, y + h // 2, "", 10, "#ffffff", "#000000", anchor="center", width=w - 24)
        for item in self.text.ids:
            c.itemconfig(item, state="hidden")

    def show(self, message, kind="info", seconds=3.5):
        c = self.canvas
        c.itemconfig(self.box, state="normal", outline=self.COLORS.get(kind, "#a0a0a0"))
        self.text.set(message)
        for item in self.text.ids:
            c.itemconfig(item, state="normal")
        c.tag_raise(self.box)
        for item in self.text.ids:
            c.tag_raise(item)
        if self.job is not None:
            try:
                c.after_cancel(self.job)
            except Exception:
                pass
        self.job = c.after(int(seconds * 1000), self.hide)

    def hide(self):
        self.job = None
        try:
            self.canvas.itemconfig(self.box, state="hidden")
            for item in self.text.ids:
                self.canvas.itemconfig(item, state="hidden")
        except Exception:
            pass
