"""Put an older world back as THE world, from a backup zip (the app's automatic backups are in the SMP Host folder, in
`backups`, named world-YYYY-MM-DD_HH-MM-SS.zip).

  python -m app.desktop.restore --zip "C:\\Users\\you\\Desktop\\backups\\world-2026-10-18_21-40-12.zip"

On the PC you run this on (close SMP Host and stop hosting first) it:
  1. moves this PC's current world aside (folder `world-before-restore-<time>`; nothing is deleted),
  2. unpacks the backup as the world,
  3. takes the hosting lock for this PC, WITHOUT downloading anything.
Then open SMP Host and press Start hosting: it sees its own lock and keeps this PC's world instead of replacing it, and
when you press Stop hosting the restored world is uploaded as the new shared world. Whatever the shared world held
before is kept in the upload history for a few days."""
import argparse
import os
import shutil
import sys
import time
import zipfile

from ..rclone import Rclone, RcloneError
from ..shared import RESTORE_MARKER, STALE_SECONDS, HostingError, RcloneWorld
from .home import Home, find_tools
from .runner import Panel


class RestoreError(Exception):
    """Shown to the person restoring: what is wrong (nothing has been changed when this is raised early)."""


def list_backups(backup_dir: str) -> list:
    """The automatic backups in the folder, newest first: [{'path', 'name', 'time', 'size'}]. The time comes from the file
    name (world-2026-10-20_18-50-12.zip, this PC's clock), else from the file's date."""
    found = []
    try:
        names = os.listdir(backup_dir)
    except OSError:
        return found
    for name in names:
        if not (name.startswith("world-") and name.endswith(".zip")):
            continue
        path = os.path.join(backup_dir, name)
        try:
            stamp = time.mktime(time.strptime(name[len("world-"):-len(".zip")], "%Y-%m-%d_%H-%M-%S"))
        except (ValueError, OverflowError):
            stamp = os.path.getmtime(path)
        try:
            found.append({"path": path, "name": name, "time": stamp, "size": os.path.getsize(path)})
        except OSError:
            continue
    return sorted(found, key=lambda b: b["time"], reverse=True)


def check_zip(path: str) -> None:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "level.dat" not in names:
                raise RestoreError("That zip isn't a world backup (it has no level.dat at the top). Use one of the "
                                   "world-....zip files from the SMP Host backups folder.")
            bad = z.testzip()
            if bad:
                raise RestoreError(f"The zip is damaged ({bad}). Try another backup.")
    except zipfile.BadZipFile:
        raise RestoreError("That file isn't a readable zip.") from None
    except OSError as exc:
        raise RestoreError(f"Can't open the zip: {exc}") from None


def restore(home: Home, rclone: Rclone, zip_path: str, report=print, force=False, clock=time.time) -> str:
    """Returns the folder the previous world was moved to ('' if there was none)."""
    check_zip(zip_path)
    settings = home.settings()
    if not settings.get("SHARED_REMOTE"):
        raise RestoreError("This PC isn't set up for shared hosting yet.")
    if Panel(home.panel_port(), "").listening():
        raise RestoreError("SMP Host is hosting on this PC right now. Press Stop hosting and close it first.")
    os.environ["RCLONE_CONFIG"] = home.rclone_conf
    world = RcloneWorld(settings["SHARED_REMOTE"], home.server_dir, settings.get("HOST_NAME", ""), rclone)
    try:
        state = world.read_state() or {}
    except HostingError as exc:
        raise RestoreError(str(exc)) from None
    mine = state.get("status") == "hosting" and state.get("host") == world.host
    if state.get("status") == "hosting" and not mine:
        age = clock() - float(state.get("lastSeen", 0))
        if age < STALE_SECONDS:
            raise RestoreError(f"{state.get('host')} is hosting right now. Ask them to press Stop hosting first.")
        if not force:
            raise RestoreError(f"{state.get('host')} still holds the lock (not seen for {int(age // 60)} min). If their PC "
                               "is gone for good, run again with --force.")

    target = os.path.join(home.server_dir, "world")
    moved = ""
    if os.path.isdir(target):
        moved = os.path.join(home.server_dir, "world-before-restore-" + time.strftime("%Y%m%d-%H%M%S"))
        os.rename(target, moved)
        report(f"This PC's current world was moved to: {moved}")
    try:
        os.makedirs(target)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(target)
        pull_marker = os.path.join(home.server_dir, ".pull-incomplete")
        if os.path.exists(pull_marker):                     # a restored world is complete: don't let it be re-downloaded
            os.remove(pull_marker)
        with open(os.path.join(home.server_dir, RESTORE_MARKER), "w") as f:
            f.write("This world was put back from a backup on purpose: the next upload may replace a bigger shared world.")
        world.acquire(takeover=force, claim_wait=0)         # the lock now says: this PC hosts (and keeps its own world)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)           # put things back as they were
        try:
            os.remove(os.path.join(home.server_dir, RESTORE_MARKER))
        except OSError:
            pass
        if moved:
            os.rename(moved, target)
        raise
    report("The backup is now this PC's world and this PC holds the hosting lock.")
    return moved


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Restore a world backup as the shared world.")
    parser.add_argument("--zip", required=True, help="a world-....zip from the SMP Host backups folder")
    parser.add_argument("--force", action="store_true", help="take over a lock that a PC left behind")
    args = parser.parse_args(argv)
    home, tools = Home(), find_tools()
    try:
        restore(home, Rclone(tools.rclone or "rclone"), args.zip, force=args.force)
    except (RestoreError, HostingError, RcloneError) as exc:
        print(f"\nStopped: {exc}", file=sys.stderr)
        return 1
    print("\nNext: open SMP Host, press Start hosting (it keeps this world), then Stop hosting and wait for 'Done'.\n"
          "That uploads the restored world for everyone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
