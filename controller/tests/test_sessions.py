"""The log of who hosted and when (sessions.py), kept in the shared storage."""
import os
import tempfile
import time
import unittest

from app import sessions
from app.rclone import Rclone
from app.shared import RcloneWorld, SharedWorld

HOUR = 3600


class LogLogicTests(unittest.TestCase):
    def test_a_session_has_a_host_a_start_and_an_end(self):
        log = sessions.begin([], "hypernacho", "t1", 1000.0)
        self.assertEqual((log[0]["host"], log[0]["start"], log[0]["end"]), ("hypernacho", 1000.0, None))
        log = sessions.finish(log, "t1", 1000.0 + 4 * HOUR, version=7)
        self.assertEqual((log[0]["ended"], log[0]["version"], log[0]["end"]), ("stopped", 7, 1000.0 + 4 * HOUR))

    def test_a_crashed_session_is_closed_at_the_last_time_it_was_seen(self):
        log = sessions.begin([], "A", "t1", 1000.0)
        previous = {"status": "hosting", "host": "A", "lastSeen": 1000.0 + 3 * HOUR}
        log = sessions.begin(log, "B", "t2", 1000.0 + 5 * HOUR, previous)
        self.assertEqual((log[0]["end"], log[0]["ended"]), (1000.0 + 3 * HOUR, sessions.NO_STOP))
        self.assertEqual((log[1]["host"], log[1]["end"]), ("B", None))

    def test_resuming_your_own_lock_continues_the_same_session(self):
        log = sessions.begin([], "A", "t1", 1000.0)
        log = sessions.begin(log, "A", "t2", 2000.0, {"status": "hosting", "host": "A", "resumedOwnLock": True})
        self.assertEqual(len(log), 1)
        self.assertEqual((log[0]["token"], log[0]["resumes"], log[0]["end"]), ("t2", 1, None))

    def test_the_log_keeps_only_the_latest_sessions(self):
        log = []
        for i in range(sessions.MAX_SESSIONS + 5):
            log = sessions.begin(log, "A", f"t{i}", float(i))
            log = sessions.finish(log, f"t{i}", i + 0.5)
        self.assertEqual(len(log), sessions.MAX_SESSIONS)

    def test_the_wording_shows_who_when_and_for_how_long(self):
        start = time.mktime((2026, 10, 20, 18, 50, 0, 0, 0, -1))
        end = time.mktime((2026, 10, 20, 23, 24, 0, 0, 0, -1))
        entry = {"host": "hypernacho", "start": start, "end": end, "ended": sessions.NO_STOP}
        text = sessions.line(entry)
        self.assertIn("hypernacho", text)
        self.assertIn("6:50 PM to 11:24 PM (4 h 34 min)", text)
        self.assertIn("no stop recorded", text)
        self.assertIs(sessions.session_at([entry], start + 600), entry)
        self.assertIsNone(sessions.session_at([entry], end + 3 * HOUR))


class WorldLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cloud = os.path.join(self.tmp.name, "cloud")
        os.makedirs(os.path.join(self.tmp.name, "a"))
        self.clock = [1_800_000_000.0]
        self.now = lambda: self.clock[0]

    def server(self, name):
        path = os.path.join(self.tmp.name, name, "server")
        os.makedirs(os.path.join(path, "world"), exist_ok=True)
        with open(os.path.join(path, "world", "level.dat"), "w") as f:
            f.write(name)
        return path

    def test_hosting_and_stopping_is_logged_with_a_start_and_an_end(self):
        world = RcloneWorld(self.cloud, self.server("a"), "hypernacho", Rclone("rclone"), clock=self.now, sleep=lambda s: None)
        world.acquire(claim_wait=0)
        self.clock[0] += 4 * HOUR
        world.push()
        world.release()
        entries = world.sessions()
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]["host"], entries[0]["ended"]), ("hypernacho", "stopped"))
        self.assertAlmostEqual(entries[0]["end"] - entries[0]["start"], 4 * HOUR, delta=5)
        self.assertEqual(entries[0]["version"], 1)

    def test_a_crash_shows_up_when_the_next_host_starts(self):
        a = RcloneWorld(self.cloud, self.server("a"), "A", Rclone("rclone"), clock=self.now, sleep=lambda s: None)
        a.acquire(claim_wait=0)
        a.push()
        self.clock[0] += 3 * HOUR
        a.heartbeat()                                           # last seen alive here, then the PC dies
        self.clock[0] += 3 * HOUR
        b = RcloneWorld(self.cloud, self.server("b"), "B", Rclone("rclone"), clock=self.now, sleep=lambda s: None)
        b.acquire(takeover=True, claim_wait=0)
        entries = b.sessions()                                  # newest first
        self.assertEqual([e["host"] for e in entries], ["B", "A"])
        self.assertEqual(entries[1]["ended"], sessions.NO_STOP)
        self.assertAlmostEqual(entries[1]["end"] - entries[1]["start"], 3 * HOUR, delta=5)

    def test_folder_storage_keeps_the_log_too(self):
        shared = os.path.join(self.tmp.name, "folder")
        os.makedirs(shared)
        world = SharedWorld(shared, self.server("a"), "A", clock=self.now, sleep=lambda s: None)
        world.acquire(claim_wait=0)
        world.release()
        self.assertEqual([(e["host"], e["ended"]) for e in world.sessions()], [("A", "stopped")])


if __name__ == "__main__":
    unittest.main()
