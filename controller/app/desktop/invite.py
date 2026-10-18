"""Invites: everything a new host's PC needs, in one line of text to send privately.

An invite holds the shared storage and that person's own key, the server settings every host shares (passwords, Discord
bot, address, ...) and the playit tunnel identity. Anyone holding it can host the world, so it is as private as a password.

  python -m app.desktop.invite make --env ..\\controller.env --playit C:\\ProgramData\\playit_gg\\playit.toml \\
      --bucket my-world-4821 --key-id <friend's keyID> --key <friend's applicationKey>"""
import argparse
import base64
import json
import sys
import zlib

PREFIX = "SMP1."
# controller.env settings every host shares. Per-PC ones (HOST_NAME, paths, voice, auto shutdown) are never included.
SHARED_KEYS = ("RCON_PASSWORD", "SERVER_NAME", "SERVER_DESCRIPTION", "PUBLIC_ADDRESS", "OPS",
               "ACCOUNT_MODE", "MC_VERSION",
               "MC_MEMORY", "IDLE_MINUTES", "START_GRACE_MINUTES", "BACKUPS_KEEP", "SHARED_HISTORY_DAYS",
               "DISCORD_TOKEN", "DISCORD_GUILD_ID", "DISCORD_CHANNEL_ID", "DISCORD_ROLE_ID", "DISCORD_BLOCKED_IDS")
REQUIRED_KEYS = ("RCON_PASSWORD",)


class InviteError(Exception):
    """Shown to the person setting up: what is wrong with the invite."""


def parse_env(text: str) -> dict:
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def make(env: dict, playit_toml: str, storage: dict) -> str:
    missing = [k for k in REQUIRED_KEYS if not env.get(k)]
    if missing:
        raise InviteError(f"controller.env has no {', '.join(missing)}.")
    if storage.get("type") == "b2" and not all(storage.get(k) for k in ("bucket", "account", "key")):
        raise InviteError("The bucket name, keyID and applicationKey are all needed.")
    data = {"v": 1, "env": {k: env[k] for k in SHARED_KEYS if env.get(k)}, "storage": storage,
            "playit": playit_toml or ""}
    packed = zlib.compress(json.dumps(data, separators=(",", ":")).encode("utf-8"), 9)
    return PREFIX + base64.urlsafe_b64encode(packed).decode("ascii").rstrip("=")


def read(code: str) -> dict:
    code = "".join((code or "").split())            # pasted from a chat: drop spaces and line breaks
    if not code.startswith(PREFIX):
        raise InviteError(f"That isn't an invite. It starts with {PREFIX} Copy the whole thing.")
    body = code[len(PREFIX):]
    try:
        data = json.loads(zlib.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))).decode("utf-8"))
    except Exception:
        raise InviteError("The invite is incomplete or damaged. Copy the whole thing again.") from None
    if data.get("v") != 1:
        raise InviteError("This invite is from a newer version of SMP Host. Update the app.")
    storage = data.get("storage") or {}
    if storage.get("type") == "b2":
        if not all(storage.get(k) for k in ("bucket", "account", "key")):
            raise InviteError("The invite is missing its storage key.")
    elif storage.get("type") == "remote":
        if not storage.get("remote"):
            raise InviteError("The invite is missing its storage.")
    else:
        raise InviteError("The invite has an unknown kind of storage.")
    if not all((data.get("env") or {}).get(k) for k in REQUIRED_KEYS):
        raise InviteError("The invite is missing the server passwords.")
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Make an SMP Host invite for a friend (send it privately).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("make")
    mk.add_argument("--env", required=True, help="the owner's controller.env")
    mk.add_argument("--playit", required=True, help="the playit identity file (playit.toml)")
    mk.add_argument("--bucket", required=True)
    mk.add_argument("--key-id", required=True, help="the friend's own B2 keyID")
    mk.add_argument("--key", required=True, help="the friend's own B2 applicationKey")
    args = parser.parse_args(argv)
    with open(args.env, encoding="utf-8") as f:
        env = parse_env(f.read())
    with open(args.playit, encoding="utf-8") as f:
        playit = f.read()
    try:
        print(make(env, playit, {"type": "b2", "bucket": args.bucket, "account": args.key_id, "key": args.key}))
    except InviteError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
