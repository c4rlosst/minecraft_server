"""The progress bar's numbers: the file the controller writes, how stages share the bar, and rclone's own stats."""
import json
import os
import tempfile
import time
import unittest

from app import progress
from app.progress import Progress
from app.rclone import Rclone
from app.shared import RcloneWorld


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ProgressFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state", "progress.json")
        self.clock = Clock()
        self.p = Progress(self.path, clock=self.clock, min_interval=0)

    def test_stages_share_the_bar_by_their_size(self):
        self.p.begin("download", "Downloading the world", {"world": 900, "mods": 100})
        self.assertIsNone(self.p.fraction())                          # nothing measured yet: the bar pulses
        self.p.stage("world", "Downloading the world")
        self.p.transfer({"bytes": 450, "totalBytes": 900, "transfers": 3})
        self.assertAlmostEqual(self.p.fraction(), 0.45)               # half of the stage that is 90% of the job
        self.p.stage("mods", "Downloading the mods")                  # the world stage is done
        self.assertAlmostEqual(self.p.fraction(), 0.9)
        self.p.transfer({"bytes": 50, "totalBytes": 100, "transfers": 1})
        self.assertAlmostEqual(self.p.fraction(), 0.95)
        shown = progress.read(self.path, now=self.clock())
        self.assertEqual((shown["phase"], shown["label"], shown["bytes"], shown["files"]), ("download", "Downloading the mods", 500, 4))
        self.p.end_stage()
        self.assertAlmostEqual(self.p.fraction(), 1.0)

    def test_a_stage_with_nothing_to_copy_still_moves_the_bar_on(self):
        self.p.begin("upload", "Uploading the world", {"world": 800, "mods": 200})
        self.p.stage("world")
        self.p.transfer({"bytes": 0, "totalBytes": 0, "transfers": 0})   # unchanged: rclone has nothing to send
        self.p.stage("mods")
        self.assertAlmostEqual(self.p.fraction(), 0.8)

    def test_notes_and_finishing(self):
        self.p.note("Taking the hosting lock...")
        shown = progress.read(self.path, now=self.clock())
        self.assertEqual((shown["label"], shown["fraction"]), ("Taking the hosting lock...", None))
        self.p.finish()
        self.assertIsNone(progress.read(self.path, now=self.clock()))
        self.assertFalse(os.path.exists(self.path))

    def test_a_new_step_forgets_the_numbers_of_the_transfer_before_it(self):
        self.p.begin("upload", "Uploading the world", {"world": 100})
        self.p.stage("world")
        self.p.transfer({"bytes": 50, "totalBytes": 100, "transfers": 2})
        self.p.note("Checking that everything arrived...", 1.0)
        shown = progress.read(self.path, now=self.clock())
        self.assertEqual((shown["fraction"], shown["bytes"], shown["files"]), (1.0, 0, 0))
        self.p.note("Handing over...")
        self.assertIsNone(progress.read(self.path, now=self.clock())["fraction"])

    def test_a_job_that_stopped_updating_is_not_shown(self):
        self.p.note("Uploading...")
        self.clock.now += progress.STALE_AFTER + 1
        self.assertIsNone(progress.read(self.path, now=self.clock()))

    def test_writes_are_throttled_but_stage_changes_always_land(self):
        p = Progress(self.path, clock=self.clock, min_interval=1.0)
        p.begin("download", "x", {"world": 10})
        p.stage("world")
        p.transfer({"bytes": 1, "totalBytes": 10})
        p.transfer({"bytes": 9, "totalBytes": 10})                    # within a second of the last write: skipped
        self.assertAlmostEqual(progress.read(self.path, now=self.clock())["fraction"], 0.0)
        self.clock.now += 1.5
        p.transfer({"bytes": 9, "totalBytes": 10})
        self.assertAlmostEqual(progress.read(self.path, now=self.clock())["fraction"], 0.9)

    def test_size_text(self):
        self.assertEqual(progress.size_text(500), "500 B")
        self.assertEqual(progress.size_text(2048), "2 KB")
        self.assertEqual(progress.size_text(5 * 1048576), "5.0 MB")
        self.assertEqual(progress.size_text(3 * 1073741824), "3.0 GB")


class RealTransferTests(unittest.TestCase):
    """A real rclone moves real files and reports how far it got (folders stand in for the cloud)."""

    def test_an_upload_and_a_download_report_a_bar_that_ends_full(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        server, cloud = os.path.join(tmp.name, "server"), os.path.join(tmp.name, "cloud")
        for rel, size in (("world/region/r.0.0.mca", 3 * 1024 * 1024), ("world/level.dat", 1000), ("mods/a.jar", 200000),
                          ("server.properties", 50)):
            path = os.path.join(server, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(os.urandom(size))
        pfile = os.path.join(tmp.name, "progress.json")
        seen = []
        world = RcloneWorld(cloud, server, "A", Rclone("rclone"), clock=time.time, sleep=lambda s: None)
        world.progress = Progress(pfile, min_interval=0)

        def watched(p):
            original = p._write

            def watch(force=False):
                original(force=True)
                data = progress.read(pfile)
                if data:
                    seen.append((data["phase"], data["label"], data["fraction"]))
            p._write = watch
            return p
        world.progress = watched(world.progress)
        world.acquire(claim_wait=0)
        world.push()
        uploads = [s for s in seen if s[0] == "upload"]
        self.assertTrue(uploads)
        self.assertIn("Uploading the world", {s[1] for s in uploads})
        fractions = [s[2] for s in uploads if s[2] is not None]
        self.assertEqual(fractions, sorted(fractions))                    # the bar never goes backwards
        self.assertAlmostEqual(fractions[-1], 1.0, places=2)
        self.assertFalse(os.path.exists(pfile))                           # and disappears when the job is done
        seen.clear()
        other = os.path.join(tmp.name, "other")
        world.release()
        friend = RcloneWorld(cloud, other, "B", Rclone("rclone"), clock=time.time, sleep=lambda s: None)
        friend.progress = watched(Progress(pfile, min_interval=0))
        friend.acquire(claim_wait=0)
        friend.pull()
        downloads = [s for s in seen if s[0] == "download"]
        self.assertTrue(downloads)
        self.assertEqual([s[2] for s in downloads if s[2] is not None], sorted(s[2] for s in downloads if s[2] is not None))
        self.assertTrue(os.path.exists(os.path.join(other, "world", "region", "r.0.0.mca")))


if __name__ == "__main__":
    unittest.main()
