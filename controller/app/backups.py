"""World backups for the process backend. Runs right after the server has stopped, so the files are consistent."""
import logging
import os
import shutil
import time

log = logging.getLogger("backups")


def make_backup(server_dir: str, backup_dir: str, keep: int = 14):
    world = os.path.join(server_dir, "world")
    if not backup_dir or not os.path.isdir(world):
        return None
    os.makedirs(backup_dir, exist_ok=True)
    stem = os.path.join(backup_dir, "world-" + time.strftime("%Y-%m-%d_%H-%M-%S"))
    archive = shutil.make_archive(stem, "zip", root_dir=world)
    old = sorted((f for f in os.listdir(backup_dir) if f.startswith("world-") and f.endswith(".zip")), reverse=True)
    for name in old[keep:]:
        os.remove(os.path.join(backup_dir, name))
    log.info("Backup written: %s", archive)
    return archive
