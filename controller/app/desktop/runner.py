"""Starting and stopping the controller from the desktop app, and talking to it through its (local) panel API."""
import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request

from .home import CONTROLLER_DIR, Home, Tools

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LONG_WAIT = 900          # seconds: stopping saves the world and uploads it, which can take minutes on a slow connection


class Panel:
    """The controller's JSON API on 127.0.0.1 (the same one the web panel uses)."""

    def __init__(self, port: int, password: str, host: str = "127.0.0.1", timeout: float = 10):
        self.base, self.password, self.timeout = f"http://{host}:{port}", password, timeout
        self.host, self.port = host, port
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def listening(self) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=1):
                return True
        except OSError:
            return False

    def _request(self, method: str, path: str, body=None, timeout: float = None):
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=timeout or self.timeout) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read() or b"{}")
            except ValueError:
                return exc.code, {}
            finally:
                exc.close()

    def call(self, method: str, path: str, body=None, timeout: float = None):
        status, data = self._request(method, path, body, timeout)
        if status == 401:                        # not logged in yet (or the controller restarted): log in once
            status, data = self._request("POST", "/api/login", {"password": self.password})
            if status != 200:
                return status, data
            status, data = self._request(method, path, body, timeout)
        return status, data

    def status(self):
        """The controller's status, or None when it isn't running."""
        try:
            status, data = self.call("GET", "/api/status")
        except OSError:
            return None
        return data if status == 200 else None

    def handover(self):
        """(True, '') when the controller is uploading and will close; (False, why) otherwise."""
        status, data = self.call("POST", "/api/handover", timeout=LONG_WAIT)     # it answers when the upload is done
        return status == 200, data.get("error", "")

    def quit(self) -> bool:
        status, _data = self.call("POST", "/api/quit", timeout=LONG_WAIT)
        return status == 200

    def reload_identity(self) -> bool:
        """The name/description/picture changed on disk: have the running controller use them now."""
        status, _data = self.call("POST", "/api/reload-identity")
        return status == 200

    def start_server(self) -> bool:
        status, _data = self.call("POST", "/api/start")
        return status == 200


def controller_command() -> tuple:
    """(command, working folder) to run the controller as a background process: the same Python as this window
    (the installed app's pythonw.exe), started inside the controller folder so that `app` can be imported."""
    return [sys.executable, "-m", "app.main"], str(CONTROLLER_DIR)


class Controller:
    """The controller process this app started."""

    def __init__(self, home: Home, tools: Tools, popen=subprocess.Popen):
        self.home, self.tools, self.popen = home, tools, popen
        self.proc = None

    def start(self):
        os.makedirs(self.home.state_dir, exist_ok=True)
        cmd, cwd = controller_command()
        console = open(os.path.join(self.home.state_dir, "controller-console.log"), "ab")
        try:
            self.proc = self.popen(cmd, cwd=cwd, env=self.home.controller_env(self.tools), stdin=subprocess.DEVNULL,
                                   stdout=console, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        finally:
            console.close()                      # the child has its own handle now
        return self.proc

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def exit_code(self):
        return None if self.proc is None else self.proc.poll()

    def kill(self) -> None:
        if self.running():
            self.proc.kill()


def last_error(log_path: str, since: int = 0) -> str:
    """The last ERROR line the controller wrote after byte offset `since` (to explain why it stopped)."""
    try:
        with open(log_path, "rb") as f:
            f.seek(since)
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        if " ERROR " in line:
            return line.split(": ", 1)[-1].strip()
    return ""


def log_size(log_path: str) -> int:
    try:
        return os.path.getsize(log_path)
    except OSError:
        return 0
