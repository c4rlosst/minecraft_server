"""Move the shared world to another cloud storage (for example from Backblaze B2 to Cloudflare R2), once, from the owner's PC.

  python -m app.desktop.migrate --account-id <Cloudflare account ID> --bucket <new bucket> --access-key <key ID>
  (it asks for the secret access key without showing it)

It copies the shared world (the lock, the world, mods, config, EasyAuth, the server's name and picture; the undo history is
not copied), checks that every file arrived, points THIS PC at the new storage, and marks the old storage as moved so a
PC that still has the old invite can't carry on hosting there by accident. Friends then update the app and use a new
invite (the Invite friend button makes one per person)."""
import argparse
import getpass
import json
import os
import shutil
import sys
import time

from ..rclone import Rclone, RcloneError, join
from ..shared import STALE_SECONDS
from . import storage as storage_conf
from .home import REMOTE, Home, find_tools

SKIP = ("history/**",)               # old copies kept for "undo": not worth moving into a free plan's 10 GB
FAR_FUTURE = 10 * 365 * 24 * 3600


class MigrateError(Exception):
    """Shown to the person moving the world: what stopped it (nothing has been changed when this is raised early)."""


def _read_state(rclone: Rclone, path: str) -> dict:
    data = rclone.cat(join(path, "state.json"))
    try:
        return json.loads(data) if data else {}
    except ValueError:
        raise MigrateError("The old storage's lock file (state.json) is unreadable.") from None


def files_of(rclone: Rclone, path: str) -> dict:
    """{relative path: size} of every file, without the undo history."""
    return {e["Path"]: e["Size"] for e in rclone.lsjson(path, recursive=True, files_only=True, excludes=SKIP)}


def move(rclone: Rclone, old: str, new: str, report=print, force=False, clock=time.time) -> int:
    """Copy the shared world from old to new and mark old as moved. Returns the number of files copied."""
    state = _read_state(rclone, old)
    if state.get("status") == "hosting" and not force:
        age = clock() - float(state.get("lastSeen", 0))
        who = state.get("host", "someone")
        if age < STALE_SECONDS:
            raise MigrateError(f"{who} is hosting right now. Wait until they press Stop hosting, then try again.")
        raise MigrateError(f"{who} still holds the lock (not seen for {int(age // 60)} min). Their last session may not "
                           "be uploaded. Ask them to start hosting and press Stop hosting first, or run again with "
                           "--force to move what is there.")
    try:
        existing = rclone.lsjson(new)
    except RcloneError as exc:
        raise MigrateError(f"Can't open the new storage: {exc}") from None
    if existing and not force:
        raise MigrateError("The new bucket is not empty. Use an empty bucket (or --force if you are sure).")
    wanted = files_of(rclone, old)
    if not wanted:
        raise MigrateError("The old storage has no world in it. Nothing to move.")
    total = sum(wanted.values())
    report(f"Copying {len(wanted)} files ({total / 1e9:.2f} GB). This can take several minutes...")
    if total > 9.5e9:
        report("Warning: that is about the size of Cloudflare R2's free 10 GB.")
    rclone.copy(old, new, excludes=SKIP)
    arrived = files_of(rclone, new)
    bad = [p for p, size in wanted.items() if arrived.get(p) != size]
    if bad:
        raise MigrateError(f"{len(bad)} files didn't arrive intact (for example {bad[0]}). The old storage is untouched; "
                           "run this again.")
    report("Every file arrived. Marking the old storage as moved...")
    try:
        marker = dict(state, status="hosting", host="MOVED to Cloudflare R2 - update SMP Host and use the new invite",
                      token="moved", lastSeen=clock() + FAR_FUTURE)
        rclone.put(join(old, "state.json"), json.dumps(marker).encode("utf-8"))
    except RcloneError as exc:
        report(f"Couldn't mark the old storage as moved ({exc}). Make sure nobody hosts from it.")
    return len(wanted)


def switch_this_pc(home: Home, storage: dict) -> None:
    """Point this PC's app at the new storage (the old settings are kept in rclone.conf.before-move)."""
    if os.path.isfile(home.rclone_conf):
        shutil.copyfile(home.rclone_conf, home.rclone_conf + ".before-move")
    home._write(home.rclone_conf, storage_conf.rclone_section(REMOTE, storage))
    home.update_settings(SHARED_REMOTE=f"{REMOTE}:{storage['bucket']}")


def run(home: Home, storage: dict, rclone: Rclone, report=print, force=False) -> int:
    """The whole move: copy from the storage this PC uses now to `storage`, then switch this PC over."""
    old_bucket = home.settings().get("SHARED_REMOTE", "")
    if not old_bucket.startswith(REMOTE + ":"):
        raise MigrateError("This PC isn't set up for shared hosting yet.")
    if home.storage_kind()["type"] == "r2":
        raise MigrateError("This PC already uses Cloudflare R2.")
    with open(home.rclone_conf, encoding="utf-8") as f:
        old_conf = f.read().replace(f"[{REMOTE}]", "[old]", 1)
    both = os.path.join(home.root, "rclone", "move.conf")
    home._write(both, old_conf + "\n" + storage_conf.rclone_section("new", storage))
    previous = os.environ.get("RCLONE_CONFIG")
    os.environ["RCLONE_CONFIG"] = both
    try:
        count = move(rclone, "old:" + old_bucket.split(":", 1)[1], "new:" + storage["bucket"], report, force)
    finally:
        if previous is None:
            os.environ.pop("RCLONE_CONFIG", None)
        else:
            os.environ["RCLONE_CONFIG"] = previous
        try:
            os.remove(both)                               # it holds both keys: don't leave it lying around
        except OSError:
            pass
    switch_this_pc(home, storage)
    return count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Move the shared world to Cloudflare R2.")
    parser.add_argument("--account-id", required=True, help="Cloudflare account ID (the long code in the R2 page's S3 URL)")
    parser.add_argument("--bucket", required=True, help="the new, empty R2 bucket")
    parser.add_argument("--access-key", required=True, help="the R2 access key ID")
    parser.add_argument("--secret-key", help="the secret access key (asked for, hidden, when left out)")
    parser.add_argument("--force", action="store_true", help="move even if the lock is held or the new bucket has files")
    args = parser.parse_args(argv)
    secret = args.secret_key or getpass.getpass("Secret access key (hidden): ").strip()
    storage = {"type": "r2", "bucket": args.bucket.strip(), "account_id": args.account_id.strip(),
               "access_key": args.access_key.strip(), "secret_key": secret}
    if not all(storage.values()):
        print("The account ID, bucket, access key ID and secret access key are all needed.", file=sys.stderr)
        return 1
    home, tools = Home(), find_tools()
    try:
        count = run(home, storage, Rclone(tools.rclone or "rclone"), force=args.force)
    except (MigrateError, RcloneError) as exc:
        print(f"\nStopped: {exc}", file=sys.stderr)
        return 1
    print(f"\nDone: {count} files moved and this PC now uses Cloudflare R2.\n"
          "Next: open SMP Host, press Start hosting once to check it works, and use Invite friend to make an invite\n"
          "for each friend (with their own R2 key). They install the new SMP Host and click New invite.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
