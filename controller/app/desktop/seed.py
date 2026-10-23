"""Start a new Cloudflare R2 bucket from a world that is already on this PC, without reading anything from the old storage.

Used when the old storage can't be downloaded from (Backblaze's free daily download limit) or when the world to start from
is a rebuilt/restored copy rather than what the old storage holds.

  python -m app.desktop.seed --account-id <Cloudflare account ID> --bucket <new empty bucket> --access-key <key ID>
  (it asks for the secret access key without showing it)

What it does:
  1. lays the world out like a shared bucket in a local folder (data/ + state.json + the server's name and picture),
  2. uploads that to the new R2 bucket and checks every file arrived,
  3. points THIS PC at R2 (the old settings are kept in rclone.conf.before-move),
  4. marks the old storage as moved, if it can (an upload, which the download limit doesn't stop), so a PC that still has the
     old invite can't carry on hosting there by accident.
Nothing is deleted from the old storage."""
import argparse
import getpass
import json
import os
import shutil
import sys
import time

from ..rclone import Rclone, RcloneError, join
from ..shared import manifest
from . import migrate
from . import storage as storage_conf
from .home import REMOTE, Home, find_tools

RECOVERY = os.path.join(os.path.dirname(__file__), "..", "..", "..", "desktop", "recovery")


def prepare_bucket(bucket_dir: str, data_source: str, face_dir: str, host: str, version: int, now: float) -> int:
    """Make bucket_dir look like a shared bucket holding the world in data_source (a data/ mirror). Returns the file count."""
    data = os.path.join(bucket_dir, "data")
    os.makedirs(bucket_dir, exist_ok=True)
    if os.path.isdir(data_source) and os.path.abspath(data_source) != os.path.abspath(data):
        if os.path.exists(data):
            raise migrate.MigrateError(f"{data} already exists; remove it or choose another folder.")
        shutil.move(data_source, data)                      # same drive: instant
    # (when data_source is gone but bucket_dir/data is there, an earlier run already laid the world out: use it)
    if not os.path.isfile(os.path.join(data, "world", "level.dat")):
        raise migrate.MigrateError("The world to start from has no world/level.dat. Nothing was changed.")
    count, nbytes = manifest(data)
    state = {"status": "idle", "host": None, "token": None, "since": None, "lastHost": host, "lastSeen": now,
             "lastPush": now, "version": version, "count": count, "bytes": nbytes}
    with open(os.path.join(bucket_dir, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f)
    for name in ("identity.json", "server-icon.png"):         # the server's name, description and picture
        src = os.path.join(face_dir, name)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(bucket_dir, name))
    return count


def seed(rclone: Rclone, bucket_dir: str, new_path: str, report=print) -> int:
    """Upload the prepared bucket folder into the (empty) new storage and verify it."""
    return migrate.move(rclone, bucket_dir, new_path, report)


def mark_old_moved(rclone: Rclone, old_conf_text: str, old_bucket_path: str, work_dir: str, report=print) -> None:
    """Write a 'moved' lock into the old bucket (an upload: it works even when downloads are refused)."""
    conf = os.path.join(work_dir, "old.conf")
    with open(conf, "w", encoding="utf-8") as f:
        f.write(old_conf_text.replace(f"[{REMOTE}]", "[old]", 1))
    previous = os.environ.get("RCLONE_CONFIG")
    os.environ["RCLONE_CONFIG"] = conf
    try:
        marker = {"status": "hosting", "host": "MOVED to Cloudflare R2 - update SMP Host and use the new invite",
                  "token": "moved", "lastSeen": time.time() + migrate.FAR_FUTURE, "version": 0}
        rclone.put(join("old:" + old_bucket_path.split(":", 1)[1], "state.json"), json.dumps(marker).encode("utf-8"))
        report("Marked the old storage as moved.")
    except RcloneError as exc:
        report(f"Couldn't mark the old storage as moved ({exc}). Make sure nobody hosts from it.")
    finally:
        if previous is None:
            os.environ.pop("RCLONE_CONFIG", None)
        else:
            os.environ["RCLONE_CONFIG"] = previous
        try:
            os.remove(conf)
        except OSError:
            pass


def run(home: Home, storage: dict, rclone: Rclone, data_source: str, bucket_dir: str, version: int, report=print) -> int:
    if home.storage_kind()["type"] == "r2":
        raise migrate.MigrateError("This PC already uses Cloudflare R2.")
    old_remote = home.settings().get("SHARED_REMOTE", "")
    old_conf = ""
    if os.path.isfile(home.rclone_conf):
        with open(home.rclone_conf, encoding="utf-8") as f:
            old_conf = f.read()
    report("Laying out the world as a bucket...")
    prepare_bucket(bucket_dir, data_source, home.server_dir, home.settings().get("HOST_NAME", ""), version, time.time())
    work = os.path.join(home.root, "rclone")
    both = os.path.join(work, "seed.conf")
    home._write(both, storage_conf.rclone_section("new", storage))
    previous = os.environ.get("RCLONE_CONFIG")
    os.environ["RCLONE_CONFIG"] = both
    try:
        count = seed(rclone, bucket_dir, "new:" + storage["bucket"], report)
    finally:
        if previous is None:
            os.environ.pop("RCLONE_CONFIG", None)
        else:
            os.environ["RCLONE_CONFIG"] = previous
        try:
            os.remove(both)                                   # it holds the secret key: don't leave it lying around
        except OSError:
            pass
    migrate.switch_this_pc(home, storage)
    if old_conf and old_remote.startswith(REMOTE + ":"):
        mark_old_moved(rclone, old_conf, old_remote, work, report)
    return count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Start a Cloudflare R2 bucket from a world on this PC.")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--bucket", required=True, help="the new, empty R2 bucket")
    parser.add_argument("--access-key", required=True)
    parser.add_argument("--secret-key", help="asked for (hidden) when left out")
    parser.add_argument("--data", default=os.path.join(RECOVERY, "v21_staging"), help="the data/ mirror to start from")
    parser.add_argument("--bucket-dir", default=os.path.join(RECOVERY, "v21_bucket"))
    parser.add_argument("--version", type=int, default=25, help="the first upload number in the new bucket")
    args = parser.parse_args(argv)
    secret = args.secret_key or getpass.getpass("Secret access key (hidden): ").strip()
    storage = {"type": "r2", "bucket": args.bucket.strip(), "account_id": args.account_id.strip(),
               "access_key": args.access_key.strip(), "secret_key": secret}
    if not all(storage.values()):
        print("The account ID, bucket, access key ID and secret access key are all needed.", file=sys.stderr)
        return 1
    home, tools = Home(), find_tools()
    try:
        count = run(home, storage, Rclone(tools.rclone or "rclone"), os.path.abspath(args.data),
                    os.path.abspath(args.bucket_dir), args.version)
    except (migrate.MigrateError, RcloneError) as exc:
        print(f"\nStopped: {exc}", file=sys.stderr)
        return 1
    print(f"\nDone: {count} files are in Cloudflare R2 and this PC now uses it.\n"
          "Next: open SMP Host, press Start hosting once (it downloads the world from R2), then Stop hosting.\n"
          "Then make an invite for your friends with Invite friend.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
