"""The server's face: name, description and picture, as shown in players' server lists (standard library only).

  <server folder>/identity.json     {"name", "description", "icon": true/false, "updated": <epoch seconds>}
  <server folder>/server-icon.png   the 64x64 picture Minecraft shows next to the name
  server.properties  motd=          what the real server shows when it is awake (written from the two above at each wake)

With shared hosting the same three live in the shared storage too (identity.json + server-icon.png next to state.json),
so whoever hosts shows the same face. The newest one wins (sync())."""
import base64
import json
import logging
import os
import re
import struct
import tempfile
import time

log = logging.getLogger("identity")

IDENTITY_FILE = "identity.json"
ICON_FILE = "server-icon.png"
MAX_NAME, MAX_DESCRIPTION = 32, 60
MAX_ICON_BYTES = 30000          # the whole server-list reply must stay small; a 64x64 PNG is normally 2-10 KB
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


# ---- text -----------------------------------------------------------------------------------------------
def clean(text, limit: int) -> str:
    """One line of plain text: no formatting codes (a section sign and the character after it), line breaks or control
    characters."""
    text = re.sub("§.?", "", str(text or ""))
    text = "".join(ch if ch.isprintable() or ch == " " else " " for ch in text)
    return " ".join(text.split())[:limit].strip()


def motd_text(name: str, description: str = "") -> str:
    """What a server list shows: the name in bold on line 1, the description under it."""
    text = f"§l{name}§r"
    return text + (f"\n§7{description}" if description else "")


def properties_escape(value: str) -> str:
    """A value the way Java's Properties writes it: \\n for new lines and \\uXXXX for everything that isn't plain ASCII
    (Minecraft reads server.properties as Latin-1, so a raw UTF-8 section sign would turn into garbage)."""
    out = []
    for ch in value:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif code < 0x20 or code > 0x7e:
            if code > 0xFFFF:                       # outside the BMP: a UTF-16 surrogate pair
                code -= 0x10000
                out.append("\\u%04X\\u%04X" % (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)))
            else:
                out.append("\\u%04X" % code)
        else:
            out.append(ch)
    return "".join(out)


def properties_unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch != "\\" or i + 1 >= len(value):
            out.append(ch)
            i += 1
            continue
        nxt = value[i + 1]
        if nxt == "u" and len(value) >= i + 6:
            try:
                out.append(chr(int(value[i + 2:i + 6], 16)))
                i += 6
                continue
            except ValueError:
                pass
        out.append({"n": "\n", "r": "\r", "t": "\t", "f": "\f"}.get(nxt, nxt))
        i += 2
    # 😀 (a surrogate pair) -> one character
    return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def set_motd(server_dir: str, name: str, description: str = "") -> bool:
    """Write motd= into server.properties (never creating the file). True when the file changed."""
    path = os.path.join(server_dir, "server.properties")
    try:
        with open(path, encoding="latin-1", newline="") as f:
            lines = f.read().splitlines()
    except OSError:
        return False
    wanted = "motd=" + properties_escape(motd_text(name, description))
    changed, found = False, False
    for i, line in enumerate(lines):
        if line.startswith("motd="):
            found = True
            if line != wanted:
                lines[i], changed = wanted, True
    if not found:
        lines.append(wanted)
        changed = True
    if changed:
        _write_text(path, "\n".join(lines) + "\n", "latin-1")
    return changed


# ---- the picture ------------------------------------------------------------------------------------------
def icon_ok(data: bytes) -> bool:
    """A PNG of exactly 64x64 (anything else is ignored by Minecraft) and not huge."""
    if len(data) > MAX_ICON_BYTES or not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        return False
    return struct.unpack(">II", data[16:24]) == (64, 64)


def icon_data_uri(path: str, _cache={}):
    """data:image/png;base64,... for the server list, or None. Re-read only when the file changes."""
    try:
        stamp = os.stat(path)
        key = (stamp.st_mtime_ns, stamp.st_size)
        cached = _cache.get(path)
        if cached and cached[0] == key:
            return cached[1]
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        _cache.pop(path, None)
        return None
    uri = "data:image/png;base64," + base64.b64encode(data).decode("ascii") if icon_ok(data) else None
    _cache[path] = (key, uri)
    return uri


# ---- the local record -----------------------------------------------------------------------------------
def _write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def make(name: str, description: str = "", icon: bool = False, updated: float = None) -> dict:
    return {"name": clean(name, MAX_NAME), "description": clean(description, MAX_DESCRIPTION), "icon": bool(icon),
            "updated": time.time() if updated is None else float(updated)}


def parse(raw) -> dict:
    """An identity dict from JSON text/bytes, or None when it isn't one."""
    try:
        data = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
        if not isinstance(data, dict) or not clean(data.get("name"), MAX_NAME):
            return None
        return make(data["name"], data.get("description", ""), data.get("icon", False), data.get("updated", 0))
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def read_local(server_dir: str):
    try:
        with open(os.path.join(server_dir, IDENTITY_FILE), "rb") as f:
            return parse(f.read())
    except OSError:
        return None


def write_local(server_dir: str, ident: dict) -> None:
    _write_text(os.path.join(server_dir, IDENTITY_FILE), json.dumps(ident, ensure_ascii=False, indent=1) + "\n")


def read_icon(server_dir: str):
    try:
        with open(os.path.join(server_dir, ICON_FILE), "rb") as f:
            data = f.read()
    except OSError:
        return None
    return data if icon_ok(data) else None


def write_icon(server_dir: str, data) -> None:
    """Save the picture (bytes), or remove it (None)."""
    path = os.path.join(server_dir, ICON_FILE)
    if data is None:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    if not icon_ok(data):
        raise ValueError("The picture must be a 64x64 PNG.")
    os.makedirs(server_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


# ---- keeping every host the same ------------------------------------------------------------------------
def sync(world, server_dir: str):
    """Newest wins between this PC's identity and the shared storage's. Returns the identity in force (or None).
    `world` offers read_identity() / read_shared_icon() / write_identity(identity, icon_bytes)."""
    local = read_local(server_dir)
    remote = world.read_identity()
    if remote and (not local or remote["updated"] > local["updated"]):
        write_local(server_dir, remote)
        if remote["icon"]:
            data = world.read_shared_icon()
            if data and icon_ok(data):
                write_icon(server_dir, data)
        else:
            write_icon(server_dir, None)
        log.info("Server name/description/picture updated from the shared storage: %s", remote["name"])
        return remote
    if local and (not remote or local["updated"] > remote["updated"]):
        world.write_identity(local, read_icon(server_dir) if local["icon"] else None)
        log.info("Published this PC's server name/description/picture to the shared storage: %s", local["name"])
    return local or remote


def apply(cfg) -> dict:
    """Use the local identity (if there is one) for this controller: the server-list name and description, and the
    motd of the real server. Returns the identity or None (then the env settings stay as they are)."""
    if not cfg.server_dir:
        return None
    ident = read_local(cfg.server_dir)
    if ident:
        cfg.server_name, cfg.server_description = ident["name"], ident["description"]
    return ident


def apply_to_server(cfg) -> None:
    """Called just before the Minecraft server starts: put the identity into server.properties."""
    if cfg.server_dir and read_local(cfg.server_dir):
        if set_motd(cfg.server_dir, cfg.server_name, cfg.server_description):
            log.info("Server list text set: %s", cfg.server_name)
