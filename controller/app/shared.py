"""Shared hosting: two (or a few) trusted people take turns hosting the same world through a shared folder
(e.g. Google Drive for desktop). Only the lock holder runs the server.

  <shared>/state.json   who is hosting, last heartbeat, and the size/file-count of the last upload
  <shared>/data/...     the world, mods, config, EasyAuth passwords, ops/bans (a mirror of the server folder)

Start: take the lock, wait until the cloud folder has fully synced, pull the world. While hosting: heartbeat every
minute and push after every sleep. Handover: push, release the lock."""
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid

from .rclone import RcloneError, join as rjoin

log = logging.getLogger("shared")

SYNC_DIRS = ("world", "mods", "config", "EasyAuth")
SYNC_FILES = ("server.properties", "ops.json", "whitelist.json", "banned-players.json", "banned-ips.json",
              "usercache.json")
PULL_MARKER = ".pull-incomplete"
SKIP_NAMES = {"session.lock", "desktop.ini"}   # desktop.ini: Google Drive adds one to every folder it syncs
STALE_SECONDS = 300


class HostingError(Exception):
    """Shown to the user: someone else is hosting, the shared folder isn't ready, and so on."""


# ---- file helpers ------------------------------------------------------------------------------
def _walk(base: str):
    for root, _dirs, files in os.walk(base):
        for name in files:
            if name not in SKIP_NAMES:
                path = os.path.join(root, name)
                yield os.path.relpath(path, base), path


def _same(a: str, b: str) -> bool:
    try:
        sa, sb = os.stat(a), os.stat(b)
    except OSError:
        return False
    return sa.st_size == sb.st_size and abs(sa.st_mtime - sb.st_mtime) < 2


def mirror_dir(src: str, dst: str) -> None:
    """Make dst identical to src: copy new/changed files, delete files that are not in src."""
    wanted = set()
    for rel, path in _walk(src):
        wanted.add(rel)
        target = os.path.join(dst, rel)
        if not _same(path, target):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(path, target)
    if os.path.isdir(dst):
        for rel, path in list(_walk(dst)):
            if rel not in wanted:
                os.remove(path)
        for root, dirs, files in os.walk(dst, topdown=False):   # tidy up empty folders
            if root != dst and not os.listdir(root):
                os.rmdir(root)


