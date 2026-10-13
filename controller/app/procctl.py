"""Runs the Minecraft server as a plain Java process (Windows/Linux, no Docker). Same interface as
dockerctl.Docker: state / start / stop / logs. Methods are blocking; callers run them in a thread."""
import asyncio
import logging
import os
import subprocess
import sys
import time

from .rcon import rcon

log = logging.getLogger("procctl")
IS_WINDOWS = sys.platform == "win32"
JVM_FLAGS = ["-XX:+UseG1GC", "-XX:+ParallelRefProcEnabled", "-XX:MaxGCPauseMillis=200", "-XX:+DisableExplicitGC"]


def _alive(pid: int) -> bool:
    if IS_WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)   # signal 0 only checks existence on POSIX
        return True
    except OSError:
        return False


def _kill(pid: int):
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        os.kill(pid, 9)


class ProcessServer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.pid_file = os.path.join(cfg.state_dir, "mc.pid")

    # The command is a method so tests can swap in a harmless stand-in for Java.
    def _command(self) -> list[str]:
        jar = os.path.join(self.cfg.server_dir, "fabric-server-launch.jar")
        if not os.path.exists(jar):
            raise RuntimeError(f"{jar} not found. Run setup.ps1 first.")
        ram = self.cfg.mc_memory
        return [self.cfg.java_path, f"-Xms{ram}", f"-Xmx{ram}", *JVM_FLAGS, "-jar", "fabric-server-launch.jar", "nogui"]

    def _read_pid(self):
        try:
            with open(self.pid_file) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    def _clear_pid(self):
        try:
            os.remove(self.pid_file)
        except OSError:
            pass

    def state(self, name: str = "") -> str:
        pid = self._read_pid()
        if pid is None:
            return "exited"
        if _alive(pid):
            return "running"
        self._clear_pid()
        return "exited"

    def start(self, name: str = "") -> None:
        if self.state() == "running":
            return
        command = self._command()
        os.makedirs(self.cfg.state_dir, exist_ok=True)
        os.makedirs(os.path.join(self.cfg.server_dir, "logs"), exist_ok=True)
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if IS_WINDOWS else 0
        with open(os.path.join(self.cfg.server_dir, "logs", "console.out"), "ab") as out:
            proc = subprocess.Popen(command, cwd=self.cfg.server_dir, stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=out, creationflags=flags)
        with open(self.pid_file, "w") as f:
            f.write(str(proc.pid))
        log.info("Started server process %s", proc.pid)

    def stop(self, name: str = "", grace: int = 120) -> None:
        pid = self._read_pid()
        if pid is None or not _alive(pid):
            self._clear_pid()
            return
        try:  # graceful: RCON "stop" makes the server save the world and exit
            asyncio.run(rcon(self.cfg.mc_host, self.cfg.rcon_port, self.cfg.rcon_password, "stop", timeout=10))
        except Exception as exc:
            log.warning("RCON stop failed (%s); the world may not be saved cleanly", exc)
            grace = min(grace, 5)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and _alive(pid):
            time.sleep(1)
        if _alive(pid):
            log.warning("Server did not exit in %ss, killing it", grace)
            _kill(pid)
        self._clear_pid()

    def logs(self, name: str = "", tail: int = 200) -> str:
        path = os.path.join(self.cfg.server_dir, "logs", "latest.log")
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 128 * 1024))
                data = f.read().decode("utf-8", "replace")
        except OSError as exc:
            return f"(no log yet: {exc})"
        return "\n".join(data.splitlines()[-int(tail):])
