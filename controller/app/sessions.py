"""Who hosted the shared world, and when: a small log kept next to the world in the shared storage (sessions.json).

Each entry: {"host", "start", "end", "ended", "version", "token"} with times in seconds since 1970. `ended` says how it
finished: "stopped" (Stop hosting / handover, the world was uploaded) or "no stop recorded" (the PC crashed, lost power or
was closed without stopping: `end` is then the last time it was seen alive). Only the host holding the lock writes."""
from datetime import datetime

MAX_SESSIONS = 60
NO_STOP = "no stop recorded"


def begin(sessions: list, host: str, token: str, now: float, previous: dict = None) -> list:
    """The log after `host` took the lock. `previous` is the lock as it was found (a crashed host's open entry gets closed
    at the last time that host was seen; this PC resuming its own lock continues its own entry)."""
    previous = previous or {}
    out = [dict(e) for e in sessions]
    resumed = bool(previous.get("resumedOwnLock"))
    for entry in out:
        if entry.get("end") is not None:
            continue
        if resumed and entry.get("host") == host:
            entry.update(token=token, resumes=int(entry.get("resumes", 0)) + 1)
            return out[-MAX_SESSIONS:]
        seen = float(previous.get("lastSeen") or 0) if entry.get("host") == previous.get("host") else 0.0
        entry.update(end=max(float(entry.get("start", 0)), seen), ended=NO_STOP)
    out.append({"host": host, "start": now, "end": None, "ended": None, "version": None, "token": token})
    return out[-MAX_SESSIONS:]


def finish(sessions: list, token: str, now: float, version=None) -> list:
    out = [dict(e) for e in sessions]
    for entry in reversed(out):
        if entry.get("token") == token and entry.get("end") is None:
            entry.update(end=now, ended="stopped", version=version)
            break
    return out


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%a %d %b")


def duration(seconds: float) -> str:
    minutes = int(max(seconds, 0) // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def when(entry: dict, now: float = None) -> str:
    """'Tue 20 Oct, 6:50 PM to 11:24 PM (4 h 34 min)'; still running: '... to now'."""
    start, end = float(entry["start"]), entry.get("end")
    if end is None:
        return f"{_day(start)}, {_clock(start)} to now ({duration((now or start) - start)})"
    end = float(end)
    same_day = _day(start) == _day(end)
    until = _clock(end) if same_day else f"{_day(end)}, {_clock(end)}"
    return f"{_day(start)}, {_clock(start)} to {until} ({duration(end - start)})"


def line(entry: dict, now: float = None) -> str:
    note = {"stopped": "", NO_STOP: "  [no stop recorded: crashed or closed?]"}.get(entry.get("ended"), "")
    if entry.get("end") is None:
        note = "  [running, or no stop recorded]"
    return f"{entry.get('host', '?')}: {when(entry, now)}{note}"


def row(entry: dict, now: float = None, host_width: int = 18) -> str:
    """One line of the history list, in columns: 'ChrisTzy was h...  Fri 23 Oct 2:13 AM-6:07 AM  3 h 53 min  !'
    (the ! marks a session whose end was never recorded)."""
    start, end = float(entry["start"]), entry.get("end")
    host = str(entry.get("host", "?"))
    if len(host) > host_width:
        host = host[:host_width - 3] + "..."
    if end is None:
        until, length, flag = "now", duration((now or start) - start), ""
    else:
        end = float(end)
        until = _clock(end) if _day(start) == _day(end) else f"{_day(end)} {_clock(end)}"
        length = duration(end - start)
        flag = "  !" if entry.get("ended") == NO_STOP else ""
    return f"{host:<{host_width}}  {_day(start)} {_clock(start)}-{until}  {length}{flag}"


def session_at(sessions: list, ts: float):
    """The session a moment (a backup's time) falls inside, or None."""
    for entry in reversed(sessions):
        end = entry.get("end")
        if float(entry["start"]) - 60 <= ts <= (float(end) + 60 if end is not None else float("inf")):
            return entry
    return None
