"""Cloudflare R2 storage: invites, the rclone settings, and moving the shared world from the old storage to the new one.
Plain folders stand in for the two buckets (rclone treats a folder like any other remote)."""
import json
import os
import tempfile
import time
import unittest

from app.desktop import invite as invites
from app.desktop import migrate
from app.desktop import storage
from app.desktop.home import Home
from app.rclone import Rclone

ENV = {"RCON_PASSWORD": "rcon-pw", "SERVER_NAME": "Test SMP", "MC_VERSION": "26.3", "MC_MEMORY": "6G"}
R2 = {"type": "r2", "bucket": "japax-world", "account_id": "abc123def", "access_key": "AKID", "secret_key": "SECRET"}


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class R2InviteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Home(os.path.join(self.tmp.name, "pc"))

    def test_an_r2_invite_sets_up_the_s3_remote_and_the_bucket(self):
        invite = invites.read(invites.make(ENV, "", R2))
        self.home.apply_invite(invite, "Ethan", total_gb=16)
        conf = read(self.home.rclone_conf)
        for line in ("type = s3", "provider = Cloudflare", "access_key_id = AKID", "secret_access_key = SECRET",
                     "endpoint = https://abc123def.r2.cloudflarestorage.com", "no_check_bucket = true"):
            self.assertIn(line, conf)
        self.assertEqual(self.home.settings()["SHARED_REMOTE"], "smp:japax-world")
        self.assertEqual(self.home.storage_kind(), {"type": "r2", "account_id": "abc123def"})

    def test_an_incomplete_r2_invite_is_refused(self):
        with self.assertRaises(invites.InviteError):
            invites.make(ENV, "", dict(R2, secret_key=""))

    def test_a_host_on_r2_invites_friends_with_their_own_r2_key(self):
        self.home.apply_invite(invites.read(invites.make(ENV, "", R2)), "MCboy23", total_gb=16)
        code = self.home.make_invite_for("japax-world", "FRIEND-ID", "FRIEND-SECRET")
        storage_part = invites.read(code)["storage"]
        self.assertEqual(storage_part, {"type": "r2", "bucket": "japax-world", "account_id": "abc123def",
                                        "access_key": "FRIEND-ID", "secret_key": "FRIEND-SECRET"})

    def test_a_host_on_backblaze_still_makes_backblaze_invites(self):
        b2 = {"type": "b2", "bucket": "old", "account": "kid", "key": "k"}
        self.home.apply_invite(invites.read(invites.make(ENV, "", b2)), "MCboy23", total_gb=16)
        self.assertEqual(self.home.storage_kind(), {"type": "b2"})
        storage_part = invites.read(self.home.make_invite_for("old", "kid2", "k2"))["storage"]
        self.assertEqual((storage_part["type"], storage_part["account"]), ("b2", "kid2"))

    def test_describe_ignores_other_storage(self):
        self.assertEqual(storage.describe("[smp]\ntype = drive\n", "smp"), {"type": "other"})
        self.assertEqual(storage.describe("", "smp"), {"type": ""})


class MoveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old, self.new = (os.path.join(self.tmp.name, n) for n in ("old", "new"))
        self.rclone = Rclone("rclone")
        write(os.path.join(self.old, "state.json"), json.dumps({"status": "idle", "host": None, "version": 6,
                                                                "lastHost": "sai", "lastSeen": 0}))
        write(os.path.join(self.old, "data", "world", "level.dat"), "the world")
        write(os.path.join(self.old, "data", "mods", "waystones.jar"), "mod")
        write(os.path.join(self.old, "identity.json"), "{}")
        write(os.path.join(self.old, "history", "2026-10-01", "world", "old.dat"), "undo copy")
        os.makedirs(self.new)
        self.said = []

    def test_everything_but_the_undo_history_arrives_and_the_old_storage_is_marked_moved(self):
        count = migrate.move(self.rclone, self.old, self.new, report=self.said.append)
        self.assertEqual(count, 4)
        self.assertEqual(read(os.path.join(self.new, "data", "world", "level.dat")), "the world")
        self.assertTrue(os.path.isfile(os.path.join(self.new, "data", "mods", "waystones.jar")))
        self.assertFalse(os.path.exists(os.path.join(self.new, "history")))
        self.assertEqual(json.loads(read(os.path.join(self.new, "state.json")))["status"], "idle")   # free to host
        old_state = json.loads(read(os.path.join(self.old, "state.json")))
        self.assertEqual(old_state["status"], "hosting")                       # nobody can carry on with the old invite
        self.assertIn("MOVED", old_state["host"])
        self.assertGreater(old_state["lastSeen"], time.time() + 5 * 365 * 24 * 3600)               # and it never goes stale

    def test_a_live_host_stops_the_move_and_nothing_is_copied(self):
        now = 1_800_000_000.0
        write(os.path.join(self.old, "state.json"), json.dumps({"status": "hosting", "host": "Ethan", "lastSeen": now - 30}))
        with self.assertRaises(migrate.MigrateError) as caught:
            migrate.move(self.rclone, self.old, self.new, clock=lambda: now)
        self.assertIn("Ethan is hosting right now", str(caught.exception))
        self.assertEqual(os.listdir(self.new), [])

    def test_a_lock_left_by_a_crashed_host_needs_force(self):
        now = 1_800_000_000.0
        write(os.path.join(self.old, "state.json"), json.dumps({"status": "hosting", "host": "Ethan", "lastSeen": now - 3600}))
        with self.assertRaises(migrate.MigrateError) as caught:
            migrate.move(self.rclone, self.old, self.new, clock=lambda: now)
        self.assertIn("--force", str(caught.exception))
        self.assertEqual(migrate.move(self.rclone, self.old, self.new, force=True, clock=lambda: now, report=self.said.append), 4)

    def test_a_bucket_that_already_has_files_is_refused(self):
        write(os.path.join(self.new, "something.txt"), "x")
        with self.assertRaises(migrate.MigrateError):
            migrate.move(self.rclone, self.old, self.new)
        self.assertFalse(os.path.exists(os.path.join(self.new, "state.json")))

    def test_switching_this_pc_keeps_a_copy_of_the_old_settings(self):
        home = Home(os.path.join(self.tmp.name, "pc"))
        b2 = {"type": "b2", "bucket": "old", "account": "kid", "key": "k"}
        home.apply_invite(invites.read(invites.make(ENV, "", b2)), "MCboy23", total_gb=16)
        migrate.switch_this_pc(home, R2)
        self.assertEqual(home.storage_kind()["type"], "r2")
        self.assertEqual(home.settings()["SHARED_REMOTE"], "smp:japax-world")
        self.assertIn("type = b2", read(home.rclone_conf + ".before-move"))

    def test_a_pc_already_on_r2_is_not_moved_again(self):
        home = Home(os.path.join(self.tmp.name, "pc"))
        home.apply_invite(invites.read(invites.make(ENV, "", R2)), "MCboy23", total_gb=16)
        with self.assertRaises(migrate.MigrateError):
            migrate.run(home, R2, self.rclone, report=self.said.append)


if __name__ == "__main__":
    unittest.main()