def copy_file(src: str, dst: str) -> None:
    if os.path.isfile(src) and not _same(src, dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def manifest(base: str) -> tuple:
    """(file count, total bytes) over everything that gets synced under base."""
    count = total = 0
    for d in SYNC_DIRS:
        for _rel, path in _walk(os.path.join(base, d)):
            count += 1
            total += os.path.getsize(path)
    for f in SYNC_FILES:
        path = os.path.join(base, f)
        if os.path.isfile(path):
            count += 1
            total += os.path.getsize(path)
    return count, total


# ---- what a world looks like (to stop a wrong or half-copied one from replacing the real one) --------
def world_facts(paths) -> dict:
    """paths: files under a world folder, relative with forward slashes."""
    paths = list(paths)
    players = sum(1 for p in paths if re.fullmatch(r"(players/data|playerdata)/[^/]+\.dat", p))
    return {"files": len(paths), "players": players, "level": "level.dat" in paths}


def local_world_paths(world_dir: str):
    return [rel.replace(os.sep, "/") for rel, _path in _walk(world_dir)]


def upload_problem(shared: dict, local: dict):
    """Why this PC's world must not replace the shared one (None = fine). Players never disappear in normal play, so
    fewer player files, a missing level.dat or half the files all mean 'incomplete or wrong world'."""
    if shared["level"] and not local["level"]:
        return "this PC's world has no level.dat"
    if local["players"] < shared["players"]:
        return f"this PC's world has {local['players']} player files, the shared one has {shared['players']}"
    if shared["files"] >= 20 and local["files"] * 2 < shared["files"]:
        return f"this PC's world has {local['files']} files, the shared one has {shared['files']}"
    return None


# ---- the lock and the sync ---------------------------------------------------------------------
def resumed_own_lock(previous: dict) -> bool:
    """True if acquire() resumed this PC's own earlier lock (then the local world must not be replaced by a download)."""
    return bool(previous.get("resumedOwnLock"))


class SharedWorld:
    def __init__(self, shared_dir: str, server_dir: str, host: str, clock=time.time, sleep=time.sleep):
        self.shared_dir, self.server_dir, self.host = shared_dir, server_dir, host
        self.clock, self.sleep = clock, sleep
        self.state_path = os.path.join(shared_dir, "state.json")
        self.data_dir = os.path.join(shared_dir, "data")
        self.token = None
        self.skip_checks = False        # SHARED_SKIP_CHECKS=1: upload even when this PC's world looks wrong
        self._lock = threading.Lock()   # the heartbeat thread and an upload must not edit state.json at once

    # -- the storage itself: a synced folder here, rclone in RcloneWorld ------------------------------
    def _prepare(self) -> None:
        if not os.path.isdir(self.shared_dir):
            raise HostingError(f"Shared folder not found: {self.shared_dir} (is Google Drive for desktop running?)")
        os.makedirs(self.data_dir, exist_ok=True)

    def _wait_for_sync(self, state: dict, takeover: bool, sync_timeout: float) -> None:
        if state.get("version", 0) > 0:     # wait until the cloud folder holds the whole last upload
            deadline = self.clock() + sync_timeout
            while True:
                count, nbytes = manifest(self.data_dir)
                if (count, nbytes) == (state.get("count"), state.get("bytes")):
                    break
                if self.clock() > deadline:
                    if takeover:
                        log.warning("Cloud folder never matched the last upload; continuing because of takeover")
                        break
                    raise HostingError("The shared folder doesn't match the last upload yet. Let Google Drive finish "
                                       "syncing and try again.")
                log.info("Waiting for the cloud to finish syncing (%s/%s files)...", count, state.get("count"))
                self.sleep(5)

    def _conflict_files(self) -> list:
        return [n for n in os.listdir(self.shared_dir)
                if n.startswith("state") and n not in ("state.json", "state.json.tmp")]

    def _shared_world_paths(self) -> list:
        return local_world_paths(os.path.join(self.data_dir, "world"))

    def _pull_data(self) -> None:
        for d in SYNC_DIRS:
            src = os.path.join(self.data_dir, d)
            if os.path.isdir(src):
                mirror_dir(src, os.path.join(self.server_dir, d))
        for f in SYNC_FILES:
            copy_file(os.path.join(self.data_dir, f), os.path.join(self.server_dir, f))

    def _before_push(self) -> None:
        pass

    def _push_data(self) -> tuple:
        for d in SYNC_DIRS:
            src = os.path.join(self.server_dir, d)
            if os.path.isdir(src):
                mirror_dir(src, os.path.join(self.data_dir, d))
        for f in SYNC_FILES:
            copy_file(os.path.join(self.server_dir, f), os.path.join(self.data_dir, f))
        return manifest(self.data_dir)

    # -- state file
    def read_state(self):
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _write_state(self, state: dict) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, self.state_path)

    def holds_lock(self) -> bool:
        state = self.read_state()
        return bool(self.token and state and state.get("token") == self.token)

    # -- taking the lock
    def acquire(self, takeover: bool = False, claim_wait: float = 20, sync_timeout: float = 1200) -> dict:
        """Returns the previous state ({} for a brand new shared folder). Raises HostingError when it can't."""
        self._prepare()
        state = self.read_state() or {}

        if state.get("status") == "hosting" and state.get("host") == self.host:
            # This PC's own lock, left behind when its controller was closed or crashed: resume it. The local world
            # is at least as new as the last upload, so the caller must not pull over it (see resumed_own_lock()).
            log.warning("Resuming the hosting lock this PC (%s) already held.", self.host)
            state = dict(state, resumedOwnLock=True)
        elif state.get("status") == "hosting":
            age = self.clock() - float(state.get("lastSeen", 0))
            who = state.get("host", "someone")
            if age < STALE_SECONDS:     # a live host is never overridden, not even with takeover
                raise HostingError(f"{who} is already hosting. Join their server, or ask them to hand over.")
            if not takeover:
                raise HostingError(f"{who} was hosting but hasn't checked in for {int(age // 60)} min (crashed, or still "
                                   "uploading?). If they're really gone, start again with -Takeover. Anything they had "
                                   "not uploaded is lost.")
            log.warning("Taking over from %s (no heartbeat for %ss)", who, int(age))

        self._wait_for_sync(state, takeover, sync_timeout)

        token = uuid.uuid4().hex
        now = self.clock()
        claim = {k: v for k, v in state.items() if k != "resumedOwnLock"}
        claim.update(status="hosting", host=self.host, token=token, since=now, lastSeen=now)
        claim.setdefault("version", 0)
        self._write_state(claim)
        self.sleep(claim_wait)              # if two people start at once, the loser sees the other's token
        check = self.read_state() or {}
        conflicts = self._conflict_files()
        if check.get("token") != token or conflicts:
            raise HostingError("Someone else took the lock at the same moment (or the shared storage made a conflict "
                               "copy of state.json). Ask who is hosting, delete any 'state (...)' files, and try again.")
        self.token = token
        return state

    def heartbeat(self) -> None:
        with self._lock:
            state = self.read_state()
            if state and self.token and state.get("token") == self.token:
                state["lastSeen"] = self.clock()
                self._write_state(state)

    def release(self) -> None:
        """Give the lock back (idempotent)."""
        if not self.token:
            return
        with self._lock:
            state = self.read_state()
            if state and state.get("token") == self.token:
                self._write_state(dict(state, status="idle", host=None, token=None, since=None,
                                       lastHost=self.host, lastSeen=self.clock()))
            self.token = None

    # -- moving the world
    @property
    def _pull_marker(self) -> str:
        return os.path.join(self.server_dir, PULL_MARKER)     # outside the synced folders, so it never travels

    def can_keep_local_world(self) -> bool:
        """After resuming this PC's own lock: the local world is only the newest copy if the last download finished.
        A download that was cut off (window closed, crash) leaves a half-copied world, so it has to be fetched again."""
        return not os.path.exists(self._pull_marker) and os.path.isdir(os.path.join(self.server_dir, "world"))

    def pull(self) -> None:
        """shared -> this PC (server must be stopped)."""
        os.makedirs(self.server_dir, exist_ok=True)
        with open(self._pull_marker, "w") as f:
            f.write("A download from the shared folder started and has not finished.\n")
        self._pull_data()
        os.remove(self._pull_marker)                           # only reached when everything was copied

    def check_before_upload(self) -> None:
        """Refuse to replace the shared world with one that looks incomplete or wrong (e.g. a half-finished download,
        a brand new default world). The shared copy is left untouched."""
        if self.skip_checks:
            return
        problem = upload_problem(world_facts(self._shared_world_paths()),
                                 world_facts(local_world_paths(os.path.join(self.server_dir, "world"))))
        if problem:
            raise HostingError(f"Not uploading: {problem}. It looks incomplete or wrong, so the shared world was left "
                               "untouched. If this is really what you want, set SHARED_SKIP_CHECKS=1 in controller.env.")

    def push(self) -> tuple:
        """this PC -> shared (server must be stopped). Requires the lock."""
        if not self.holds_lock():
            raise HostingError("Lost the hosting lock, not uploading the world.")
        self.check_before_upload()
        self._before_push()
        count, nbytes = self._push_data()
        with self._lock:
            state = self.read_state()
            if not state or state.get("token") != self.token:
                raise HostingError("Lost the hosting lock while uploading.")
            state.pop("pushing", None)
            state.pop("pushingBy", None)
            state.update(version=int(state.get("version", 0)) + 1, count=count, bytes=nbytes, lastPush=self.clock())
            self._write_state(state)
        return count, nbytes


