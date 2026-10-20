"""A thin wrapper around the rclone command line (https://rclone.org), used by shared hosting instead of a synced folder.

Talking to the storage directly (Google Drive's API, Cloudflare R2, Backblaze B2, ...) means a lock written by one PC is
seen by the other at once, and only files that changed are transferred. The remote is whatever `rclone config` set up,
written the rclone way: "name:folder/sub" (a plain local folder works too, which is how the tests run)."""
import json
import logging
import shutil
import subprocess
import threading

log = logging.getLogger("rclone")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NOT_FOUND = (3, 4)      # rclone exit codes: directory not found, file not found


class RcloneError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def join(base: str, *parts: str) -> str:
    """remote:folder + 'data' + 'world' -> remote:folder/data/world (and 'remote:' + 'x' -> 'remote:x')."""
    path = base
    for part in parts:
        path = path + part if path.endswith((":", "/", "\\")) else path + "/" + part
    return path


class Rclone:
    def __init__(self, exe: str = "rclone", run=subprocess.run, which=shutil.which):
        self.path = which(exe) if exe else None
        self.run = run

    @property
    def available(self) -> bool:
        return bool(self.path)

    def _call(self, *args: str, data: bytes | None = None, timeout: int = 3600, ok=(0,)):
        if not self.path:
            raise RcloneError("rclone is not installed (try: winget install Rclone.Rclone)")
        try:
            done = self.run([self.path, *args], input=data, capture_output=True, timeout=timeout,
                            creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise RcloneError(f"rclone {args[0]} timed out after {timeout}s")
        if done.returncode not in ok:
            lines = (done.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            raise RcloneError(" | ".join(lines[-3:]) or f"rclone {args[0]} failed", done.returncode)
        return done

    def _stream(self, *args: str, on_stats, timeout: int = 3600):
        """Run rclone and hand its progress numbers ({'bytes', 'totalBytes', 'transfers', ...}, about twice a second) to
        on_stats while it works. Raises RcloneError like _call when it fails."""
        if not self.path:
            raise RcloneError("rclone is not installed (try: winget install Rclone.Rclone)")
        cmd = [self.path, *args, "--use-json-log", "--stats", "500ms", "--stats-log-level", "NOTICE"]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        timer = threading.Timer(timeout, proc.kill)
        timer.start()
        problems = []
        try:
            for raw in proc.stderr:
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(entry.get("stats"), dict):
                    try:
                        on_stats(entry["stats"])
                    except Exception:
                        log.debug("progress callback failed", exc_info=True)
                elif entry.get("level") in ("error", "critical", "fatal") and entry.get("msg"):
                    problems.append(str(entry["msg"]))
            code = proc.wait()
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
        if code != 0:
            raise RcloneError(" | ".join(problems[-3:]) or f"rclone {args[0]} failed", code)

    def _run(self, *args: str, progress=None, timeout: int = 3600):
        if progress is None:
            self._call(*args, timeout=timeout)
        else:
            self._stream(*args, on_stats=progress, timeout=timeout)

    # -- small files (the lock) -----------------------------------------------------------------------
    def cat(self, remote_path: str) -> bytes | None:
        """The file's contents, or None if it doesn't exist. Some storage (Backblaze B2) answers a missing file with
        'success, nothing': none of the files we read are ever empty, so an empty answer also means 'not there'."""
        done = self._call("cat", remote_path, timeout=120, ok=(0, *NOT_FOUND))
        return done.stdout if done.returncode == 0 and done.stdout else None

    def put(self, remote_path: str, data: bytes) -> None:
        self._call("rcat", remote_path, data=data, timeout=120)

    # -- folders ---------------------------------------------------------------------------------------
    def mkdir(self, remote_path: str) -> None:
        self._call("mkdir", remote_path, timeout=120)

    def lsjson(self, remote_path: str, recursive: bool = False, files_only: bool = False, excludes=()) -> list:
        args = ["lsjson", remote_path]
        if recursive:
            args.append("-R")
        if files_only:
            args.append("--files-only")
        for pattern in excludes:
            args += ["--exclude", pattern]
        done = self._call(*args, timeout=600, ok=(0, *NOT_FOUND))
        return json.loads(done.stdout or b"[]") if done.returncode == 0 else []

    def sync(self, src: str, dst: str, excludes=(), backup_dir: str | None = None, transfers: int = 8,
             progress=None) -> None:
        """Make dst identical to src. With backup_dir, files that get replaced or deleted are kept there."""
        args = ["sync", src, dst, "--transfers", str(transfers), "--checkers", "16", "--fast-list"]
        for pattern in excludes:
            args += ["--exclude", pattern]
        if backup_dir:
            args += ["--backup-dir", backup_dir]
        self._run(*args, progress=progress)

    def copy(self, src: str, dst: str, excludes=(), transfers: int = 8) -> None:
        """Copy everything from src into dst (between two different storages too). Never deletes anything."""
        args = ["copy", src, dst, "--transfers", str(transfers), "--checkers", "16", "--fast-list"]
        for pattern in excludes:
            args += ["--exclude", pattern]
        self._call(*args, timeout=4 * 3600)

    def copy_files(self, src: str, dst: str, names, backup_dir: str | None = None, progress=None) -> None:
        """Copy only these files from the top level of src (the ones that exist), never deleting anything."""
        args = ["copy", src, dst, "--max-depth", "1"]
        for name in names:
            args += ["--include", "/" + name]
        if backup_dir:
            args += ["--backup-dir", backup_dir]
        self._run(*args, progress=progress)

    def purge(self, remote_path: str) -> None:
        """Delete a folder and everything in it (a missing folder is fine)."""
        self._call("purge", remote_path, ok=(0, *NOT_FOUND))
