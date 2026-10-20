"""Closing the controller while hosting: the world is uploaded and the lock given back, but a failed upload must NOT hand
the lock back (the next host would start from an old copy and the unuploaded progress would be thrown away)."""
import unittest
from types import SimpleNamespace

from app.main import cleanup


class FakeShared:
    def __init__(self, push_fails=False):
        self.token, self.push_fails, self.pushed, self.released = "tok", push_fails, 0, 0

    def push(self):
        self.pushed += 1
        if self.push_fails:
            raise RuntimeError("Can't reach the shared storage: transaction cap exceeded")

    def release(self):
        self.released += 1


class FakeRuntime:
    def __init__(self, running=False):
        self.running, self.stopped = running, 0

    def state(self, name):
        return "running" if self.running else "exited"

    def stop(self, name, grace):
        self.stopped += 1
        self.running = False


def app_with(shared, runtime, dirty=True):
    cfg = SimpleNamespace(mc_container="mc", stop_grace=1, backup_dir="", server_dir="")
    return SimpleNamespace(shared=shared, cfg=cfg, runtime=runtime, playit=None, hosting=SimpleNamespace(dirty=dirty))


class CleanupTests(unittest.TestCase):
    def test_a_normal_close_uploads_and_releases_the_lock(self):
        shared = FakeShared()
        cleanup(app_with(shared, FakeRuntime(running=True)))
        self.assertEqual((shared.pushed, shared.released), (1, 1))

    def test_a_failed_upload_keeps_the_lock_so_the_next_host_cannot_start_from_the_old_world(self):
        shared = FakeShared(push_fails=True)
        with self.assertLogs(level="ERROR") as logs:
            cleanup(app_with(shared, FakeRuntime(running=True)))
        self.assertEqual((shared.pushed, shared.released), (1, 0))
        self.assertIn("KEPT", "\n".join(logs.output))

    def test_nothing_to_upload_still_gives_the_lock_back(self):
        shared = FakeShared()
        cleanup(app_with(shared, FakeRuntime(running=False), dirty=False))
        self.assertEqual((shared.pushed, shared.released), (0, 1))


if __name__ == "__main__":
    unittest.main()
