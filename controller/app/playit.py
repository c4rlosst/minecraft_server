"""Start and stop the playit.gg agent for shared hosting.

With two people taking turns, only the current host may run the agent: both PCs use the same agent identity, so two running
agents would fight over the tunnel. The controller starts it AFTER it has won the hosting lock and stops it on the way out."""
import logging
import shutil
import subprocess

log = logging.getLogger("playit")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Playit:
    def __init__(self, exe: str, run=subprocess.run, popen=subprocess.Popen, which=shutil.which):
        self.path = which(exe) if exe else None
        self.run, self.popen = run, popen
        self.proc = None

    @property
    def available(self) -> bool:
        return bool(self.path)

    def is_running(self) -> bool:
        try:
            out = self.run([self.path, "status"], capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW).stdout
        except Exception:
            return False
        return "Phase: running" in (out or "")

    def start(self) -> str:
        if not self.available:
            return "not installed"
        if self.is_running():
            return "already running"
        self.proc = self.popen([self.path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        return "started"

    def stop(self) -> None:
        if not self.available:
            return
        try:
            self.run([self.path, "stop"], capture_output=True, timeout=30, creationflags=NO_WINDOW)
        except Exception as exc:
            log.warning("Could not stop the playit service (%s). If it keeps running, run `playit stop`.", exc)
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
