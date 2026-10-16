"""Shared hosting tests (standard library only): two 'PCs' (A and B) take turns through one shared folder."""
import asyncio
import json
import os
import tempfile
import unittest

from app import shared, voice
from app.hosting import Hosting
from app.manager import PlayersOnline
from app.shared import HostingError, SharedWorld


class Clock:
    """A fake clock whose sleep() just advances time, so nothing really waits."""
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


class Setup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.cloud = os.path.join(self.root, "cloud")
        os.makedirs(self.cloud)
        self.clock = Clock()
        self.a_dir, self.b_dir = (os.path.join(self.root, n, "server") for n in ("pcA", "pcB"))
        os.makedirs(self.a_dir)
        os.makedirs(self.b_dir)

    def host(self, name, server_dir, clock=None):
        clock = clock or self.clock
        return SharedWorld(self.cloud, server_dir, name, clock=clock.time, sleep=clock.sleep)


class LockAndHandoverTests(Setup):
    def test_world_travels_from_one_pc_to_the_next(self):
        a = self.host("Carlos", self.a_dir)
        previous = a.acquire(claim_wait=0)
        self.assertEqual(previous, {})                                    # brand new shared folder: nothing to pull
        write(os.path.join(self.a_dir, "world", "level.dat"), "world v1")
        write(os.path.join(self.a_dir, "world", "region", "r.0.0.mca"), "chunks")
        write(os.path.join(self.a_dir, "EasyAuth", "easyauth.db"), "passwords")     # players' passwords travel too
        write(os.path.join(self.a_dir, "mods", "voicechat.jar"), "jar")
        write(os.path.join(self.a_dir, "ops.json"), "[ops]")
        write(os.path.join(self.a_dir, "world", "session.lock"), "never synced")
        a.push()
        a.release()

        b = self.host("Friend", self.b_dir)
        write(os.path.join(self.b_dir, "mods", "old-mod.jar"), "stale")                # leftovers are replaced
        write(os.path.join(self.b_dir, "libraries", "keep.txt"), "not part of the sync")
        previous = b.acquire(claim_wait=0)
        self.assertEqual(previous["version"], 1)
        b.pull()
        self.assertEqual(read(os.path.join(self.b_dir, "world", "level.dat")), "world v1")
        self.assertEqual(read(os.path.join(self.b_dir, "EasyAuth", "easyauth.db")), "passwords")
        self.assertEqual(read(os.path.join(self.b_dir, "ops.json")), "[ops]")
        self.assertFalse(os.path.exists(os.path.join(self.b_dir, "mods", "old-mod.jar")))
        self.assertFalse(os.path.exists(os.path.join(self.b_dir, "world", "session.lock")))
        self.assertTrue(os.path.exists(os.path.join(self.b_dir, "libraries", "keep.txt")))

        write(os.path.join(self.b_dir, "world", "level.dat"), "world v2 (friend played)")
        b.push()
        b.release()
        a2 = self.host("Carlos", self.a_dir)
        a2.acquire(claim_wait=0)
        a2.pull()
        self.assertEqual(read(os.path.join(self.a_dir, "world", "level.dat")), "world v2 (friend played)")
        self.assertEqual(a2.read_state()["version"], 2)

    def test_second_host_is_refused_while_the_first_is_alive(self):
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        self.clock.sleep(60)
        a.heartbeat()
        self.clock.sleep(120)                                               # 2 minutes later, still fresh
        b = self.host("Friend", self.b_dir)
        with self.assertRaises(HostingError) as ctx:
            b.acquire(claim_wait=0)
        self.assertIn("Carlos is already hosting", str(ctx.exception))
        with self.assertRaises(HostingError):                              # takeover never overrides a live host
            b.acquire(takeover=True, claim_wait=0)

    def test_stale_lock_needs_an_explicit_takeover(self):
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        a.push()
        self.clock.sleep(shared.STALE_SECONDS + 60)                         # Carlos's PC died
        b = self.host("Friend", self.b_dir)
        with self.assertRaises(HostingError) as ctx:
            b.acquire(claim_wait=0)
        self.assertIn("-Takeover", str(ctx.exception))
        b.acquire(takeover=True, claim_wait=0)
        self.assertTrue(b.holds_lock())
        self.assertFalse(a.holds_lock())                                    # the old host has lost the lock...
        write(os.path.join(self.a_dir, "world", "level.dat"), "late write")
        with self.assertRaises(HostingError):                              # ...and can't overwrite the new host's world
            a.push()

    def test_a_pc_can_resume_its_own_lock_after_a_closed_window_or_crash(self):
        first = self.host("Carlos", self.a_dir)
        first.acquire(claim_wait=0)
        write(os.path.join(self.a_dir, "world", "level.dat"), "v1")
        first.push()                                                         # shared copy: version 1
        write(os.path.join(self.a_dir, "world", "level.dat"), "v2 (newer, never uploaded)")
        # window closed with the X: lock still says hosting; the same PC starts again, a fresh one and a stale one
        for minutes in (1, 60):
            self.clock.sleep(minutes * 60)
            again = self.host("Carlos", self.a_dir)
            previous = again.acquire(claim_wait=0)                          # no -Takeover needed for its own lock
            self.assertTrue(shared.resumed_own_lock(previous))
            self.assertTrue(again.holds_lock())
            self.assertEqual(again.read_state()["host"], "Carlos")
            self.assertNotIn("resumedOwnLock", again.read_state())
            self.assertEqual(read(os.path.join(self.a_dir, "world", "level.dat")), "v2 (newer, never uploaded)")

    def test_only_the_same_host_name_may_resume_a_lock(self):
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        self.clock.sleep(10)
        with self.assertRaises(HostingError):
            self.host("Friend", self.b_dir).acquire(claim_wait=0)           # a different name is still refused
        self.assertFalse(shared.resumed_own_lock(a.read_state()))

    def test_waits_for_the_cloud_to_finish_syncing(self):
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        write(os.path.join(self.a_dir, "world", "level.dat"), "x" * 100)
        write(os.path.join(self.a_dir, "world", "extra.dat"), "y" * 50)
        a.push()
        a.release()
        late_file = os.path.join(a.data_dir, "world", "extra.dat")
        saved = read(late_file)
        os.remove(late_file)                                                # Drive hasn't delivered this file yet
        arrived = []

        def drive_catches_up(seconds):
            self.clock.sleep(seconds)
            if len(arrived) == 0 and self.clock.now > 1_000_000 + 20:
                write(late_file, saved)
                arrived.append(True)
        b = SharedWorld(self.cloud, self.b_dir, "Friend", clock=self.clock.time, sleep=drive_catches_up)
        b.acquire(claim_wait=0)
        self.assertTrue(arrived)                                           # it waited until the file showed up
        b.pull()
        self.assertEqual(read(os.path.join(self.b_dir, "world", "extra.dat")), "y" * 50)

    def test_gives_up_if_the_cloud_never_catches_up(self):
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        write(os.path.join(self.a_dir, "world", "level.dat"), "data")
        a.push()
        a.release()
        os.remove(os.path.join(a.data_dir, "world", "level.dat"))
        with self.assertRaises(HostingError) as ctx:
            self.host("Friend", self.b_dir).acquire(claim_wait=0, sync_timeout=30)
        self.assertIn("doesn't match", str(ctx.exception))

    def test_two_hosts_starting_at_the_same_moment_one_loses(self):
        a = self.host("Carlos", self.a_dir)

        def rival_writes_during_the_pause(seconds):
            self.clock.sleep(seconds)
            state = a.read_state()
            state.update(token="rivals-token", host="Friend")               # the friend's claim lands after ours
            a._write_state(state)
        a.sleep = rival_writes_during_the_pause
        with self.assertRaises(HostingError) as ctx:
            a.acquire(claim_wait=20)
        self.assertIn("same moment", str(ctx.exception))
        self.assertFalse(a.holds_lock())

    def test_drive_conflict_copies_of_the_state_file_stop_the_start(self):
        write(os.path.join(self.cloud, "state (1).json"), "{}")
        with self.assertRaises(HostingError):
            self.host("Carlos", self.a_dir).acquire(claim_wait=0)

    def test_missing_shared_folder_is_a_clear_error(self):
        with self.assertRaises(HostingError) as ctx:
            SharedWorld(os.path.join(self.root, "nope"), self.a_dir, "Carlos").acquire(claim_wait=0)
        self.assertIn("Google Drive", str(ctx.exception))

    def test_heartbeats_during_uploads_never_lose_the_new_version(self):
        import threading
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        write(os.path.join(self.a_dir, "world", "level.dat"), "x")
        stop = threading.Event()

        def hammer():
            while not stop.is_set():
                a.heartbeat()
        thread = threading.Thread(target=hammer)
        thread.start()
        try:
            for i in range(30):
                write(os.path.join(self.a_dir, "world", "level.dat"), "x" * (i + 2))
                a.push()
        finally:
            stop.set()
            thread.join()
        self.assertEqual(a.read_state()["version"], 30)              # no upload was lost to a racing heartbeat

    def test_release_is_idempotent_and_heartbeat_only_for_the_owner(self):
        a = self.host("Carlos", self.a_dir)
        a.acquire(claim_wait=0)
        started = a.read_state()["lastSeen"]
        self.clock.sleep(60)
        a.heartbeat()
        self.assertEqual(a.read_state()["lastSeen"], started + 60)
        b = self.host("Friend", self.b_dir)
        b.token = "not-the-token"
        self.clock.sleep(60)
        b.heartbeat()                                                      # someone else's heartbeat does nothing
        self.assertEqual(a.read_state()["lastSeen"], started + 60)
        a.release()
        a.release()
        state = a.read_state()
        self.assertEqual((state["status"], state["host"], state["lastHost"]), ("idle", None, "Carlos"))


