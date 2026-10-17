"""Start and stop the playit.gg agent for shared hosting.

With two people taking turns, only the current host may run the agent: both PCs use the same agent identity, so two running
agents would fight over the tunnel. The controller starts it AFTER it has won the hosting lock and stops it on the way out."""
import logging
import os
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


class PlayitDaemon:
    """The desktop app's agent: playitd.exe run as an ordinary child process with its own copy of the secret file, so it
    needs no Windows service and no admin rights. Its own named pipe keeps it apart from an installed playit service."""
    PIPE = r"\\.\pipe\smp-host-playitd"

    def __init__(self, exe: str, secret_path: str, log_path: str = "", popen=subprocess.Popen):
        self.exe, self.secret_path, self.log_path = exe, secret_path, log_path
        self.popen = popen
        self.proc = None

    @property
    def available(self) -> bool:
        return bool(self.exe) and os.path.isfile(self.exe) and os.path.isfile(self.secret_path)

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def command(self) -> list:
        cmd = [self.exe, "--secret-path", self.secret_path, "--socket-path", self.PIPE]
        if self.log_path:
            cmd += ["--log-path", self.log_path]
        return cmd

    def start(self) -> str:
        if not self.available:
            return "not installed"
        if self.is_running():
            return "already running"
        self.proc = self.popen(self.command(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        return "started"

    def stop(self) -> None:
        if not self.is_running():
            return
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