def build_world(cfg):
    """The shared world this PC is configured for (SHARED_REMOTE = rclone, else SHARED_DIR = a synced folder), as
    (world, where), or (None, "") when shared hosting is off."""
    if not (cfg.shared_remote or cfg.shared_dir):
        return None, ""
    import platform
    cfg.host_name = cfg.host_name or platform.node()
    if cfg.shared_remote:
        from .rclone import Rclone
        if cfg.shared_dir:
            log.warning("Both SHARED_REMOTE and SHARED_DIR are set; using SHARED_REMOTE (rclone).")
        world = RcloneWorld(cfg.shared_remote, cfg.server_dir, cfg.host_name, Rclone(cfg.rclone_exe),
                            history_days=cfg.shared_history_days)
        where = cfg.shared_remote
    else:
        world = SharedWorld(cfg.shared_dir, cfg.server_dir, cfg.host_name)
        where = cfg.shared_dir
    world.skip_checks = cfg.shared_skip_checks
    return world, where


class RcloneWorld(SharedWorld):
    """The same shared world, but kept in cloud storage that rclone talks to directly (Google Drive's API, Cloudflare R2,
    Backblaze B2, ...) instead of a folder synced by a desktop app:
      <remote>/state.json    who is hosting (visible to the other PC at once, no sync delay)
      <remote>/data/...      the world, mods, config, passwords (only changed files travel)
      <remote>/history/...   what each upload replaced or deleted, kept for history_days so a bad upload can be undone"""

    def __init__(self, remote: str, server_dir: str, host: str, rclone, clock=time.time, sleep=time.sleep,
                 history_days: int = 7):
        super().__init__(remote, server_dir, host, clock, sleep)
        self.remote, self.rclone, self.history_days = remote, rclone, history_days
        self.state_path = self.data_dir = None          # there are no local files for these

    def _p(self, *parts: str) -> str:
        return rjoin(self.remote, *parts)

    def _unreachable(self, what: str, exc: Exception) -> HostingError:
        return HostingError(f"{what} ({self.remote}): {exc}")

    # -- the lock file
    def read_state(self):
        try:
            raw = self.rclone.cat(self._p("state.json"))
        except RcloneError as exc:
            raise self._unreachable("Can't reach the shared storage", exc) from None
        if raw is None:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise HostingError(f"state.json in the shared storage ({self.remote}) is unreadable. Check it with "
                               "`rclone cat` before starting.") from None

    def _write_state(self, state: dict) -> None:
        try:
            self.rclone.put(self._p("state.json"), json.dumps(state).encode("utf-8"))
        except RcloneError as exc:
            raise self._unreachable("Can't write to the shared storage", exc) from None

    def _prepare(self) -> None:
        if not self.rclone.available:
            raise HostingError("rclone isn't installed on this PC. Install it with: winget install Rclone.Rclone")
        try:
            self.rclone.mkdir(self.remote)
        except RcloneError as exc:
            raise self._unreachable("Can't reach the shared storage", exc) from None

    def _conflict_files(self) -> list:
        try:
            names = [e["Name"] for e in self.rclone.lsjson(self.remote, files_only=True)]
        except RcloneError as exc:
            raise self._unreachable("Can't reach the shared storage", exc) from None
        odd = [n for n in names if n.startswith("state") and n != "state.json"]
        return odd + (["state.json"] if names.count("state.json") > 1 else [])      # Drive allows duplicate names

    # -- what is stored
    def _data_files(self) -> list:
        try:
            entries = self.rclone.lsjson(self._p("data"), recursive=True, files_only=True, excludes=sorted(SKIP_NAMES))
        except RcloneError as exc:
            raise self._unreachable("Can't list the shared world", exc) from None
        return [(e["Path"].replace("\\", "/"), int(e.get("Size", 0))) for e in entries]

    def _remote_manifest(self) -> tuple:
        count = total = 0
        for path, size in self._data_files():
            top = path.split("/", 1)[0]
            if (top in SYNC_DIRS) if "/" in path else (path in SYNC_FILES):
                count += 1
                total += size
        return count, total

    def _shared_world_paths(self) -> list:
        return [p[len("world/"):] for p, _size in self._data_files() if p.startswith("world/")]

    def _wait_for_sync(self, state: dict, takeover: bool, sync_timeout: float) -> None:
        # The storage is consistent (no sync delay), so there is nothing to wait for: it either matches or it doesn't.
        if state.get("resumedOwnLock"):
            return
        who = state.get("host") or state.get("lastHost") or "the last host"
        if state.get("pushing"):
            if state.get("pushingBy") == self.host:
                # our own upload was cut off: this PC still has the complete world, so keep it and upload again
                log.warning("Your last upload never finished; keeping this PC's world and uploading again.")
                state["resumedOwnLock"] = True
                return
            who = state.get("pushingBy") or who
            msg = (f"{who}'s last upload never finished, so the shared world may be a mix of old and new files. Ask them "
                   "to start again and finish it")
            if not takeover:
                raise HostingError(msg + ", or start with -Takeover to use it anyway (their newest changes may be missing).")
            log.warning("%s; continuing because of takeover", msg)
            return
        if state.get("version", 0) > 0:
            count, nbytes = self._remote_manifest()
            if (count, nbytes) != (state.get("count"), state.get("bytes")):
                if takeover:
                    log.warning("Shared storage holds %s files / %s bytes, the last upload recorded %s / %s; continuing "
                                "because of takeover", count, nbytes, state.get("count"), state.get("bytes"))
                    return
                raise HostingError(f"The shared storage doesn't match the last upload ({count} files there, "
                                   f"{state.get('count')} expected). Try again in a minute; if it stays like this, "
                                   f"{who}'s upload was damaged.")

    # -- moving the world
    def _pull_data(self) -> None:
        data = self._p("data")
        try:
            folders = {e["Name"] for e in self.rclone.lsjson(data) if e.get("IsDir")}
            for d in SYNC_DIRS:
                if d in folders:
                    self.rclone.sync(self._p("data", d), os.path.join(self.server_dir, d), excludes=sorted(SKIP_NAMES))
            self.rclone.copy_files(data, self.server_dir, SYNC_FILES)
        except RcloneError as exc:
            raise HostingError(f"Downloading the world failed: {exc}") from None

    def _before_push(self) -> None:
        with self._lock:
            state = self.read_state()
            if not state or state.get("token") != self.token:
                raise HostingError("Lost the hosting lock before uploading.")
            state.update(pushing=self.clock(), pushingBy=self.host)    # cleared when the upload is complete
            self._write_state(state)                                   # (see _wait_for_sync for what the next start does)

    def _push_data(self) -> tuple:
        keep = None
        if self.history_days > 0:
            keep = self._p("history", time.strftime("%Y%m%d-%H%M%S", time.gmtime(self.clock())))
        try:
            for d in SYNC_DIRS:
                src = os.path.join(self.server_dir, d)
                if os.path.isdir(src):
                    self.rclone.sync(src, self._p("data", d), excludes=sorted(SKIP_NAMES),
                                     backup_dir=rjoin(keep, d) if keep else None)
            self.rclone.copy_files(self.server_dir, self._p("data"), SYNC_FILES, backup_dir=keep)
        except RcloneError as exc:
            raise HostingError(f"Uploading the world failed: {exc}. Nothing was marked as uploaded; it will be "
                               "tried again.") from None
        self._prune_history()
        return self._remote_manifest()

    def _prune_history(self) -> None:
        if self.history_days <= 0:
            return
        cutoff = time.strftime("%Y%m%d-%H%M%S", time.gmtime(self.clock() - self.history_days * 86400))
        try:
            for entry in self.rclone.lsjson(self._p("history")):
                name = entry.get("Name", "")
                if entry.get("IsDir") and re.fullmatch(r"\d{8}-\d{6}", name) and name < cutoff:
                    self.rclone.purge(self._p("history", name))
        except RcloneError as exc:
            log.warning("Could not clean up the old upload history (%s)", exc)
