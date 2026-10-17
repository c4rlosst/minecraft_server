"""Where SMP Host keeps things on this PC, the tools it ships with, and turning an invite into a working setup."""
import ctypes
import json
import os
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .. import identity
from .invite import parse_env

APP_NAME = "SMP Host"
FABRIC_META = "https://meta.fabricmc.net/v2/versions"
USER_AGENT = "smp-host/1.0"
EULA_URL = "https://aka.ms/MinecraftEULA"
REMOTE = "smp"                                                # the rclone remote the app keeps its storage key under
CONTROLLER_DIR = Path(__file__).resolve().parents[2]          # .../controller (the app's code folder, installed or repo)
KEEP, REMOVE = "keep", "remove"                               # what to do with the server picture on save


def data_root() -> str:
    """Per-user data: settings, the server, backups. Survives updates and uninstalls."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.environ.get("SMP_HOST_HOME") or os.path.join(base, APP_NAME)


def install_root() -> str:
    """Where the bundled tools (runtime/, tools/) are: the app folder when installed, else desktop/vendor in the repo."""
    app_dir = CONTROLLER_DIR.parent
    for candidate in (app_dir, app_dir / "desktop" / "vendor"):
        if (candidate / "tools").is_dir() or (candidate / "runtime").is_dir():
            return str(candidate)
    return str(app_dir / "desktop" / "vendor")


@dataclass
class Tools:
    java: str
    rclone: str
    playitd: str

    def missing(self) -> list:
        return [name for name, path in (("Java", self.java), ("rclone", self.rclone)) if not path]


def find_tools(root: str = None) -> Tools:
    root = root or install_root()

    def first(*paths):
        for p in paths:
            if p and os.path.isfile(p):
                return p
        return ""
    return Tools(
        java=first(os.path.join(root, "runtime", "bin", "java.exe"), shutil.which("java")),
        rclone=first(os.path.join(root, "tools", "rclone.exe"), shutil.which("rclone")),
        playitd=first(os.path.join(root, "tools", "playitd.exe"),
                      os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "playit_gg", "bin", "playitd.exe")),
    )


def total_ram_gb() -> float:
    if sys.platform != "win32":
        return 0.0

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        return 0.0
    return stat.ullTotalPhys / 1024 ** 3


def fit_memory(wanted: str, total_gb: float) -> str:
    """The server's memory (e.g. '6G'), lowered on a PC that can't spare it (leave ~3 GB for Windows and the game)."""
    try:
        want = int(str(wanted).upper().rstrip("G"))
    except ValueError:
        want = 6
    if total_gb <= 0:
        return f"{want}G"
    return f"{max(2, min(want, int(total_gb) - 3))}G"


class Home:
    def __init__(self, root: str = None):
        self.root = root or data_root()
        self.server_dir = os.path.join(self.root, "server")
        self.state_dir = os.path.join(self.root, "state")
        self.backup_dir = os.path.join(self.root, "backups")
        self.settings_path = os.path.join(self.root, "settings.env")
        self.playit_secret = os.path.join(self.root, "playit", "playit.toml")
        self.rclone_conf = os.path.join(self.root, "rclone", "rclone.conf")
        self.log_path = os.path.join(self.state_dir, "controller.log")

    # -- settings
    def configured(self) -> bool:
        return os.path.isfile(self.settings_path)

    def settings(self) -> dict:
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                return parse_env(f.read())
        except OSError:
            return {}

    def panel_port(self) -> int:
        return int(self.settings().get("PANEL_PORT") or 8080)

    def _write(self, path: str, text: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)

    def apply_invite(self, invite: dict, host_name: str, total_gb: float = None) -> dict:
        """Write this PC's settings, storage key and tunnel identity from an invite. Returns the settings."""
        host_name = host_name.strip()
        if not host_name:
            raise ValueError("Pick a name for this PC.")
        env = dict(invite["env"])
        storage = invite["storage"]
        if storage["type"] == "b2":
            self._write(self.rclone_conf, f"[{REMOTE}]\ntype = b2\naccount = {storage['account']}\n"
                                          f"key = {storage['key']}\nhard_delete = true\n")
            env["SHARED_REMOTE"] = f"{REMOTE}:{storage['bucket']}"
        else:                                        # any rclone remote (also how the tests use a plain folder)
            self._write(self.rclone_conf, storage.get("rclone_conf", ""))
            env["SHARED_REMOTE"] = storage["remote"]
        env["HOST_NAME"] = host_name
        env["MC_MEMORY"] = fit_memory(env.get("MC_MEMORY", "6G"), total_ram_gb() if total_gb is None else total_gb)
        for key, value in (self.settings().items()):  # keep this PC's own extra settings (ports, voice, ...) on re-setup
            if key not in env and key not in ("SHARED_REMOTE",):
                env[key] = value
        if invite.get("playit"):
            self._write(self.playit_secret, invite["playit"])
        self._write_settings(env)
        for d in (self.server_dir, self.state_dir, self.backup_dir):
            os.makedirs(d, exist_ok=True)
        return env

    def _write_settings(self, env: dict) -> None:
        lines = [f"# {APP_NAME} settings for this PC (made from an invite). Keep this file private."]
        lines += [f"{k}={v}" for k, v in sorted(env.items())]
        self._write(self.settings_path, "\n".join(lines) + "\n")

    def update_settings(self, **values) -> dict:
        env = self.settings()
        env.update({k: str(v) for k, v in values.items()})
        self._write_settings(env)
        return env

    def make_invite_for(self, bucket: str, key_id: str, key: str) -> str:
        """An invite for a friend: this PC's shared settings and tunnel identity, with the friend's own storage key."""
        from .invite import make
        playit = ""
        if os.path.isfile(self.playit_secret):
            with open(self.playit_secret, encoding="utf-8") as f:
                playit = f.read()
        shared = self.settings()
        face = self.identity()                         # the name/description as they are now, not as at setup
        shared.update(SERVER_NAME=face["name"], SERVER_DESCRIPTION=face["description"])
        return make(shared, playit, {"type": "b2", "bucket": bucket.strip(), "account": key_id.strip(),
                                     "key": key.strip()})

    def uses_tunnel(self, tools: Tools) -> bool:
        """True when hosting here would start the app's own playit agent (an invite that carries a tunnel identity)."""
        return bool(tools.playitd) and os.path.isfile(self.playit_secret)

    # -- the server's name, description and picture (see identity.py)
    def identity(self) -> dict:
        """What this PC shows for the server: identity.json (kept in step with the shared storage by the controller),
        else the settings the invite brought. Always has name, description and icon (a path or None)."""
        ident = identity.read_local(self.server_dir)
        if not ident:
            settings = self.settings()
            ident = identity.make(settings.get("SERVER_NAME") or "Minecraft server", settings.get("SERVER_DESCRIPTION", ""),
                                  False, 0)
        icon = os.path.join(self.server_dir, identity.ICON_FILE)
        ident["icon_path"] = icon if identity.read_icon(self.server_dir) is not None else None
        return ident

    def identity_stamp(self):
        """Changes whenever the name, description or picture changes (to refresh the window)."""
        stamp = []
        for name in (identity.IDENTITY_FILE, identity.ICON_FILE):
            try:
                st = os.stat(os.path.join(self.server_dir, name))
                stamp.append((st.st_mtime_ns, st.st_size))
            except OSError:
                stamp.append(None)
        return tuple(stamp)

    def save_identity(self, name: str, description: str = "", picture=KEEP) -> dict:
        """Save the server's name/description/picture on this PC. picture: KEEP, REMOVE, a file path, or the PNG bytes
        (already 64x64). Raises ValueError with a plain message. Sharing it is sync_identity()."""
        name = identity.clean(name, identity.MAX_NAME)
        if not name:
            raise ValueError("The server needs a name.")
        description = identity.clean(description, identity.MAX_DESCRIPTION)
        if picture == REMOVE:
            identity.write_icon(self.server_dir, None)
        elif picture != KEEP:
            if not isinstance(picture, (bytes, bytearray)):
                from .icon import make_icon
                picture = make_icon(picture)
            identity.write_icon(self.server_dir, bytes(picture))
        ident = identity.make(name, description, identity.read_icon(self.server_dir) is not None)
        identity.write_local(self.server_dir, ident)
        self.update_settings(SERVER_NAME=name, SERVER_DESCRIPTION=description)
        return ident

    def sync_identity(self, world) -> dict:
        """Newest wins between this PC and the shared storage (publishes a change made here, takes a newer one)."""
        return identity.sync(world, self.server_dir)

    # -- the Minecraft server files that don't come from the shared world
    def has_launcher(self) -> bool:
        return os.path.isfile(os.path.join(self.server_dir, "fabric-server-launch.jar"))

    def install_server(self, mc_version: str, accept_eula: bool, report=lambda msg: None, opener=None) -> None:
        os.makedirs(self.server_dir, exist_ok=True)
        if not self.has_launcher():
            opener = opener or _fetch
            report("Finding the newest Fabric version...")
            loader = _first_stable(json.loads(opener(f"{FABRIC_META}/loader")))
            installer = _first_stable(json.loads(opener(f"{FABRIC_META}/installer")))
            report(f"Downloading the Fabric server for Minecraft {mc_version}...")
            data = opener(f"{FABRIC_META}/loader/{mc_version}/{loader}/{installer}/server/jar")
            jar = os.path.join(self.server_dir, "fabric-server-launch.jar")
            with open(jar + ".tmp", "wb") as f:
                f.write(data)
            os.replace(jar + ".tmp", jar)
        if accept_eula:
            self._write(os.path.join(self.server_dir, "eula.txt"), "eula=true\n")

    # -- running the controller
    def controller_env(self, tools: Tools) -> dict:
        env = dict(os.environ)
        env.update(BACKEND="process", MC_HOST="127.0.0.1", MC_PORT="25566", LISTEN_HOST="127.0.0.1",
                   PANEL_HOST="127.0.0.1", SERVER_DIR=self.server_dir,
                   WHITELIST_PATH=os.path.join(self.server_dir, "whitelist.json"), STATE_DIR=self.state_dir,
                   BACKUP_DIR=self.backup_dir, LOG_FILE=self.log_path, JAVA_PATH=tools.java or "java",
                   RCLONE_EXE=tools.rclone or "rclone", RCLONE_CONFIG=self.rclone_conf, PYTHONUNBUFFERED="1",
                   PLAYIT_EXE="", PLAYIT_SECRET_PATH="")
        if tools.playitd and os.path.isfile(self.playit_secret):
            env.update(PLAYIT_EXE=tools.playitd, PLAYIT_SECRET_PATH=self.playit_secret)
        env.update(self.settings())                   # this PC's settings win (ports, memory, ...)
        return env


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _first_stable(entries: list) -> str:
    for entry in entries:
        if entry.get("stable"):
            return entry["version"]
    raise RuntimeError("Couldn't find a stable Fabric version (is the internet working?)")
