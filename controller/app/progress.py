"""Progress of the big jobs (taking the lock, downloading the world, uploading it, handing over), shared between the
controller, which writes it, and the SMP Host window, which draws it, through one small JSON file in the state folder.

The file says what is happening ("label"), how far along it is ("fraction", 0..1, or null when it can't tell yet) and how
much has been transferred. A transfer is split into stages (world, mods, config, ...) that each get a share of the bar."""
import json
import os
import time

STALE_AFTER = 20.0           # seconds without an update: the controller died or finished badly, so the window hides the bar


class NullProgress:
    """Used when nobody is watching (tests, the folder-sync mode): every call does nothing."""

    def begin(self, phase, label, weights=None): pass
    def stage(self, name, label=None): pass
    def transfer(self, stats): pass
    def end_stage(self): pass
    def note(self, label, fraction=None): pass
    def finish(self): pass


class Progress(NullProgress):
    def __init__(self, path, clock=time.time, min_interval=0.25):
        self.path, self.clock, self.min_interval = path, clock, min_interval
        self._reset()
        self._last_write = 0.0

    def _reset(self):
        self.phase = self.label = self.stage_name = None
        self.weights, self.finished_weight, self.finished_bytes, self.finished_files = {}, 0.0, 0, 0
        self.cur_bytes = self.cur_total = self.cur_files = 0
        self.fixed_fraction = None
        self.started = None

    # -- what the controller calls ---------------------------------------------------------------------
    def begin(self, phase, label, weights=None):
        """A new job. weights: {stage name: relative size}, so the bar fills in proportion to the work."""
        self._reset()
        self.phase, self.label, self.started = phase, label, self.clock()
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

    def note(self, label, fraction=None):
        """A step without numbers (taking the lock, starting the server, verifying). fraction None = 'working on it'."""
        self.label, self.fixed_fraction = label, fraction
        self.weights, self.finished_weight, self.finished_bytes, self.finished_files = {}, 0.0, 0, 0
        self.cur_bytes = self.cur_total = self.cur_files = 0       # a new step: the numbers of the last transfer are over
        self.stage_name = None
        if self.phase is None:
            self.phase, self.started = "step", self.clock()
        self._write(force=True)

    def finish(self):
        self._reset()
        try:
            os.remove(self.path)
        except OSError:
            pass

    # -- the numbers ------------------------------------------------------------------------------------
    def fraction(self):
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
            return None                                  # nothing measured yet: the bar pulses instead
        return max(0.0, min(1.0, seen / total_weight))

    def _write(self, force=False):
        now = self.clock()
        if not force and now - self._last_write < self.min_interval:
            return
        self._last_write = now
        data = {"phase": self.phase, "label": self.label, "stage": self.stage_name, "fraction": self.fraction(),
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