class MirrorTests(Setup):
    def test_unchanged_files_are_not_copied_again_and_deleted_ones_disappear(self):
        src, dst = os.path.join(self.root, "src"), os.path.join(self.root, "dst")
        write(os.path.join(src, "a.txt"), "1")
        write(os.path.join(src, "sub", "b.txt"), "2")
        shared.mirror_dir(src, dst)
        stamp = os.stat(os.path.join(dst, "a.txt")).st_mtime_ns
        shared.mirror_dir(src, dst)
        self.assertEqual(os.stat(os.path.join(dst, "a.txt")).st_mtime_ns, stamp)
        os.remove(os.path.join(src, "sub", "b.txt"))
        shared.mirror_dir(src, dst)
        self.assertFalse(os.path.exists(os.path.join(dst, "sub")))          # file and its empty folder are gone

    def test_manifest_counts_only_what_is_synced(self):
        write(os.path.join(self.a_dir, "world", "level.dat"), "12345")
        write(os.path.join(self.a_dir, "world", "session.lock"), "ignored")
        write(os.path.join(self.a_dir, "logs", "latest.log"), "ignored")
        write(os.path.join(self.a_dir, "ops.json"), "123")
        self.assertEqual(shared.manifest(self.a_dir), (2, 8))


class HandoverTests(Setup):
    def run_async(self, coro):
        return asyncio.run(coro)

    def fake_manager(self, players=0, state="asleep"):
        class M:
            pass
        m = M()
        m.players, m.names, m.state = players, ["Alex"] if players else [], state
        m.stop_calls = 0

        async def stop(force=False):
            m.stop_calls += 1
            m.state = "asleep"
            return True
        m.stop = stop
        return m

    def test_handover_refuses_while_players_are_online(self):
        async def go():
            world = self.host("Carlos", self.a_dir)
            world.acquire(claim_wait=0)
            hosting = Hosting(world, self.fake_manager(players=2, state="online"))
            with self.assertRaises(PlayersOnline):
                await hosting.handover()
            self.assertTrue(world.holds_lock())
            self.assertFalse(hosting.exit_event.is_set())
        self.run_async(go())

    def test_handover_uploads_releases_and_asks_the_controller_to_quit(self):
        async def go():
            world = self.host("Carlos", self.a_dir)
            world.acquire(claim_wait=0)
            write(os.path.join(self.a_dir, "world", "level.dat"), "played")
            hosting = Hosting(world, self.fake_manager(state="online"))
            hosting.mark_started()
            await hosting.handover()
            await asyncio.wait_for(hosting.exit_event.wait(), 5)
            return world
        world = self.run_async(go())
        state = world.read_state()
        self.assertEqual((state["status"], state["version"]), ("idle", 1))
        self.assertEqual(read(os.path.join(world.data_dir, "world", "level.dat")), "played")


