"""Simple Voice Chat needs clients to know where the voice server is (UDP). With shared hosting that is the
current host's home address, so the controller writes it before every server start."""
import logging
import os
import time
import urllib.request

log = logging.getLogger("voice")
CONFIG = os.path.join("config", "voicechat", "voicechat-server.properties")
_cache = {"ip": None, "at": 0.0}


def public_ip(fetch=None) -> str:
    if _cache["ip"] and time.time() - _cache["at"] < 600:
        return _cache["ip"]
    if fetch is None:
        def fetch():
            with urllib.request.urlopen("https://api.ipify.org", timeout=10) as response:
                return response.read().decode().strip()
    ip = fetch()
    _cache.update(ip=ip, at=time.time())
    return ip


def resolve(setting: str, fetch=None):
    """'' -> None (leave the file alone), 'off' -> '', 'auto' -> '<public ip>:24454', anything else as given."""
    setting = (setting or "").strip()
    if not setting:
        return None
    if setting.lower() == "off":
        return ""
    if setting.lower() == "auto":
        return f"{public_ip(fetch)}:24454"
    return setting


def apply(server_dir: str, setting: str, fetch=None) -> bool:
    """Write voice_host into the voice chat config if it exists. Returns True if the file was changed."""
    value = resolve(setting, fetch)
    path = os.path.join(server_dir, CONFIG)
    if value is None or not os.path.isfile(path):
        return False
    with open(path) as f:
        lines = f.read().splitlines()
    out, found = [], False
    for line in lines:
        if line.startswith("voice_host="):
            out.append(f"voice_host={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"voice_host={value}")
    new = "\n".join(out) + "\n"
    with open(path) as f:
        if f.read() == new:
            return False
    with open(path, "w") as f:
        f.write(new)
    log.info("voice_host set to %r", value)
    return True
