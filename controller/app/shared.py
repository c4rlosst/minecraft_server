"""Shared hosting: two (or a few) trusted people take turns hosting the same world through a shared folder
(e.g. Google Drive for desktop). Only the lock holder runs the server.

  <shared>/state.json   who is hosting, last heartbeat, and the size/file-count of the last upload
  <shared>/data/...     the world, mods, config, EasyAuth passwords, ops/bans (a mirror of the server folder)

Start: take the lock, wait until the cloud folder has fully synced, pull the world. While hosting: heartbeat every
minute and push after every sleep. Handover: push, release the lock."""
import json
import logging
import os
import shutil
import threading
import time
import uuid

log = logging.getLogger("shared")

SYNC_DIRS = ("world", "mods", "config", "EasyAuth")
SYNC_FILES = ("server.properties", "ops.json", "whitelist.json", "banned-players.json", "banned-ips.json",
              "usercache.json")
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
        self._lock = threading.Lock()   # the heartbeat thread and an upload must not edit state.json at once

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
        if not os.path.isdir(self.shared_dir):
            raise HostingError(f"Shared folder not found: {self.shared_dir} (is Google Drive for desktop running?)")
        os.makedirs(self.data_dir, exist_ok=True)
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

        token = uuid.uuid4().hex
        now = self.clock()
        claim = {k: v for k, v in state.items() if k != "resumedOwnLock"}
        claim.update(status="hosting", host=self.host, token=token, since=now, lastSeen=now)
        claim.setdefault("version", 0)
        self._write_state(claim)
        self.sleep(claim_wait)              # if two people start at once, the loser sees the other's token
        check = self.read_state() or {}
        conflicts = [n for n in os.listdir(self.shared_dir)
                     if n.startswith("state") and n not in ("state.json", "state.json.tmp")]
        if check.get("token") != token or conflicts:
            raise HostingError("Someone else took the lock at the same moment (or Google Drive made a conflict copy of "
                               "state.json). Ask who is hosting, delete any 'state (...)' files, and try again.")
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
    def pull(self) -> None:
        """shared -> this PC (server must be stopped)."""
        for d in SYNC_DIRS:
            src = os.path.join(self.data_dir, d)
            if os.path.isdir(src):
                mirror_dir(src, os.path.join(self.server_dir, d))
        for f in SYNC_FILES:
            copy_file(os.path.join(self.data_dir, f), os.path.join(self.server_dir, f))

    def push(self) -> tuple:
        """this PC -> shared (server must be stopped). Requires the lock."""
        if not self.holds_lock():
            raise HostingError("Lost the hosting lock, not uploading the world.")
        for d in SYNC_DIRS:
            src = os.path.join(self.server_dir, d)
            if os.path.isdir(src):
                mirror_dir(src, os.path.join(self.data_dir, d))
        for f in SYNC_FILES:
            copy_file(os.path.join(self.server_dir, f), os.path.join(self.data_dir, f))
        count, nbytes = manifest(self.data_dir)
        with self._lock:
            state = self.read_state()
            if not state or state.get("token") != self.token:
                raise HostingError("Lost the hosting lock while uploading.")
            state.update(version=int(state.get("version", 0)) + 1, count=count, bytes=nbytes, lastPush=self.clock())
            self._write_state(state)
        return count, nbytes