class CleanupTests(Setup):
    """What happens when the controller is closed with Ctrl+C while hosting."""
    def app(self, world, server_running, dirty):
        from app.config import Config
        from app.main import App

        class Runtime:
            def __init__(self):
                self.running, self.stops = server_running, 0

            def state(self, name=""):
                return "running" if self.running else "exited"

            def stop(self, name="", grace=120):
                self.stops += 1
                write(os.path.join(world.server_dir, "world", "level.dat"), "saved on stop")
                self.running = False
        app = App()
        app.cfg = Config(server_dir=world.server_dir, backup_dir="")
        app.runtime = Runtime()
        app.shared = world
        app.hosting = Hosting(world, None)
        app.hosting.dirty = dirty
        return app

    def test_ctrl_c_with_a_running_server_stops_it_uploads_and_releases(self):
        from app.main import cleanup
        world = self.host("Carlos", self.a_dir)
        world.acquire(claim_wait=0)
        app = self.app(world, server_running=True, dirty=False)
        cleanup(app)
        self.assertEqual(app.runtime.stops, 1)
        state = world.read_state()
        self.assertEqual((state["status"], state["version"]), ("idle", 1))
        self.assertEqual(read(os.path.join(world.data_dir, "world", "level.dat")), "saved on stop")

    def test_a_clean_exit_with_nothing_changed_just_releases(self):
        from app.main import cleanup
        world = self.host("Carlos", self.a_dir)
        world.acquire(claim_wait=0)
        cleanup(self.app(world, server_running=False, dirty=False))
        self.assertEqual(world.read_state()["version"], 0)               # nothing uploaded
        self.assertEqual(world.read_state()["status"], "idle")

    def test_failed_acquire_never_touches_anything(self):
        from app.main import cleanup
        other = self.host("Friend", self.b_dir)
        other.acquire(claim_wait=0)
        mine = self.host("Carlos", self.a_dir)
        with self.assertRaises(HostingError):
            mine.acquire(claim_wait=0)
        app = self.app(mine, server_running=True, dirty=True)
        cleanup(app)                                                     # no lock: must not stop, upload or release
        self.assertEqual(app.runtime.stops, 0)
        self.assertEqual(other.read_state()["host"], "Friend")


