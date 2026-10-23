"""Shared hosting through rclone. These run the real rclone binary against a plain local folder (rclone treats a local
path as just another remote), so they exercise the same commands that talk to Google Drive / R2 / B2. They are skipped
when rclone isn't installed."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from app import shared
from app.rclone import Rclone, RcloneError, join
from app.shared import HostingError, RcloneWorld

HAVE_RCLONE = bool(shutil.which("rclone"))


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def read(path):
    with open(path) as f:
        return f.read()


class FakeRun:
    """Stands in for subprocess.run: records the command and returns what the test says rclone would."""
    def __init__(self, code=0, out=b"", err=b""):
        self.code, self.out, self.err, self.calls = code, out, err, []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, self.code, self.out, self.err)


class WrapperTests(unittest.TestCase):
    def test_join(self):
        self.assertEqual(join("jx:", "state.json"), "jx:state.json")
        self.assertEqual(join("jx:japax", "data", "world"), "jx:japax/data/world")
        self.assertEqual(join("C:\\cloud", "data"), "C:\\cloud/data")

    def test_missing_files_are_none_not_errors(self):
        for code in (3, 4):
            rc = Rclone("rclone", run=FakeRun(code=code), which=lambda exe: "rclone")
            self.assertIsNone(rc.cat("jx:state.json"))
            self.assertEqual(rc.lsjson("jx:data"), [])

    def test_storage_that_answers_a_missing_file_with_success_and_nothing_counts_as_missing(self):
        # Backblaze B2: `rclone cat` of a file that doesn't exist exits 0 with empty output, and lsjson of a missing
        # folder exits 0 with []. That is a brand new shared world, not a damaged lock file.
        rc = Rclone("rclone", run=FakeRun(code=0, out=b""), which=lambda exe: "rclone")
        self.assertIsNone(rc.cat("smp:bucket/state.json"))
        world = RcloneWorld("smp:bucket", tempfile.gettempdir(), "Carlos", rc)
        self.assertIsNone(world.read_state())
        self.assertIsNone(world.read_identity())

    def test_real_failures_are_errors_with_rclones_message(self):
        rc = Rclone("rclone", run=FakeRun(code=1, err=b"2026 NOTICE: x\n2026 ERROR : couldn't connect to the internet\n"),
                    which=lambda exe: "rclone")
        with self.assertRaises(RcloneError) as ctx:
            rc.cat("jx:state.json")
        self.assertIn("couldn't connect", str(ctx.exception))

    def test_not_installed(self):
        rc = Rclone("no-such-rclone", which=lambda exe: None)
        self.assertFalse(rc.available)
        with self.assertRaises(RcloneError) as ctx:
            rc.cat("jx:x")
        self.assertIn("winget install Rclone.Rclone", str(ctx.exception))


class UnreachableStorageTests(unittest.TestCase):
    def world(self, run):
        rc = Rclone("rclone", run=run, which=lambda exe: "rclone")
        return RcloneWorld("jx:japax", tempfile.gettempdir(), "Carlos", rc)

    def test_a_network_failure_is_never_mistaken_for_an_empty_folder(self):
        world = self.world(FakeRun(code=1, err=b"ERROR : dial tcp: no such host"))
        with self.assertRaises(HostingError) as ctx:
            world.acquire(claim_wait=0)           # must NOT carry on as if this were a brand new shared folder
        self.assertIn("Can't reach the shared storage", str(ctx.exception))

    def test_rclone_missing_says_how_to_install_it(self):
        rc = Rclone("rclone", which=lambda exe: None)
        with self.assertRaises(HostingError) as ctx:
            RcloneWorld("jx:japax", tempfile.gettempdir(), "Carlos", rc).acquire(claim_wait=0)
        self.assertIn("winget install Rclone.Rclone", str(ctx.exception))

    def test_hitting_the_free_daily_limit_says_so_instead_of_a_cryptic_error(self):
        world = self.world(FakeRun(code=1, err=b"ERROR : state.json: Failed to open: Cannot download file, download "
                                               b"bandwidth or transaction (Class B) cap exceeded."))
        with self.assertRaises(HostingError) as ctx:
            world.read_state()
        self.assertIn("free daily limit", str(ctx.exception))
        self.assertNotIn("rclone", str(ctx.exception))

    def test_unreadable_state_file_is_an_error_not_a_fresh_start(self):
        world = self.world(FakeRun(code=0, out=b"{not json"))
        with self.assertRaises(HostingError) as ctx:
            world.read_state()
        self.assertIn("unreadable", str(ctx.exception))


@unittest.skipUnless(HAVE_RCLONE, "rclone is not installed")
class RcloneSharedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.cloud = os.path.join(self.root, "cloud")        # the 'remote'
        os.makedirs(self.cloud)
        self.clock = Clock()
        self.a_dir, self.b_dir = (os.path.join(self.root, n, "server") for n in ("pcA", "pcB"))
        os.makedirs(self.a_dir)
        os.makedirs(self.b_dir)

    def host(self, name, server_dir, **kwargs):
        return RcloneWorld(self.cloud, server_dir, name, Rclone("rclone"), clock=self.clock.time,
                           sleep=self.clock.sleep, **kwargs)

    def seed_world(self, directory, level="world v1", players=("a", "b")):
        write(os.path.join(directory, "world", "level.dat"), level)
        for p in players:
            write(os.path.join(directory, "world", "players", "data", f"{p}.dat"), f"{p}'s inventory")
        write(os.path.join(directory, "world", "region", "r.0.0.mca"), "chunks")
        write(os.path.join(directory, "mods", "waystones.jar"), "jar")
        write(os.path.join(directory, "EasyAuth", "easyauth.db"), "passwords")
        write(os.path.join(directory, "server.properties"), "motd=JAPAX x RENE")
        write(os.path.join(directory, "ops.json"), "[ops]")

    # -- the basics
    def test_world_travels_from_one_pc_to_the_next_and_back(self):
        a = self.host("Carlos", self.a_dir)
        self.assertEqual(a.acquire(claim_wait=0), {})
        self.seed_world(self.a_dir)
        write(os.path.join(self.a_dir, "world", "session.lock"), "never synced")
        write(os.path.join(self.a_dir, "world", "desktop.ini"), "never synced")
        count, _nbytes = a.push()
        self.assertEqual(count, 8)                             # level, 2 players, region, mod, db, properties, ops
        a.release()
        self.assertFalse(os.path.exists(os.path.join(self.cloud, "data", "world", "session.lock")))
        self.assertFalse(os.path.exists(os.path.join(self.cloud, "data", "world", "desktop.ini")))

        b = self.host("Friend", self.b_dir)
        write(os.path.join(self.b_dir, "mods", "old-mod.jar"), "stale")
        write(os.path.join(self.b_dir, "libraries", "keep.txt"), "not part of the sync")
        previous = b.acquire(claim_wait=0)
        self.assertEqual(previous["version"], 1)
        b.pull()
        self.assertEqual(read(os.path.join(self.b_dir, "world", "level.dat")), "world v1")
        self.assertEqual(read(os.path.join(self.b_dir, "world", "players", "data", "a.dat")), "a's inventory")
        self.assertEqual(read(os.path.join(self.b_dir, "EasyAuth", "easyauth.db")), "passwords")
        self.assertEqual(read(os.path.join(self.b_dir, "server.properties")), "motd=JAPAX x RENE")
        self.assertFalse(os.path.exists(os.path.join(self.b_dir, "mods", "old-mod.jar")))
        self.assertTrue(os.path.exists(os.path.join(self.b_dir, "libraries", "keep.txt")))
        self.assertTrue(b.can_keep_local_world())

        write(os.path.join(self.b_dir, "world", "level.dat"), "world v2 (friend played)")
        write(os.path.join(self.b_dir, "world", "players", "data", "c.dat"), "a new player")
        b.push()
        b.release()
        a2 = self.host("Carlos", self.a_dir)
        a2.acquire(claim_wait=0)
        a2.pull()
        self.assertEqual(read(os.path.join(self.a_dir, "world", "level.dat")), "world v2 (friend played)")
        self.assertEqual(read(os.path.join(self.a_dir, "world", "players", "data", "c.dat")), "a new player")
        self.assertEqual(a2.read_state()["version"], 2)

    def test_the_other_pc_sees_the_lock_at_once(self):
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        with self.assertRaises(HostingError) as ctx:
            self.host("Friend", self.b_dir).acquire(claim_wait=0)       # no sync delay: it is refused immediately
        self.assertIn("Carlos is already hosting", str(ctx.exception))
        a.release()
        self.host("Friend", self.b_dir).acquire(claim_wait=0)           # and released immediately

    def test_only_changed_files_are_uploaded_again(self):
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        self.seed_world(self.a_dir)
        a.push()
        stamp = os.stat(os.path.join(self.cloud, "data", "world", "region", "r.0.0.mca")).st_mtime_ns
        write(os.path.join(self.a_dir, "world", "level.dat"), "changed")
        self.clock.sleep(60)
        a.push()
        self.assertEqual(os.stat(os.path.join(self.cloud, "data", "world", "region", "r.0.0.mca")).st_mtime_ns, stamp)
        self.assertEqual(read(os.path.join(self.cloud, "data", "world", "level.dat")), "changed")

    def test_a_pc_can_resume_its_own_lock_and_keeps_its_world(self):
        first = self.host("Carlos", self.a_dir)
        first.acquire(claim_wait=0)
        self.seed_world(self.a_dir)
        first.push()
        write(os.path.join(self.a_dir, "world", "level.dat"), "v2 (newer, never uploaded)")
        again = self.host("Carlos", self.a_dir)
        previous = again.acquire(claim_wait=0)
        self.assertTrue(shared.resumed_own_lock(previous))
        self.assertTrue(again.can_keep_local_world())
        self.assertEqual(read(os.path.join(self.a_dir, "world", "level.dat")), "v2 (newer, never uploaded)")

    # -- history: a bad upload can be undone
    def test_what_an_upload_replaces_is_kept_in_history_and_old_history_is_cleaned(self):
        a = self.host("Carlos", self.a_dir, history_days=7)
        a.acquire(claim_wait=0)
        self.seed_world(self.a_dir, level="good world")
        a.push()
        os.makedirs(os.path.join(self.cloud, "history", "19600101-000000", "world"))       # ancient
        write(os.path.join(self.cloud, "history", "19600101-000000", "world", "level.dat"), "ancient")
        write(os.path.join(self.a_dir, "world", "level.dat"), "newer world")
        os.remove(os.path.join(self.a_dir, "world", "players", "data", "b.dat"))
        a.skip_checks = True                                                                  # (player files shrank)
        self.clock.sleep(3600)
        a.push()
        kept = {}
        for dirpath, _d, files in os.walk(os.path.join(self.cloud, "history")):
            for name in files:
                kept[os.path.relpath(os.path.join(dirpath, name), os.path.join(self.cloud, "history")).split(os.sep, 1)[1]] \
                    = read(os.path.join(dirpath, name))
        self.assertEqual(kept.get("world\\level.dat", kept.get("world/level.dat")), "good world")
        self.assertIn("b's inventory", kept.get("world\\players\\data\\b.dat", kept.get("world/players/data/b.dat", "")))
        self.assertFalse(os.path.exists(os.path.join(self.cloud, "history", "19600101-000000")))   # pruned by age

    def test_history_can_be_turned_off(self):
        a = self.host("Carlos", self.a_dir, history_days=0)
        a.acquire(claim_wait=0)
        self.seed_world(self.a_dir)
        a.push()
        write(os.path.join(self.a_dir, "world", "level.dat"), "changed")
        self.clock.sleep(60)
        a.push()
        self.assertFalse(os.path.exists(os.path.join(self.cloud, "history")))

    # -- the safety checks
    def test_a_world_with_fewer_players_never_replaces_the_real_one(self):
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        self.seed_world(self.a_dir, players=("a", "b", "c"))
        a.push()
        a.release()
        # the friend's PC has a brand new default world: one new player, no passwords
        b = self.host("Friend", self.b_dir)
        b.acquire(claim_wait=0)
        write(os.path.join(self.b_dir, "world", "level.dat"), "fresh default world")
        write(os.path.join(self.b_dir, "world", "players", "data", "x.dat"), "new")
        with self.assertRaises(HostingError) as ctx:
            b.push()
        self.assertIn("Not uploading", str(ctx.exception))
        self.assertEqual(read(os.path.join(self.cloud, "data", "world", "level.dat")), "world v1")   # untouched
        self.assertEqual(b.read_state()["version"], 1)
        b.skip_checks = True                                                                          # explicit override
        self.clock.sleep(60)
        b.push()
        self.assertEqual(b.read_state()["version"], 2)

    def test_a_world_without_level_dat_is_refused(self):
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        self.seed_world(self.a_dir)
        a.push()
        os.remove(os.path.join(self.a_dir, "world", "level.dat"))
        with self.assertRaises(HostingError) as ctx:
            a.push()
        self.assertIn("level.dat", str(ctx.exception))

    def test_a_world_missing_half_its_files_is_refused(self):
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        self.seed_world(self.a_dir)
        for i in range(30):
            write(os.path.join(self.a_dir, "world", "region", f"r.{i}.0.mca"), "chunks")
        a.push()
        for i in range(30):
            os.remove(os.path.join(self.a_dir, "world", "region", f"r.{i}.0.mca"))
        with self.assertRaises(HostingError) as ctx:
            a.push()
        self.assertIn("files", str(ctx.exception))

    def test_a_download_is_checked_against_what_was_recorded(self):
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        self.seed_world(self.a_dir)
        a.push()
        a.release()
        os.remove(os.path.join(self.cloud, "data", "world", "players", "data", "a.dat"))   # storage lost a file
        with self.assertRaises(HostingError) as ctx:
            self.host("Friend", self.b_dir).acquire(claim_wait=0)
        self.assertIn("doesn't match the last upload", str(ctx.exception))
        self.host("Friend", self.b_dir).acquire(takeover=True, claim_wait=0)               # only on purpose

    # -- an upload that was cut off
    def cut_off_upload(self, world):
        real = world.rclone.sync
        calls = []

        def sync(src, dst, **kwargs):
            calls.append(dst)
            if len(calls) == 2:
                raise RcloneError("connection reset")
            return real(src, dst, **kwargs)
        world.rclone.sync = sync

    def test_an_upload_that_was_cut_off_is_flagged_and_the_same_pc_just_uploads_again(self):
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        self.seed_world(self.a_dir)
        a.push()
        write(os.path.join(self.a_dir, "world", "level.dat"), "v2")
        self.cut_off_upload(a)
        self.clock.sleep(60)
        with self.assertRaises(HostingError) as ctx:
            a.push()
        self.assertIn("Uploading the world failed", str(ctx.exception))
        state = a.read_state()
        self.assertEqual(state["version"], 1)                    # never recorded as uploaded
        self.assertIn("pushing", state)
        a.release()                                              # e.g. Ctrl+C

        other = self.host("Friend", self.b_dir)                  # someone else must not use the mixed-up copy...
        with self.assertRaises(HostingError) as ctx:
            other.acquire(claim_wait=0)
        self.assertIn("never finished", str(ctx.exception))
        other.acquire(takeover=True, claim_wait=0)               # ...unless they really mean to
        other.release()

        again = self.host("Carlos", self.a_dir)                  # but this PC still has the complete world
        previous = again.acquire(claim_wait=0)
        self.assertTrue(shared.resumed_own_lock(previous))
        self.assertTrue(again.can_keep_local_world())
        again.push()
        self.assertNotIn("pushing", again.read_state())          # finished this time
        self.assertEqual(read(os.path.join(self.cloud, "data", "world", "level.dat")), "v2")

    def test_a_download_that_was_cut_off_is_fetched_again(self):
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        self.seed_world(self.a_dir)
        a.push()
        a.release()
        b = self.host("Friend", self.b_dir)
        b.acquire(claim_wait=0)
        real = b.rclone.sync

        def sync(src, dst, **kwargs):
            if dst.endswith("mods"):
                raise RcloneError("window closed")
            return real(src, dst, **kwargs)
        b.rclone.sync = sync
        with self.assertRaises(HostingError):
            b.pull()
        self.assertFalse(b.can_keep_local_world())
        b.rclone.sync = real
        again = self.host("Friend", self.b_dir)
        previous = again.acquire(claim_wait=0)
        self.assertTrue(shared.resumed_own_lock(previous))
        self.assertFalse(again.can_keep_local_world())           # so the controller downloads again
        again.pull()
        self.assertTrue(again.can_keep_local_world())
        self.assertEqual(read(os.path.join(self.b_dir, "EasyAuth", "easyauth.db")), "passwords")

    # -- the lock
    def test_conflicting_copies_of_the_lock_file_stop_the_start(self):
        write(os.path.join(self.cloud, "state (1).json"), "{}")
        with self.assertRaises(HostingError):
            self.host("Carlos", self.a_dir).acquire(claim_wait=0)

    def test_two_hosts_starting_at_the_same_moment_one_loses(self):
        a = self.host("Carlos", self.a_dir)

        def rival_writes_during_the_pause(seconds):
            self.clock.sleep(seconds)
            state = a.read_state()
            state.update(token="rivals-token", host="Friend")
            a._write_state(state)
        a.sleep = rival_writes_during_the_pause
        with self.assertRaises(HostingError) as ctx:
            a.acquire(claim_wait=20)
        self.assertIn("same moment", str(ctx.exception))
        self.assertFalse(a.holds_lock())

    def test_stale_lock_needs_takeover_and_the_old_host_cannot_overwrite(self):
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        self.seed_world(self.a_dir)
        a.push()
        self.clock.sleep(shared.STALE_SECONDS + 60)
        b = self.host("Friend", self.b_dir)
        with self.assertRaises(HostingError) as ctx:
            b.acquire(claim_wait=0)
        self.assertIn("-Takeover", str(ctx.exception))
        b.acquire(takeover=True, claim_wait=0)
        with self.assertRaises(HostingError):
            a.push()

    def test_state_file_is_plain_json_you_can_read_with_rclone_cat(self):
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        state = json.loads(read(os.path.join(self.cloud, "state.json")))
        self.assertEqual((state["status"], state["host"]), ("hosting", "Carlos"))


if __name__ == "__main__":
    unittest.main()


class ChangedContentIsNeverSkippedTests(unittest.TestCase):
    """The silent-loss bug: a world file whose content changed but whose size and modified time did not."""

    def test_a_rewritten_file_with_the_same_size_and_time_is_still_copied(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src, dst = os.path.join(tmp.name, "src"), os.path.join(tmp.name, "dst")
        os.makedirs(src)
        path = os.path.join(src, "r.0.0.mca")
        with open(path, "wb") as f:
            f.write(b"A" * 8192)
        stamp = os.stat(path).st_mtime_ns
        rclone = Rclone("rclone")
        rclone.sync(src, dst)
        with open(path, "wb") as f:
            f.write(b"B" * 8192)
        os.utime(path, ns=(stamp, stamp))                              # what a stale Windows timestamp looks like
        rclone.sync(src, dst)
        with open(os.path.join(dst, "r.0.0.mca"), "rb") as f:
            self.assertEqual(f.read()[:1], b"B")
        with open(path, "wb") as f:
            f.write(b"C" * 8192)
        os.utime(path, ns=(stamp, stamp))
        rclone.copy_files(src, dst, ["r.0.0.mca"])                     # the loose files too
        with open(os.path.join(dst, "r.0.0.mca"), "rb") as f:
            self.assertEqual(f.read()[:1], b"C")


class RefusedDownloadIsNotAMissingFileTests(unittest.TestCase):
    """B2 answers a refused download (the free daily download limit) with 'success, nothing', exactly like a missing file.
    A missing lock file means 'brand new shared world'; a refused one must never be taken for that."""

    class FakeRun:
        def __init__(self, listing):
            self.listing, self.calls = listing, []

        def __call__(self, cmd, input=None, capture_output=True, timeout=None, creationflags=0):
            self.calls.append(cmd[1])
            out = json.dumps(self.listing).encode() if cmd[1] == "lsjson" else b""
            return subprocess.CompletedProcess(cmd, 0, out, b"")

    def test_nothing_back_for_a_file_that_exists_is_an_error_not_none(self):
        run = self.FakeRun([{"Name": "state.json", "IsDir": False, "Size": 193}])
        rclone = Rclone("rclone", run=run, which=lambda exe: "rclone")
        with self.assertRaises(RcloneError) as caught:
            rclone.cat("smp:japax-java-2026/state.json")
        self.assertIn("download_cap_exceeded", str(caught.exception))
        self.assertTrue(shared.is_cap_error(caught.exception))                 # and the app words it as the daily limit

    def test_nothing_back_for_a_file_that_is_really_missing_is_none(self):
        run = self.FakeRun([{"Name": "identity.json", "IsDir": False}])
        rclone = Rclone("rclone", run=run, which=lambda exe: "rclone")
        self.assertIsNone(rclone.cat("smp:japax-java-2026/state.json"))

    def test_the_lock_read_says_the_limit_is_used_up_instead_of_looking_empty(self):
        run = self.FakeRun([{"Name": "state.json", "IsDir": False}])
        world = RcloneWorld("smp:japax-java-2026", "server", "A", Rclone("rclone", run=run, which=lambda exe: "rclone"))
        with self.assertRaises(HostingError) as caught:
            world.read_state()
        self.assertIn("daily limit", str(caught.exception))
