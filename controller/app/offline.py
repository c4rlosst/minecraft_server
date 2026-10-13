"""Operator file for an offline-mode server.

In offline mode a player's UUID is computed from their name. The /op command, however, asks Mojang about the name
and stores the *online* UUID for real accounts, which then never matches the offline UUID the player actually joins
with. So in this mode the controller writes ops.json itself, using the offline UUID."""
import hashlib
import json
import os
import uuid


def offline_uuid(name: str) -> str:
    """Same as Java's UUID.nameUUIDFromBytes(("OfflinePlayer:" + name).getBytes(UTF_8))."""
    digest = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def read_list(path: str) -> list:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def write_list(path: str, entries: list) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(entries, f, indent=2)
    os.replace(tmp, path)


def ensure_ops(path: str, names) -> list:
    """Make sure each name is an operator (level 4) under its offline UUID; keep other existing operators
    and repair any entry whose UUID isn't the offline one."""
    wanted = {n.lower(): n for n in names if n}
    entries = []
    for entry in read_list(path):
        name = entry.get("name", "")
        if name:
            entries.append(dict(entry, uuid=offline_uuid(name)))
            wanted.pop(name.lower(), None)
    for name in wanted.values():
        entries.append({"uuid": offline_uuid(name), "name": name, "level": 4, "bypassesPlayerLimit": False})
    write_list(path, entries)
    return entries
