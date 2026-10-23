"""Is there a newer SMP Host? Asks GitHub for the latest release (one small request, a few times a day). It never installs
anything: the window offers a button that opens the download page. Turn it off with CHECK_UPDATES=0 in settings.env."""
import json
import re
import urllib.request

LATEST = "https://api.github.com/repos/c4rlosst/minecraft_server/releases/latest"
PAGE = "https://github.com/c4rlosst/minecraft_server/releases/latest"


def parse_version(text) -> tuple:
    """'v1.0.6' -> (1, 0, 6); anything that isn't a version -> ()."""
    found = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", str(text or ""))
    return tuple(int(x) for x in found.groups()) if found else ()


def is_newer(latest, current) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return bool(a) and bool(b) and a > b


def check(current: str, opener=urllib.request.urlopen, timeout: float = 6.0):
    """{'version': '1.0.7', 'url': ...} when a newer release exists, else None (also None when it can't be reached)."""
    request = urllib.request.Request(LATEST, headers={"User-Agent": "smp-host-update-check", "Accept": "application/vnd.github+json"})
    try:
        with opener(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None                                       # offline, rate limited, GitHub down: just say nothing
    tag = data.get("tag_name") or ""
    if not is_newer(tag, current):
        return None
    return {"version": tag.lstrip("vV"), "url": data.get("html_url") or PAGE}
