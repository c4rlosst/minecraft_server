"""A PC that starts from an old copy of the world must not overwrite a newer upload (the 'my progress is gone' bug):
every PC remembers which version its world is based on, and an upload from an older base is refused."""
import json
import os
import tempfile
import time
import unittest

from app.rclone import Rclone
from app.shared import HostingError, RcloneWorld, VERSION_MARKER


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class LocalVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cloud = os.path.join(self.tmp.name, "cloud")
        self.clock = [1_800_000_000.0]

    def pc(self, name):
        server = os.path.join(self.tmp.name, name, "server")
        write(os.path.join(server, "world", "level.dat"), f"{name}-level")
        write(os.path.join(server, "world", "playerdata", "p0.dat"), "p")
        world = RcloneWorld(self.cloud, server, name, Rclone("rclone"), clock=lambda: self.clock[0], sleep=lambda s: None)
        return world

    def host_once(self, world, text=None, pull=True):
        """What a start/stop does: take the lock, download (or keep), change something, upload, give the lock back."""
        world.acquire(claim_wait=0)
        if pull and world.shared_version() > 0:
            world.pull()
        if text:
            write(os.path.join(world.server_dir, "world", "level.dat"), text)
        self.clock[0] += 3600
        world.push()
        world.release()

    def test_every_pull_and_push_records_the_version_it_is_based_on(self):
        a = self.pc("A")
        self.assertIsNone(a.local_version())
        self.host_once(a, "a1")
        self.assertEqual(a.local_version(), 1)
        b = self.pc("B")
        b.acquire(claim_wait=0)
        b.pull()
        self.assertEqual(b.local_version(), 1)
        self.assertFalse(b.local_is_behind())

    def test_an_old_copy_cannot_overwrite_a_newer_upload(self):
        a, b = self.pc("A"), self.pc("B")
        self.host_once(a, "a1")                                   # version 1
        a.acquire(claim_wait=0)
        a.pull()
        write(os.path.join(a.server_dir, "world", "level.dat"), "a-unuploaded")      # A plays, then its PC crashes
        # B hosts meanwhile (takes over the silent lock) and uploads version 2
        self.clock[0] += 3600
        b.acquire(takeover=True, claim_wait=0)
        b.pull()
        write(os.path.join(b.server_dir, "world", "level.dat"), "b-newer")
        b.push()
        b.release()
        self.assertEqual(json.loads(read(os.path.join(self.cloud, "state.json")))["version"], 2)
        # A starts again with the lock it thinks it holds, from its OLD copy
        a2 = RcloneWorld(self.cloud, a.server_dir, "A", Rclone("rclone"), clock=lambda: self.clock[0], sleep=lambda s: None)
        previous = a2.acquire(claim_wait=0)
        self.assertTrue(a2.local_is_behind())                     # the controller notices and downloads instead of keeping it
        with self.assertRaises(HostingError) as refused:
            a2.push()                                             # and even if it tried to upload, it is refused
        self.assertIn("version 1", str(refused.exception))
        self.assertIn("version 2", str(refused.exception))
        self.assertEqual(read(os.path.join(self.cloud, "data", "world", "level.dat")), "b-newer")   # nothing overwritten
        self.assertEqual(read(os.path.join(a.server_dir, "world", "level.dat")), "a-unuploaded")    # A's copy left alone
        self.assertFalse(previous.get("resumedOwnLock"))

    def test_a_pc_that_is_not_behind_uploads_normally_and_an_unknown_base_is_allowed(self):
        a = self.pc("A")
        self.host_once(a, "a1")
        self.host_once(a, "a2")
        self.assertEqual(read(os.path.join(self.cloud, "data", "world", "level.dat")), "a2")
        os.remove(os.path.join(a.server_dir, VERSION_MARKER))      # an install from before this check: no record yet
        self.host_once(a, "a3", pull=False)
        self.assertEqual(read(os.path.join(self.cloud, "data", "world", "level.dat")), "a3")
        self.assertEqual(a.local_version(), 3)

    def test_a_deliberate_restore_may_replace_the_newest_upload(self):
        a, b = self.pc("A"), self.pc("B")
        self.host_once(a, "a1")
        self.host_once(b, "b2")                                   # version 2
        a.progress  # (restore.py does the next two steps for the PC being restored)
        a.acquire(claim_wait=0)
        a.mark_local_version(a.shared_version())
        write(os.path.join(a.server_dir, "world", "level.dat"), "restored")
        a.push()
        self.assertEqual(read(os.path.join(self.cloud, "data", "world", "level.dat")), "restored")


if __name__ == "__main__":
    unittest.main()


class VerifiedDownloadTests(unittest.TestCase):
    """A downloaded world is compared with the storage before it is hosted."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cloud = os.path.join(self.tmp.name, "cloud")
        a_dir = os.path.join(self.tmp.name, "a", "server")
        write(os.path.join(a_dir, "world", "level.dat"), "the-level")
        write(os.path.join(a_dir, "world", "region", "r.0.0.mca"), "R" * 5000)
        write(os.path.join(a_dir, "mods", "m.jar"), "mod")
        a = RcloneWorld(self.cloud, a_dir, "A", Rclone("rclone"), clock=time.time, sleep=lambda s: None)
        a.acquire(claim_wait=0)
        a.push()
        a.release()
        self.b_dir = os.path.join(self.tmp.name, "b", "server")
        self.b = RcloneWorld(self.cloud, self.b_dir, "B", Rclone("rclone"), clock=time.time, sleep=lambda s: None)

    def test_a_normal_download_passes_the_check(self):
        self.b.acquire(claim_wait=0)
        self.b.pull()                                                  # (the check runs inside pull)
        self.assertEqual(read(os.path.join(self.b_dir, "world", "region", "r.0.0.mca")), "R" * 5000)

    def test_a_file_that_was_cut_off_is_fetched_again(self):
        self.b.acquire(claim_wait=0)
        self.b.pull()
        path = os.path.join(self.b_dir, "world", "region", "r.0.0.mca")
        with open(path, "w") as f:
            f.write("R" * 100)                                         # a half-written file
        self.b._verify_download({"world", "mods"})
        self.assertEqual(read(path), "R" * 5000)

    def test_a_download_that_cannot_be_repaired_is_refused_instead_of_hosted(self):
        self.b.acquire(claim_wait=0)
        self.b.pull()
        os.remove(os.path.join(self.b_dir, "mods", "m.jar"))
        self.b.rclone.sync = lambda *a, **k: None                       # a repair that does nothing
        with self.assertRaises(HostingError) as refused:
            self.b._verify_download({"world", "mods"})
        self.assertIn("mods", str(refused.exception))
        self.assertIn("completely", str(refused.exception))
