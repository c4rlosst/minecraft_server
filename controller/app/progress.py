"""Progress of the big jobs (starting to host, downloading the world, uploading it, handing over), shared between the
controller, which writes it, and the SMP Host window, which draws it, through one small JSON file in the state folder.

The file says what is happening ("label") and how far along the whole job is ("fraction", 0..1). A job is a JOURNEY made of
steps that each own a slice of the bar:

    start:  lock 0-8%   |  download 8-88% (real numbers)   |  tunnel and bot 88-99%
    stop:   save 0-20%  |  upload 20-90% (real numbers)    |  verify 90-96%  |  hand over 96-99%

A step that has no numbers yet (taking the lock, starting the server) reports "span" instead of a fraction, and the window
creeps the bar forward inside that slice, so the bar always moves and never jumps back. A transfer inside a journey fills
its slice in proportion to the bytes moved, split into stages (world, mods, config, ...) that share the slice by size."""
import json
import os
import time

STALE_AFTER = 45.0           # seconds without an update: the controller died or finished badly, so the window hides the bar
KEEPALIVE = 5.0              # a job that is waiting (taking the lock, starting) re-writes the file this often (touch())

JOURNEYS = {
    "start": {"lock": (0.00, 0.08), "download": (0.08, 0.88), "services": (0.88, 0.99)},
    "stop": {"stop": (0.00, 0.20), "upload": (0.20, 0.90), "verify": (0.90, 0.96), "handover": (0.96, 0.99)},
}


class NullProgress:
    """Used when nobody is watching (tests, the folder-sync mode): every call does nothing."""

    def start_journey(self, kind): pass
    def end_journey(self): pass
    def begin(self, phase, label, weights=None, step=None): pass
    def stage(self, name, label=None): pass
    def transfer(self, stats): pass
    def end_stage(self): pass
    def note(self, label, fraction=None, step=None): pass
    def finish(self): pass
    def touch(self): pass


class Progress(NullProgress):
    def __init__(self, path, clock=time.time, min_interval=0.25):
        self.path, self.clock, self.min_interval = path, clock, min_interval
        self.journey = None
        self._reset()
        self._last_write = 0.0

    def _reset(self):
        self.phase = self.label = self.stage_name = None
        self.weights, self.finished_weight, self.finished_bytes, self.finished_files = {}, 0.0, 0, 0
        self.cur_bytes = self.cur_total = self.cur_files = 0
        self.fixed_fraction = None
        self.span = None
        self.step_started = None
        self.started = None

    # -- journeys --------------------------------------------------------------------------------------
    def start_journey(self, kind):
        """A whole job (start hosting, stop hosting). The file stays until end_journey(), whatever the steps do."""
        self.journey = kind if kind in JOURNEYS else None
        self._reset()
        self.phase, self.started = "journey", self.clock()

    def end_journey(self):
        self.journey = None
        self._reset()
        self._remove()

    def _span_for(self, step):
        return JOURNEYS.get(self.journey, {}).get(step) if self.journey else None

    # -- what the controller calls ---------------------------------------------------------------------
    def begin(self, phase, label, weights=None, step=None):
        """A transfer. weights: {stage name: relative size}, so the bar fills in proportion to the work."""
        started = self.started if self.journey else None
        self._reset()
        self.phase, self.label = phase, label
        self.started = started or self.clock()
        self.step_started = self.clock()
        self.span = self._span_for(step or phase)
        self.weights = {k: max(float(v), 1.0) for k, v in (weights or {}).items()}
        self._write(force=True)

    def stage(self, name, label=None):
        self.end_stage()
        self.stage_name = name
        if label:
            self.label = label
        self._write(force=True)

    def transfer(self, stats):
        """rclone's own numbers for the stage that is running (see Rclone.sync)."""
        self.cur_bytes = int(stats.get("bytes") or 0)
        self.cur_total = int(stats.get("totalBytes") or 0)
        self.cur_files = int(stats.get("transfers") or 0)
        self._write()

    def end_stage(self):
        if self.stage_name is None:
            return
        self.finished_weight += self.weights.get(self.stage_name, 1.0)
        self.finished_bytes += self.cur_bytes
        self.finished_files += self.cur_files
        self.cur_bytes = self.cur_total = self.cur_files = 0
        self.stage_name = None
        self._write(force=True)

    def note(self, label, fraction=None, step=None):
        """A step without transfer numbers (taking the lock, starting the server, verifying). fraction None means 'no
        numbers': inside a journey the window creeps the bar through the step's slice."""
        self.label, self.fixed_fraction = label, fraction
        self.weights, self.finished_weight, self.finished_bytes, self.finished_files = {}, 0.0, 0, 0
        self.cur_bytes = self.cur_total = self.cur_files = 0
        self.stage_name = None
        self.span = self._span_for(step) if step else self.span
        self.step_started = self.clock()
        if self.phase is None:
            self.phase, self.started = "step", self.clock()
        self._write(force=True)

    def finish(self):
        """A transfer is done. Inside a journey the file stays (the next step continues the bar); otherwise it goes."""
        if self.journey:
            self.finished_weight = sum(self.weights.values())       # the slice is full
            self.fixed_fraction = 1.0
            self.stage_name = None
            self._write(force=True)
            return
        self._reset()
        self._remove()

    # -- the numbers ------------------------------------------------------------------------------------
    def _local(self):
        """How far the current step is (0..1), or None when it has no numbers."""
        if self.fixed_fraction is not None:
            return self.fixed_fraction
        total_weight = sum(self.weights.values())
        if not total_weight:
            return None
        current = 0.0
        if self.stage_name is not None and self.cur_total > 0:
            current = self.weights.get(self.stage_name, 1.0) * min(1.0, self.cur_bytes / self.cur_total)
        seen = self.finished_weight + current
        if seen <= 0 and self.stage_name is None:
            return None                                  # nothing measured yet
        return max(0.0, min(1.0, seen / total_weight))

    def fraction(self):
        """How far the whole job is (0..1), or None while the current step has no numbers."""
        local = self._local()
        if local is None:
            return None
        if self.span:
            lo, hi = self.span
            return lo + (hi - lo) * local
        return local

    def _write(self, force=False):
        now = self.clock()
        if not force and now - self._last_write < self.min_interval:
            return
        self._last_write = now
        data = {"phase": self.phase, "label": self.label, "stage": self.stage_name, "fraction": self.fraction(),
                "span": list(self.span) if self.span else None, "stepStarted": self.step_started,
                "bytes": self.finished_bytes + self.cur_bytes, "files": self.finished_files + self.cur_files,
                "started": self.started, "updated": now}
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.path)
        except OSError:
            pass                                          # a progress bar is never worth stopping a transfer over

    def touch(self):
        """Say 'still working' (a step with no numbers writes nothing else for a while)."""
        if self.phase:
            self._write(force=True)

    def _remove(self):
        try:
            os.remove(self.path)
        except OSError:
            pass


async def keepalive(progress, every=KEEPALIVE):
    """Run next to the controller: while a job is on, say 'still working' every few seconds, so the window doesn't take a
    long wait (taking the lock, listing the files) for a controller that died."""
    import asyncio
    while True:
        await asyncio.sleep(every)
        progress.touch()


def read(path, now=None):
    """What the window shows: the controller's current job, or None when there is none (or it stopped updating)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("phase"):
        return None
    if (now if now is not None else time.time()) - float(data.get("updated") or 0) > STALE_AFTER:
        return None
    return data


def size_text(nbytes) -> str:
    nbytes = float(nbytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024 or unit == "GB":
            return f"{nbytes:.0f} {unit}" if unit in ("B", "KB") else f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} GB"
