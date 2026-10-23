"""Starting a new storage from a world already on this PC (no reading from the old storage). Folders stand in for R2."""
import json
import os
import tempfile
import unittest

from app.desktop import migrate, seed
from app.rclone import Rclone
from app.shared import RcloneWorld, manifest


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = os.path.join(self.tmp.name, "staging")                  # a data/ mirror, as the rebuilt world is
        write(os.path.join(self.data, "world", "level.dat"), "restored-world")
        write(os.path.join(self.data, "world", "playerdata", "p0.dat"), "p0")
        write(os.path.join(self.data, "mods", "waystones.jar"), "mod")
        write(os.path.join(self.data, "server.properties"), "motd=x")
        self.face = os.path.join(self.tmp.name, "pc", "server")
        write(os.path.join(self.face, "identity.json"), json.dumps({"name": "JAPAX"}))
        write(os.path.join(self.face, "server-icon.png"), "png")
        self.bucket = os.path.join(self.tmp.name, "bucket")
        self.r2 = os.path.join(self.tmp.name, "r2")
        os.makedirs(self.r2)
        self.said = []

    def test_the_prepared_bucket_has_a_lock_the_next_host_accepts(self):
        count = seed.prepare_bucket(self.bucket, self.data, self.face, "sai", 25, 1_800_000_000.0)
        self.assertEqual(count, 4)
        state = json.loads(read(os.path.join(self.bucket, "state.json")))
        self.assertEqual((state["status"], state["version"], state["lastHost"]), ("idle", 25, "sai"))
        self.assertEqual((state["count"], state["bytes"]), manifest(os.path.join(self.bucket, "data")))
        self.assertTrue(os.path.isfile(os.path.join(self.bucket, "identity.json")))
        self.assertFalse(os.path.exists(self.data))                          # moved, not copied

    def test_running_again_after_a_failed_upload_reuses_the_world_already_laid_out(self):
        seed.prepare_bucket(self.bucket, self.data, self.face, "sai", 25, 1.0)      # the first run: moves it, then the upload fails
        self.assertFalse(os.path.exists(self.data))
        count = seed.prepare_bucket(self.bucket, self.data, self.face, "sai", 25, 2.0)   # the second run: the source is gone
        self.assertEqual(count, 4)
        self.assertEqual(json.loads(read(os.path.join(self.bucket, "state.json")))["lastSeen"], 2.0)

    def test_a_world_without_level_dat_is_refused(self):
        os.remove(os.path.join(self.data, "world", "level.dat"))
        with self.assertRaises(migrate.MigrateError):
            seed.prepare_bucket(self.bucket, self.data, self.face, "sai", 25, 1.0)

    def test_after_seeding_a_friend_can_take_the_lock_and_download_the_world(self):
        seed.prepare_bucket(self.bucket, self.data, self.face, "sai", 25, 1_800_000_000.0)
        rclone = Rclone("rclone")
        seed.seed(rclone, self.bucket, self.r2, self.said.append)
        self.assertEqual(read(os.path.join(self.r2, "data", "world", "level.dat")), "restored-world")
        friend_server = os.path.join(self.tmp.name, "friend", "server")
        friend = RcloneWorld(self.r2, friend_server, "Ethan", rclone, clock=lambda: 1_800_000_100.0, sleep=lambda s: None)
        previous = friend.acquire(claim_wait=0)                              # the manifest check passes: counts match
        self.assertEqual(previous["version"], 25)
        friend.pull()
        self.assertEqual(read(os.path.join(friend_server, "world", "level.dat")), "restored-world")
        self.assertTrue(os.path.isfile(os.path.join(friend_server, "mods", "waystones.jar")))
        self.assertEqual(friend.local_version(), 25)

    def test_a_bucket_that_is_not_empty_is_refused(self):
        seed.prepare_bucket(self.bucket, self.data, self.face, "sai", 25, 1.0)
        write(os.path.join(self.r2, "something.txt"), "x")
        with self.assertRaises(migrate.MigrateError):
            seed.seed(Rclone("rclone"), self.bucket, self.r2, self.said.append)

    def test_marking_the_old_storage_is_an_upload_and_a_failure_only_warns(self):
        class FakeRclone:
            def __init__(self, fail=False): self.puts, self.fail = [], fail
            def put(self, path, data):
                if self.fail: raise seed.RcloneError("offline")
                self.puts.append((path, json.loads(data)))
        said = []
        conf = "[smp]\ntype = b2\naccount = a\nkey = k\n"
        fake = FakeRclone()
        seed.mark_old_moved(fake, conf, "smp:japax-java-2026", self.tmp.name, said.append)
        self.assertEqual(fake.puts[0][0], "old:japax-java-2026/state.json")
        self.assertEqual((fake.puts[0][1]["status"], fake.puts[0][1]["token"]), ("hosting", "moved"))
        seed.mark_old_moved(FakeRclone(fail=True), conf, "smp:japax-java-2026", self.tmp.name, said.append)
        self.assertIn("Couldn't mark", said[-1])


if __name__ == "__main__":
    unittest.main()
