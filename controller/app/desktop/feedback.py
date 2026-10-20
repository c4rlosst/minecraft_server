"""What the window shows back after a click or during a long job: the Minecraft XP-style progress bar, the toast message,
and the plain-words result of a Refresh. The drawing parts need a Scene (gui.py); the wording parts are plain functions."""
import time

from .. import progress as progress_file
from ..sharedtool import _ago as ago

XP_GREEN = "#80ff20"
XP_LIGHT = "#c6ff7a"
XP_DARK = "#4fae10"


# ---- wording ---------------------------------------------------------------------------------------
def bar_text(job: dict):
    """(label, fraction or None, detail) for the controller's current job (see progress.py)."""
    label = job.get("label") or "Working..."
    fraction = job.get("fraction")
    detail = ""
    if job.get("bytes"):
        detail = progress_file.size_text(job["bytes"]) + " transferred"
        if job.get("files"):
            detail += f"  -  {job['files']} file{'s' if job['files'] != 1 else ''}"
    elif job.get("started"):
        detail = f"{int(max(0, time.time() - float(job['started'])))} s"
    return label, fraction, detail


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
class ExpBar:
    """The Minecraft experience bar: a thin bar made of separate segments, bright green with a light top edge, and the
    percent as the 'level' number above the middle (green with a black outline). While the size of the job isn't known
    yet, a few lit segments sweep back and forth instead."""

    SEGMENTS, SEG_GAP, HEIGHT = 18, 2, 10
    EMPTY, EMPTY_EDGE = "#3d3d3d", "#262626"
    LIGHT, BASE, DARK = "#bdf47a", "#7fe02a", "#4fae10"

    def __init__(self, scene, x, y, w):
        self.scene, self.canvas = scene, scene.canvas
        c, n, gap, h = self.canvas, self.SEGMENTS, self.SEG_GAP, self.HEIGHT
        self.seg_w = (w - (n - 1) * gap) // n
        self.w = n * self.seg_w + (n - 1) * gap
        self.x, self.y = x + (w - self.w) // 2, y
        self.h = h
        self.visible, self.determinate, self.sweep_pos, self.sweep_dir = False, False, 0.0, 0.5
        self.items, self.segs = [], []
        for i in range(n):
            left = self.x + i * (self.seg_w + gap)
            empty = c.create_rectangle(left, y, left + self.seg_w, y + h, fill=self.EMPTY, outline="")
            edge = c.create_rectangle(left, y + h - 2, left + self.seg_w, y + h, fill=self.EMPTY_EDGE, outline="")
            light = c.create_rectangle(left, y, left, y, fill=self.LIGHT, outline="")
            base = c.create_rectangle(left, y, left, y, fill=self.BASE, outline="")
            dark = c.create_rectangle(left, y, left, y, fill=self.DARK, outline="")
            self.segs.append((left, light, base, dark))
            self.items += [empty, edge, light, base, dark]
        cx = self.x + self.w // 2
        self.label = scene.text(self.x, y - 40, "", 10, "#202020")
        self.outline = [scene.text(cx + dx, y - 6 + dy, "", 16, "#000000", anchor="s", bold=True)
                        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2), (-2, 2), (2, -2))]
        self.level = scene.text(cx, y - 6, "", 16, "#80ff20", anchor="s", bold=True)
        self.detail = scene.text(self.x, y + h + 8, "", 9, "#404040")
        for t in (self.label, self.detail, self.level, *self.outline):
            self.items += t.ids
        self.hide()

    def _fill_segment(self, i, amount):
        """Light up segment i by amount (0..1)."""
        left, light, base, dark = self.segs[i]
        right = left + round(self.seg_w * max(0.0, min(1.0, amount)))
        y, h, c = self.y, self.h, self.canvas
        if right <= left:
            right = left
        c.coords(light, left, y, right, y + 2)
        c.coords(base, left, y + 2, right, y + h - 3)
        c.coords(dark, left, y + h - 3, right, y + h)

    def show(self, label, fraction=None, detail=""):
        """fraction None = 'working on it, no numbers yet'."""
        c = self.canvas
        if not self.visible:
            self.visible = True
            for item in self.items:
                c.itemconfig(item, state="normal")
            self._animate()
        self.label.set(label)
        self.detail.set(detail)
        self.determinate = fraction is not None
        if fraction is None:
            self._text_level("")
            return
        fraction = max(0.0, min(1.0, float(fraction)))
        lit = fraction * self.SEGMENTS
        for i in range(self.SEGMENTS):
            self._fill_segment(i, lit - i)
        self._text_level(str(round(fraction * 100)))

    def _text_level(self, text):
        for t in (*self.outline, self.level):
            for item in t.ids:
                self.canvas.itemconfig(item, text=text)

    def hide(self):
        self.visible = False
        for item in self.items:
            self.canvas.itemconfig(item, state="hidden")

    def _animate(self):
        """No numbers yet: a few segments light up and sweep across (about 16 frames a second)."""
        if not self.visible:
            return
        try:
            if not self.determinate:
                self.sweep_pos += self.sweep_dir
                if self.sweep_pos <= 0 or self.sweep_pos >= self.SEGMENTS - 1:
                    self.sweep_dir = -self.sweep_dir
                for i in range(self.SEGMENTS):
                    self._fill_segment(i, max(0.0, 1.0 - abs(i - self.sweep_pos) / 3.0))
            self.canvas.after(60, self._animate)
        except Exception:                       # the window was rebuilt or closed
            self.visible = False


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
