"""Putting a backup zip back as the shared world (the recovery when a session was never uploaded). A plain folder stands in
for the cloud storage."""
import json
import os
import socket
import tempfile
import time
import unittest
import zipfile

from app.desktop import invite as invites
from app.desktop import restore
from app.desktop.home import Home
from app.rclone import Rclone
from app.shared import RESTORE_MARKER, RcloneWorld, resumed_own_lock

ENV = {"RCON_PASSWORD": "rcon-pw", "SERVER_NAME": "Test SMP", "MC_VERSION": "26.3", "MC_MEMORY": "6G"}


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cloud = os.path.join(self.tmp.name, "cloud")
        self.home = Home(os.path.join(self.tmp.name, "pc"))
        invite = invites.read(invites.make(ENV, "", {"type": "remote", "remote": self.cloud}))
        self.home.apply_invite(invite, "Ethan", total_gb=16)
        self.home.update_settings(PANEL_PORT=free_port())
        self.rclone = Rclone("rclone")
        self.said = []
        # the shared world as B left it: a real upload (more players than the backup has), the lock handed back
        b_server = os.path.join(self.tmp.name, "b", "server")
        for i in range(3):
            write(os.path.join(b_server, "world", "playerdata", f"p{i}.dat"), f"b-{i}")
        write(os.path.join(b_server, "world", "level.dat"), "b-level")
        b = RcloneWorld(self.cloud, b_server, "B", self.rclone)
        b.acquire(claim_wait=0)
        b.push()
        b.release()
        # this PC's current world (what a pull gave it), and the backup A's progress is in
        write(os.path.join(self.home.server_dir, "world", "level.dat"), "current")
        self.zip = os.path.join(self.tmp.name, "world-2026-10-18_21-40-12.zip")
        with zipfile.ZipFile(self.zip, "w") as z:
            z.writestr("level.dat", "a-level")
            z.writestr("playerdata/p0.dat", "a-0")
            z.writestr("region/r.0.0.mca", "a-region")

    def test_the_backup_becomes_this_pcs_world_and_the_lock_is_taken_without_downloading(self):
        moved = restore.restore(self.home, self.rclone, self.zip, report=self.said.append)
        world = os.path.join(self.home.server_dir, "world")
        self.assertEqual(read(os.path.join(world, "level.dat")), "a-level")
        self.assertEqual(read(os.path.join(moved, "level.dat")), "current")            # nothing deleted
        state = json.loads(read(os.path.join(self.cloud, "state.json")))
        self.assertEqual((state["status"], state["host"]), ("hosting", "Ethan"))
        self.assertEqual(read(os.path.join(self.cloud, "data", "world", "level.dat")), "b-level")   # cloud untouched
        self.assertTrue(os.path.exists(os.path.join(self.home.server_dir, RESTORE_MARKER)))

    def test_starting_again_resumes_the_lock_and_the_upload_replaces_the_shared_world_despite_fewer_players(self):
        restore.restore(self.home, self.rclone, self.zip, report=self.said.append)
        settings = self.home.settings()
        os.environ["RCLONE_CONFIG"] = self.home.rclone_conf
        world = RcloneWorld(settings["SHARED_REMOTE"], self.home.server_dir, "Ethan", self.rclone)
        previous = world.acquire(claim_wait=0)                   # what the controller does at Start hosting
        self.assertTrue(resumed_own_lock(previous))
        self.assertTrue(world.can_keep_local_world())             # so it does not download over the restored world
        world.push()                                              # Stop hosting: 1 player file < the shared 3, allowed once
        self.assertEqual(read(os.path.join(self.cloud, "data", "world", "level.dat")), "a-level")
        self.assertEqual(read(os.path.join(self.cloud, "data", "world", "region", "r.0.0.mca")), "a-region")
        self.assertFalse(os.path.exists(os.path.join(self.home.server_dir, RESTORE_MARKER)))      # one time only
        history = os.listdir(os.path.join(self.cloud, "history"))
        self.assertTrue(history)                                  # the version it replaced is kept for a few days

    def test_a_live_host_blocks_the_restore(self):
        state = json.loads(read(os.path.join(self.cloud, "state.json")))
        write(os.path.join(self.cloud, "state.json"), json.dumps(dict(state, status="hosting", host="B", lastSeen=9e9)))
        with self.assertRaises(restore.RestoreError):
            restore.restore(self.home, self.rclone, self.zip, report=self.said.append)
        self.assertEqual(read(os.path.join(self.home.server_dir, "world", "level.dat")), "current")

    def test_a_zip_of_the_world_folder_is_accepted_and_session_lock_is_left_out(self):
        drive = os.path.join(self.tmp.name, "world-20261020T164727Z-1-001.zip")      # what a Google Drive download looks like
        with zipfile.ZipFile(drive, "w") as z:
            z.writestr("world/level.dat", "drive-level")
            z.writestr("world/playerdata/p0.dat", "drive-0")
            z.writestr("world/dimensions/minecraft/the_nether/region/r.0.0.mca", "nether")
            z.writestr("world/session.lock", "x")
        restore.restore(self.home, self.rclone, drive, report=self.said.append)
        world = os.path.join(self.home.server_dir, "world")
        self.assertEqual(read(os.path.join(world, "level.dat")), "drive-level")
        self.assertEqual(read(os.path.join(world, "dimensions", "minecraft", "the_nether", "region", "r.0.0.mca")), "nether")
        self.assertFalse(os.path.exists(os.path.join(world, "session.lock")))
        self.assertFalse(os.path.exists(os.path.join(world, "world")))                 # not a world inside the world

    def test_a_zip_trying_to_write_outside_the_world_is_refused(self):
        evil = os.path.join(self.tmp.name, "evil.zip")
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("level.dat", "x")
            z.writestr("../escaped.txt", "x")
        with self.assertRaises(restore.RestoreError):
            restore.restore(self.home, self.rclone, evil, report=self.said.append)
        self.assertEqual(read(os.path.join(self.home.server_dir, "world", "level.dat")), "current")     # put back
        self.assertFalse(os.path.exists(os.path.join(self.home.server_dir, "escaped.txt")))

    def test_backups_are_listed_newest_first_with_the_time_from_their_names(self):
        folder = os.path.join(self.tmp.name, "backups")
        for name in ("world-2026-10-20_18-50-12.zip", "world-2026-10-20_23-30-00.zip", "world-2026-10-19_08-00-00.zip",
                     "notes.txt"):
            write(os.path.join(folder, name), "x")
        listed = restore.list_backups(folder)
        self.assertEqual([b["name"] for b in listed], ["world-2026-10-20_23-30-00.zip", "world-2026-10-20_18-50-12.zip",
                                                        "world-2026-10-19_08-00-00.zip"])
        self.assertEqual(time.strftime("%H:%M", time.localtime(listed[1]["time"])), "18:50")
        self.assertEqual(restore.list_backups(os.path.join(self.tmp.name, "nothing")), [])

    def test_a_zip_that_is_not_a_world_is_refused_and_nothing_changes(self):
        bad = os.path.join(self.tmp.name, "other.zip")
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("readme.txt", "hi")
        with self.assertRaises(restore.RestoreError):
            restore.restore(self.home, self.rclone, bad, report=self.said.append)
        self.assertEqual(read(os.path.join(self.home.server_dir, "world", "level.dat")), "current")


if __name__ == "__main__":
    unittest.main()