class VoiceHostTests(Setup):
    def config_file(self, text="port=24454\nvoice_host=\n"):
        path = os.path.join(self.a_dir, voice.CONFIG)
        write(path, text)
        return path

    def test_auto_uses_this_pcs_public_ip(self):
        path = self.config_file()
        voice._cache.update(ip=None, at=0.0)
        self.assertTrue(voice.apply(self.a_dir, "auto", fetch=lambda: "203.0.113.7"))
        self.assertIn("voice_host=203.0.113.7:24454", read(path))
        self.assertFalse(voice.apply(self.a_dir, "auto", fetch=lambda: "203.0.113.7"))   # no change, no rewrite

    def test_explicit_off_and_blank(self):
        path = self.config_file("port=24454\nvoice_host=old:1\n")
        voice.apply(self.a_dir, "voice.example.com:24454")
        self.assertIn("voice_host=voice.example.com:24454", read(path))
        voice.apply(self.a_dir, "off")
        self.assertIn("voice_host=\n", read(path))
        before = read(path)
        voice.apply(self.a_dir, "")                                          # blank means: don't touch the file
        self.assertEqual(read(path), before)

    def test_missing_config_is_ignored(self):
        self.assertFalse(voice.apply(self.a_dir, "off"))


if __name__ == "__main__":
    unittest.main()
